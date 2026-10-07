import socket
import threading
import time
from types import SimpleNamespace
import pytest

from openpilot.selfdrive.addon.cluster.cluster_jpeg import PreparedFrame
from openpilot.selfdrive.addon.cluster.hdmi_display.network_display import ClusterNetworkDisplay
from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  ACK_OK, ACK_STREAM_SUPPORTED, FRAME_HEADER, FRAME_QUERY_STREAM, FRAME_STREAM,
  pack_ack, recv_exact, unpack_frame_header, unpack_frame_header_info,
)
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.cluster_receiver import receive_frames


def _config(bind_host, port):
  return SimpleNamespace(
    network_bind_host=bind_host,
    network_port=port,
    network_accept_timeout=0.1,
    network_ack_timeout=1.0,
    jpeg_quality=68,
    rotate_180=False,
    fps=20,
    status_interval_frames=200,
  )


def _free_port():
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  try:
    sock.bind(("127.0.0.1", 0))
    return sock.getsockname()[1]
  finally:
    sock.close()


def _start_client(address, ack_sequence_offset=0):
  received = {}
  completed = threading.Event()
  errors = []

  def receive():
    try:
      deadline = time.monotonic() + 1.0
      while True:
        try:
          sock = socket.create_connection(address, timeout=0.1)
          break
        except OSError:
          if time.monotonic() >= deadline:
            raise
          time.sleep(0.01)

      with sock:
        sequence, size = unpack_frame_header(recv_exact(sock, FRAME_HEADER.size))
        received["sequence"] = sequence
        received["payload"] = recv_exact(sock, size)
        sock.sendall(pack_ack(sequence + ack_sequence_offset))
    except Exception as e:
      errors.append(e)
    finally:
      completed.set()

  thread = threading.Thread(target=receive, daemon=True)
  thread.start()
  return received, completed, errors, thread


def test_network_display_sends_framed_jpeg_and_receives_ack():
  port = _free_port()
  display = ClusterNetworkDisplay(_config("127.0.0.1", port))
  received, completed, errors, thread = _start_client(("127.0.0.1", port))
  prepared = PreparedFrame(memoryview(b"jpeg-frame"), 1, 0.005)

  try:
    assert display.open()
    assert display.send_prepared(prepared)
    assert completed.wait(timeout=1.0)
    assert not errors
    assert received == {"sequence": 1, "payload": b"jpeg-frame"}
    assert display.connected
  finally:
    display.close()
    thread.join(timeout=1.0)


def test_network_display_disconnects_on_wrong_ack_sequence():
  port = _free_port()
  display = ClusterNetworkDisplay(_config("127.0.0.1", port))
  _received, completed, errors, thread = _start_client(("127.0.0.1", port), ack_sequence_offset=1)
  prepared = PreparedFrame(memoryview(b"jpeg-frame"), 1, 0.005)

  try:
    assert display.open()
    assert not display.send_prepared(prepared)
    assert completed.wait(timeout=1.0)
    assert not errors
    assert not display.connected
    assert display.listener is not None
  finally:
    display.close()
    thread.join(timeout=1.0)


def _prepared(payload=b"jpeg-frame"):
  return PreparedFrame(memoryview(payload), 1, 0.005)


def _wait_for(predicate, timeout=1.0):
  deadline = time.monotonic() + timeout
  while not predicate() and time.monotonic() < deadline:
    time.sleep(0.005)
  assert predicate()


def _stream_client(display, handle_stream):
  errors = []
  ready = threading.Event()

  def receive():
    try:
      with socket.create_connection((display.bind_host, display.port), timeout=1.0) as sock:
        sequence, size, flags = unpack_frame_header_info(recv_exact(sock, FRAME_HEADER.size))
        assert flags == FRAME_QUERY_STREAM
        recv_exact(sock, size)
        sock.sendall(pack_ack(sequence, ACK_OK, ACK_STREAM_SUPPORTED))
        ready.set()
        handle_stream(sock)
    except Exception as e:
      errors.append(e)

  assert display._ensure_listener()
  thread = threading.Thread(target=receive, daemon=True)
  thread.start()
  assert display.open()
  assert display.send_prepared(_prepared())
  assert ready.wait(timeout=1.0)
  assert display.streaming
  return thread, errors


def _read_stream(sock):
  sequence, size, flags = unpack_frame_header_info(recv_exact(sock, FRAME_HEADER.size))
  assert flags == FRAME_STREAM
  return sequence, recv_exact(sock, size)


def test_stream_window_bounds_in_flight_frames_and_releases_on_ack():
  display = ClusterNetworkDisplay(_config("127.0.0.1", _free_port()))
  window_received = threading.Event()
  release = threading.Event()
  sender_started, sender_finished = threading.Event(), threading.Event()
  sent = []

  def client(sock):
    sequences = [_read_stream(sock)[0] for _ in range(3)]
    window_received.set()
    assert release.wait(timeout=2.0)
    for sequence in sequences:
      sock.sendall(pack_ack(sequence, ACK_OK, ACK_STREAM_SUPPORTED))
    sequence, _payload = _read_stream(sock)
    sock.sendall(pack_ack(sequence, ACK_OK, ACK_STREAM_SUPPORTED))

  def fourth_send():
    sender_started.set()
    sent.append(display.send_prepared(_prepared(b"fourth")))
    sender_finished.set()

  thread, errors = _stream_client(display, client)
  sender = threading.Thread(target=fourth_send, daemon=True)
  try:
    for _ in range(3):
      assert display.send_prepared(_prepared())
    assert window_received.wait(timeout=1.0)
    assert display.get_confirmed_frame_count() == 1
    sender.start()
    assert sender_started.wait(timeout=1.0)
    assert not sender_finished.wait(timeout=0.05)
    assert len(display._pending_acks) == display.max_in_flight == 3
    release.set()
    assert sender_finished.wait(timeout=1.0)
    assert sent == [True]
    _wait_for(lambda: display.frame_count == 5)
    thread.join(timeout=1.0)
    assert not errors
  finally:
    release.set()
    display.close()
    if sender.ident is not None:
      sender.join(timeout=1.0)
    thread.join(timeout=1.0)


@pytest.mark.parametrize("failure", ["wrong_sequence", "missing_capability", "display_error", "timeout"])
def test_stream_ack_errors_disconnect_and_stop_the_reader(failure):
  config = _config("127.0.0.1", _free_port())
  config.network_ack_timeout = 0.1
  display = ClusterNetworkDisplay(config)
  release = threading.Event()

  def client(sock):
    sequence, _payload = _read_stream(sock)
    if failure != "timeout":
      sock.sendall(pack_ack(sequence + (failure == "wrong_sequence"), int(failure == "display_error"),
                           0 if failure == "missing_capability" else ACK_STREAM_SUPPORTED))
    release.wait(timeout=1.0)

  thread, errors = _stream_client(display, client)
  try:
    assert display.send_prepared(_prepared())
    _wait_for(lambda: not display.connected)
    assert display.frame_count == 1
    assert not display.send_prepared(_prepared())
    ack_thread = display._ack_thread
    display.close()
    assert not ack_thread.is_alive()
    assert not display._pending_acks
  finally:
    release.set()
    display.close()
    thread.join(timeout=1.0)
  assert not errors


def test_stream_connection_can_reconnect_to_a_legacy_receiver():
  display = ClusterNetworkDisplay(_config("127.0.0.1", _free_port()))

  def client(sock):
    _read_stream(sock)

  thread, errors = _stream_client(display, client)
  try:
    assert display.send_prepared(_prepared())
    _wait_for(lambda: not display.connected)
    thread.join(timeout=1.0)
    received, completed, reconnect_errors, reconnect_thread = _start_client((display.bind_host, display.port))
    assert display.open()
    assert not display.streaming and display._ack_thread is None
    assert display.send_prepared(_prepared(b"reconnected"))
    assert completed.wait(timeout=1.0)
    reconnect_thread.join(timeout=1.0)
    assert received["payload"] == b"reconnected"
    assert not reconnect_errors and not errors
    assert display.get_confirmed_frame_count() == 2
  finally:
    display.close()
    thread.join(timeout=1.0)


def test_sender_and_receiver_keep_receiving_when_display_is_blocked():
  sender = ClusterNetworkDisplay(_config("127.0.0.1", _free_port()))
  display_started, release_display = threading.Event(), threading.Event()
  displayed, results, errors = [], [], []

  class BlockingDisplay:
    owner = None

    def pump_events(self):
      assert threading.get_ident() == self.owner
      return True

    def send_jpeg(self, jpeg):
      assert threading.get_ident() == self.owner
      displayed.append(jpeg)
      if len(displayed) == 2:
        display_started.set()
        assert release_display.wait(timeout=2.0)
      return True

  display = BlockingDisplay()

  def receiver():
    try:
      display.owner = threading.get_ident()
      with socket.create_connection((sender.bind_host, sender.port), timeout=1.0) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        results.append(receive_frames(sock, display, max_frames=21))
    except BaseException as e:
      errors.append(e)

  assert sender._ensure_listener()
  thread = threading.Thread(target=receiver, daemon=True)
  thread.start()
  try:
    assert sender.open()
    assert sender.send_prepared(_prepared(b"1"))
    assert sender.streaming
    assert sender.send_prepared(_prepared(b"2"))
    assert display_started.wait(timeout=1.0)
    for sequence in range(3, 22):
      assert sender.send_prepared(_prepared(str(sequence).encode()))
    _wait_for(lambda: sender.get_confirmed_frame_count() == 21)
    assert displayed == [b"1", b"2"]
    release_display.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive() and not errors
    assert results == [21]
    assert displayed[-1] == b"21"
    assert len(displayed) < 21
  finally:
    release_display.set()
    sender.close()
    thread.join(timeout=1.0)
