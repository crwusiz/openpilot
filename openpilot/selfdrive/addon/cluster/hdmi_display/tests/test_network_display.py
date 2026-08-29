import socket
import threading
import time
from types import SimpleNamespace

from openpilot.selfdrive.addon.cluster.cluster_jpeg import PreparedFrame
from openpilot.selfdrive.addon.cluster.hdmi_display.network_display import ClusterNetworkDisplay
from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  FRAME_HEADER, pack_ack, recv_exact, unpack_frame_header,
)


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
