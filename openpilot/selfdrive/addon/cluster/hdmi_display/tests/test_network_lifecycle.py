import socket
import threading
import time
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.cluster_jpeg import PreparedFrame
from openpilot.selfdrive.addon.cluster.hdmi_display import network_display
from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  ACK_OK, ACK_SCREEN_OFF, ACK_STREAM_SUPPORTED, FRAME_HEADER, FRAME_QUERY_STREAM, FRAME_STREAM,
  pack_ack, recv_exact, unpack_frame_header_info,
)


def _prepared(payload):
  return PreparedFrame(memoryview(payload), 1, 0.001)


def _wait_for(predicate):
  deadline = time.monotonic() + 1.0
  while not predicate() and time.monotonic() < deadline:
    time.sleep(0.005)
  assert predicate()


class Peer:
  """A real TCP receiver with an optionally withheld stream ACK."""

  def __init__(self, display, streaming=True, hold_stream=False, stream_flags=ACK_STREAM_SUPPORTED):
    self.streaming = streaming
    self.stream_flags = stream_flags
    self.received_stream = threading.Event()
    self.allow_ack = threading.Event()
    self.stop = threading.Event()
    self.errors = []
    if not hold_stream:
      self.allow_ack.set()
    assert display._ensure_listener()
    self.sock = socket.create_connection(display.listener.getsockname(), timeout=3.0)
    self.thread = threading.Thread(target=self._receive, daemon=True)
    self.thread.start()
    assert display.open()
    assert display.send_prepared(_prepared(b"query"))
    assert display.streaming == streaming

  def _receive(self):
    try:
      sequence, size, flags = unpack_frame_header_info(recv_exact(self.sock, FRAME_HEADER.size))
      assert flags == FRAME_QUERY_STREAM
      recv_exact(self.sock, size)
      self.sock.sendall(pack_ack(sequence, ACK_OK, ACK_STREAM_SUPPORTED if self.streaming else 0))
      if self.streaming:
        sequence, size, flags = unpack_frame_header_info(recv_exact(self.sock, FRAME_HEADER.size))
        assert flags == FRAME_STREAM
        recv_exact(self.sock, size)
        self.received_stream.set()
        assert self.allow_ack.wait(timeout=4.0)
        self.sock.sendall(pack_ack(sequence, ACK_OK, self.stream_flags))
      self.stop.wait(timeout=4.0)
    except Exception as e:
      if not self.stop.is_set():
        self.errors.append(e)

  def close(self):
    self.stop.set()
    self.allow_ack.set()
    try:
      self.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    self.sock.close()
    self.thread.join(timeout=1.0)


@pytest.fixture
def display(monkeypatch):
  monkeypatch.setattr(network_display, "write_network_status", lambda _address: None)
  monkeypatch.setattr(network_display, "flog", lambda _message: None)
  config = SimpleNamespace(network_bind_host="127.0.0.1", network_port=0,
                           network_accept_timeout=0.1, network_ack_timeout=3.0,
                           jpeg_quality=68, fps=60, status_interval_frames=600)
  display = network_display.ClusterNetworkDisplay(config)
  yield display
  display.close()


@pytest.mark.parametrize("new_streaming", [False, True])
def test_stalled_ack_record_cannot_follow_a_reconnected_client(display, monkeypatch, new_streaming):
  old_peer = Peer(display)
  old_reader = display._ack_thread
  blocked, release = threading.Event(), threading.Event()
  original_record = display._record_ack
  peers = [old_peer]

  def record(*args):
    if threading.current_thread() is old_reader:
      blocked.set()
      assert release.wait(timeout=4.0)
    original_record(*args)

  monkeypatch.setattr(display, "_record_ack", record)
  try:
    assert display.send_prepared(_prepared(b"old-frame"))
    assert blocked.wait(timeout=1.0)
    display._disconnect_client()  # The real one-second join cannot stop stalled I/O.
    assert old_reader.is_alive()
    new_peer = Peer(display, streaming=new_streaming, hold_stream=True)
    peers.append(new_peer)
    new_reader = display._ack_thread
    if new_streaming:
      assert display.send_prepared(_prepared(b"new-frame"))
      assert new_peer.received_stream.wait(timeout=1.0)
    release.set()
    old_reader.join(timeout=1.0)
    assert not old_reader.is_alive()
    assert display.connected and display._ack_error is None
    assert display._ack_thread is new_reader
    if new_streaming:
      with display._ack_condition:
        assert len(display._pending_acks) == 1
        assert display._pending_acks[0][0] == display.sequence
        assert display._pending_bytes == len(b"new-frame")
      new_peer.allow_ack.set()
      _wait_for(lambda: display.get_confirmed_frame_count() == 4)
    else:
      assert new_reader is None
    assert not any(peer.errors for peer in peers)
  finally:
    release.set()
    for peer in peers:
      peer.close()
    old_reader.join(timeout=1.0)


@pytest.mark.parametrize("late_read_error", [False, True])
def test_stale_ack_or_read_error_cannot_mutate_new_connection(display, monkeypatch, late_read_error):
  old_peer = Peer(display, stream_flags=ACK_STREAM_SUPPORTED | ACK_SCREEN_OFF)
  old_sock, old_reader = display.sock, display._ack_thread
  blocked, release = threading.Event(), threading.Event()
  original_recv = network_display.recv_exact
  peers = [old_peer]

  def receive(sock, size):
    data = original_recv(sock, size)
    if sock is old_sock:
      blocked.set()
      assert release.wait(timeout=4.0)
      if late_read_error:
        raise ConnectionError("late failure from the previous client")
    return data

  monkeypatch.setattr(network_display, "recv_exact", receive)
  try:
    assert display.send_prepared(_prepared(b"old-frame"))
    assert blocked.wait(timeout=1.0)
    display._disconnect_client()
    assert old_reader.is_alive()
    new_peer = Peer(display, hold_stream=True)
    peers.append(new_peer)
    new_reader = display._ack_thread
    assert display.send_prepared(_prepared(b"new-frame"))
    assert new_peer.received_stream.wait(timeout=1.0)
    release.set()
    old_reader.join(timeout=1.0)
    assert not old_reader.is_alive()
    assert display.connected and display._ack_error is None
    assert not display.screen_off
    assert display._ack_thread is new_reader
    with display._ack_condition:
      assert len(display._pending_acks) == 1
      assert display._pending_acks[0][0] == display.sequence
      assert display._pending_bytes == len(b"new-frame")
    new_peer.allow_ack.set()
    _wait_for(lambda: display.get_confirmed_frame_count() == 3)
    assert not any(peer.errors for peer in peers)
  finally:
    release.set()
    for peer in peers:
      peer.close()
    old_reader.join(timeout=1.0)
