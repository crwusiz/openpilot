import ipaddress
import socket
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  ACK_DISPLAY_ERROR, ACK_OK, ACK_PACKET, ACK_SCREEN_OFF, ACK_STREAM_SUPPORTED, FRAME_QUERY_STREAM, FRAME_STREAM,
  pack_ack as pack_c4_ack, pack_frame_header, recv_exact, unpack_ack, unpack_ack_info,
)
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import cluster_protocol, cluster_receiver, frame_stream, hdmi_display
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.cluster_protocol import (
  ACK_SCREEN_OFF as PI_ACK_SCREEN_OFF, ReceiveStats, pack_ack as pack_orange_pi_ack, recv_exact as pi_recv_exact, unpack_frame_header,
)
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.hdmi_display import HdmiDisplay


class FakeDisplay:
  def __init__(self, succeeds=True):
    self.succeeds = succeeds
    self.frames = []
    self.poll_count = 0

  def pump_events(self):
    self.poll_count += 1
    return True

  def send_jpeg(self, jpeg):
    self.frames.append(jpeg)
    return self.succeeds


def _exchange_frame(display, flags=0, ack_details=False):
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(1.0)
  pi_sock.settimeout(1.0)
  errors = []

  def receive():
    try:
      cluster_receiver.receive_frames(pi_sock, display, 1)
    except RuntimeError as e:
      errors.append(str(e))

  receiver = threading.Thread(
    target=receive,
    daemon=True,
  )
  receiver.start()
  try:
    payload = b"jpeg-frame"
    c4_sock.sendall(pack_frame_header(7, len(payload), flags) + payload)
    ack_data = recv_exact(c4_sock, ACK_PACKET.size)
    ack = unpack_ack_info(ack_data) if ack_details else unpack_ack(ack_data)
    receiver.join(timeout=1.0)
    assert not receiver.is_alive()
    assert errors == ([] if display.succeeds else ["Unable to display cluster frame"])
    return ack
  finally:
    c4_sock.close()
    pi_sock.close()


def test_orange_pi_protocol_matches_c4_protocol():
  assert unpack_frame_header(pack_frame_header(123, 456)) == (123, 456)
  assert pack_orange_pi_ack(123, ACK_OK) == pack_c4_ack(123, ACK_OK)
  assert ACK_SCREEN_OFF == PI_ACK_SCREEN_OFF == 2


def test_capability_flags_preserve_the_legacy_packet_layout():
  header = pack_frame_header(123, 456, FRAME_QUERY_STREAM)
  assert len(header) == 16
  assert struct.Struct("!4sB3xII").unpack(header) == (b"OPCF", 1, 123, 456)
  ack = pack_orange_pi_ack(123, ACK_OK, ACK_STREAM_SUPPORTED)
  assert len(ack) == 16
  assert struct.Struct("!4sB3xIB3x").unpack(ack) == (b"OPCA", 1, 123, ACK_OK)
  old_ack = struct.Struct("!4sB3xIB3x").pack(b"OPCA", 1, 123, ACK_OK)
  assert unpack_ack_info(old_ack) == (123, ACK_OK, 0)


class BlockingDisplay(FakeDisplay):
  def __init__(self):
    super().__init__()
    self.started = threading.Event()
    self.release = threading.Event()
    self.owner = None
    self.quit = False

  def pump_events(self):
    assert threading.get_ident() == self.owner, "SDL events must stay on the display thread"
    return super().pump_events() and not self.quit

  def send_jpeg(self, jpeg):
    assert threading.get_ident() == self.owner, "SDL presentation must stay on the display thread"
    self.frames.append(jpeg)
    self.started.set()
    assert self.release.wait(timeout=2.0)
    return self.succeeds


def _start_stream_receiver(display, max_frames=None, frame_timeout=1.0):
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(1.0)
  pi_sock.settimeout(1.0)
  result, errors = [], []

  def receive():
    display.owner = threading.get_ident()
    try:
      result.append(cluster_receiver.receive_frames(pi_sock, display, max_frames, frame_timeout))
    except BaseException as e:
      errors.append(e)
    finally:
      pi_sock.close()

  thread = threading.Thread(target=receive, daemon=True)
  thread.start()
  return c4_sock, thread, result, errors


def _send_stream(sock, sequence, payload, flags=ACK_STREAM_SUPPORTED):
  sock.sendall(pack_frame_header(sequence, len(payload), FRAME_STREAM) + payload)
  assert unpack_ack_info(recv_exact(sock, ACK_PACKET.size)) == (sequence, ACK_OK, flags)


def test_stream_ack_and_next_receive_do_not_wait_for_blocked_display():
  display = BlockingDisplay()
  sock, thread, result, errors = _start_stream_receiver(display, max_frames=2)
  try:
    _send_stream(sock, 1, b"first")
    assert display.started.wait(timeout=1.0)
    # SDL remains blocked, but a second complete frame is already acknowledged.
    _send_stream(sock, 2, b"second")
    assert display.frames == [b"first"]
    display.release.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not errors
    assert result == [2]
    assert display.frames == [b"first", b"second"]
  finally:
    display.release.set()
    sock.close()
    thread.join(timeout=1.0)


def test_slow_display_skips_pending_frames_and_presents_the_latest():
  display = BlockingDisplay()
  sock, thread, result, errors = _start_stream_receiver(display, max_frames=3)
  try:
    _send_stream(sock, 1, b"first")
    assert display.started.wait(timeout=1.0)
    _send_stream(sock, 2, b"stale")
    _send_stream(sock, 3, b"latest")
    display.release.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not errors
    assert result == [3]
    assert display.frames == [b"first", b"latest"]
  finally:
    display.release.set()
    sock.close()
    thread.join(timeout=1.0)


def test_screen_off_and_wake_are_reported_on_receipt_ack_without_reconnect():
  display = BlockingDisplay()
  display.screen_off = False
  sock, thread, result, errors = _start_stream_receiver(display, max_frames=3)
  try:
    _send_stream(sock, 1, b"first")
    assert display.started.wait(timeout=1.0)
    display.screen_off = True
    # Changing local touch state emits no unsolicited packet.
    sock.settimeout(0.05)
    with pytest.raises(socket.timeout):
      sock.recv(ACK_PACKET.size)
    sock.settimeout(1.0)
    # Screen status piggybacks on normal ACKs while SDL remains blocked.
    _send_stream(sock, 2, b"off", ACK_STREAM_SUPPORTED | ACK_SCREEN_OFF)
    display.screen_off = False
    _send_stream(sock, 3, b"awake")
    display.release.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not errors
    assert result == [3]
    assert display.frames == [b"first", b"awake"]
  finally:
    display.release.set()
    sock.close()
    thread.join(timeout=1.0)


def test_stream_screen_state_callback_reads_property_without_calling_sdl():
  class ScreenOffDisplay(BlockingDisplay):
    def __init__(self):
      super().__init__()
      self.property_readers = []

    @property
    def screen_off(self):
      self.property_readers.append(threading.current_thread().name)
      return True

  display = ScreenOffDisplay()
  display.release.set()
  sock, thread, result, errors = _start_stream_receiver(display, max_frames=1)
  try:
    _send_stream(sock, 1, b"off", ACK_STREAM_SUPPORTED | ACK_SCREEN_OFF)
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not errors
    assert result == [1]
    assert display.property_readers == ["cluster-jpeg-receiver"]
  finally:
    sock.close()
    thread.join(timeout=1.0)


def test_stream_partial_frame_still_times_out_and_closes_connection():
  display = BlockingDisplay()
  display.release.set()
  sock, thread, _result, errors = _start_stream_receiver(display, frame_timeout=0.1)
  try:
    _send_stream(sock, 1, b"first")
    assert display.started.wait(timeout=1.0)
    sock.sendall(pack_frame_header(2, 10, FRAME_STREAM) + b"partial")
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], TimeoutError)
    assert display.frames == [b"first"]
    assert sock.recv(1) == b""
  finally:
    sock.close()
    thread.join(timeout=1.0)


def test_stream_display_error_closes_transport_after_receive_ack():
  display = BlockingDisplay()
  display.succeeds = False
  display.release.set()
  sock, thread, _result, errors = _start_stream_receiver(display)
  try:
    _send_stream(sock, 1, b"invalid-jpeg")
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert "Unable to display cluster frame" in str(errors[0])
    assert sock.recv(1) == b""
  finally:
    sock.close()
    thread.join(timeout=1.0)


def test_stream_quit_interrupts_idle_receive_without_leaving_io_thread():
  display = BlockingDisplay()
  sock, thread, _result, errors = _start_stream_receiver(display)
  try:
    _send_stream(sock, 1, b"first")
    assert display.started.wait(timeout=1.0)
    display.quit = True
    display.release.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], KeyboardInterrupt)
    assert not any(t.name == "cluster-jpeg-receiver" and t.is_alive() for t in threading.enumerate())
  finally:
    display.release.set()
    sock.close()
    thread.join(timeout=1.0)


def test_slow_ack_keeps_display_polling_and_does_not_publish_unacknowledged_jpeg():
  class SlowAckSocket:
    def __init__(self):
      self.started = threading.Event()
      self.release = threading.Event()

    def sendall(self, _packet):
      self.started.set()
      assert self.release.wait(timeout=2.0)

    def shutdown(self, _how):
      self.release.set()

  sock = SlowAckSocket()
  frame = frame_stream.ReceivedFrame(1, b"jpeg", 0.0, 0.0, 0.0)
  receiver = frame_stream.LatestFrameReceiver(sock, frame, 1.0, max_frames=1)
  receiver.thread.start()
  result = []
  returned = threading.Event()

  def take_pending():
    result.append(receiver.take_frame())
    returned.set()

  poller = threading.Thread(target=take_pending, daemon=True)
  try:
    assert sock.started.wait(timeout=1.0)
    poller.start()
    # SDL can return to event polling while the transport ACK is congested.
    assert returned.wait(timeout=0.5)
    assert result == [None]
    assert receiver.received_frames == 0
    receiver.close()
    assert not receiver.thread.is_alive()
    assert receiver.pending is None
    assert receiver.error is None
  finally:
    receiver.close()
    if poller.ident is not None:
      poller.join(timeout=1.0)


def test_receive_ack_failure_never_presents_that_jpeg():
  class FailedAckSocket:
    def sendall(self, _packet):
      raise ConnectionError("ACK connection closed")

  display = FakeDisplay()
  frame = frame_stream.ReceivedFrame(1, b"jpeg", 0.0, 0.0, 0.0)
  with pytest.raises(ConnectionError, match="ACK connection closed"):
    frame_stream.receive_stream_frames(FailedAckSocket(), display, frame, 1.0, max_frames=1)
  assert not display.frames


def test_stream_intentional_blanking_counts_receipts_without_display_fps(monkeypatch):
  receiver_instances = []
  original_receiver = frame_stream.LatestFrameReceiver

  def capture_receiver(*args, **kwargs):
    receiver = original_receiver(*args, **kwargs)
    receiver_instances.append(receiver)
    return receiver

  monkeypatch.setattr(frame_stream, "LatestFrameReceiver", capture_receiver)
  display = FakeDisplay()
  display.last_frame_presented = False
  sock, thread, result, errors = _start_stream_receiver(display, max_frames=1)
  try:
    _send_stream(sock, 1, b"off-screen-jpeg")
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not errors
    assert result == [1]
    assert receiver_instances[0]._perf_received == 1
    assert receiver_instances[0]._perf_displayed == 0
    assert receiver_instances[0]._perf_rx_stats_frames == 1
    assert receiver_instances[0]._perf_read_calls >= 1
  finally:
    sock.close()
    thread.join(timeout=1.0)


def test_stream_performance_log_exposes_queue_age_and_display_stalls(monkeypatch, caplog):
  now = [0.0]
  monkeypatch.setattr(frame_stream.time, "monotonic", lambda: now[0])
  first = frame_stream.ReceivedFrame(1, b"jpeg", 0.0, 0.005, 0.02)
  receiver = frame_stream.LatestFrameReceiver(None, first, 1.0)
  receiver.record_display(first, 0.03, 0.05, {"decode": 0.01, "blit": 0.005, "flip": 0.001, "convert": 0.001, "upload": 0.003, "draw": 0.001})
  second = frame_stream.ReceivedFrame(2, b"jpeg", 0.07, 0.08, 0.10)
  receiver.record_display(second, 0.15, 0.30, {"decode": 0.02, "blit": 0.007, "flip": 0.002, "convert": 0.001, "upload": 0.004, "draw": 0.002})
  now[0] = 10.0
  with caplog.at_level("INFO", logger="cluster_receiver"):
    receiver._log_perf()
  assert "display_fps=0.20" in caplog.text
  assert "queue_wait_avg=30.0ms" in caplog.text
  assert "queue_wait_max=50.0ms" in caplog.text
  assert "display_max=150.0ms" in caplog.text
  assert "display_gap_max=250.0ms" in caplog.text
  assert "decode_max=20.0ms" in caplog.text
  assert "blit_max=7.0ms" in caplog.text
  assert "flip_max=2.0ms" in caplog.text
  assert "convert_avg=1.0ms" in caplog.text
  assert "upload_avg=3.5ms" in caplog.text
  assert "draw_avg=1.5ms" in caplog.text
  assert "convert_max=1.0ms" in caplog.text
  assert "upload_max=4.0ms" in caplog.text
  assert "draw_max=2.0ms" in caplog.text


def test_stream_receive_diagnostics_are_reported_and_reset_per_window(monkeypatch, caplog):
  now = [0.03]
  monkeypatch.setattr(frame_stream, "time", SimpleNamespace(monotonic=lambda: now[0]))
  stats = ReceiveStats(wait_time=0.017, wait_max=0.012, read_calls=3, read_max=0.002, select_timeouts=1)
  first = frame_stream.ReceivedFrame(1, b"jpeg", 0.0, 0.005, 0.025, stats)
  acks = []
  receiver = frame_stream.LatestFrameReceiver(SimpleNamespace(sendall=acks.append), first, 1.0, max_frames=1)
  receiver._receive_loop()
  assert len(acks) == 1
  assert receiver.take_frame() == first
  now[0] = 10.03
  with caplog.at_level("INFO", logger="cluster_receiver"):
    receiver._log_perf()
  assert "receive_avg=20.0ms" in caplog.text
  assert "payload_wait_avg=17.0ms" in caplog.text
  assert "payload_wait_max=12.0ms" in caplog.text
  assert "payload_read_calls_avg=3.0" in caplog.text
  assert "payload_read_max=2.0ms" in caplog.text
  assert "payload_select_timeouts=1" in caplog.text
  caplog.clear()
  now[0] = 20.03
  with caplog.at_level("INFO", logger="cluster_receiver"):
    receiver._log_perf()
  assert "payload_wait_avg=0.0ms" in caplog.text
  assert "payload_read_calls_avg=0.0" in caplog.text
  assert "payload_select_timeouts=0" in caplog.text


def test_receiver_acknowledges_successful_hdmi_frame():
  display = FakeDisplay()
  assert _exchange_frame(display) == (7, ACK_OK)
  assert display.frames == [b"jpeg-frame"]


def test_legacy_performance_log_does_not_count_blanking_as_display(monkeypatch, caplog):
  start = time.monotonic()
  timestamps = iter(start + elapsed for elapsed in (0.0, 0.0, 0.001, 0.002, 0.003, 10.0))
  monkeypatch.setattr(cluster_receiver, "time", SimpleNamespace(monotonic=lambda: next(timestamps)))
  display = FakeDisplay()
  display.last_frame_presented = False
  with caplog.at_level("INFO", logger="cluster_receiver"):
    assert _exchange_frame(display) == (7, ACK_OK)
  assert "fps=0.10 | display_fps=0.00" in caplog.text
  assert "display_avg=0.0ms" in caplog.text
  assert "mode=legacy" in caplog.text


def test_receiver_advertises_streaming_only_when_queried():
  assert _exchange_frame(FakeDisplay(), flags=FRAME_QUERY_STREAM, ack_details=True) == (7, ACK_OK, ACK_STREAM_SUPPORTED)
  assert _exchange_frame(FakeDisplay(), ack_details=True) == (7, ACK_OK, 0)


def test_query_ack_reports_screen_off_while_legacy_ack_flags_stay_empty():
  display = FakeDisplay()
  display.screen_off = True
  assert _exchange_frame(display, flags=FRAME_QUERY_STREAM, ack_details=True) == (
    7, ACK_OK, ACK_STREAM_SUPPORTED | ACK_SCREEN_OFF,
  )
  assert _exchange_frame(display, ack_details=True) == (7, ACK_OK, 0)


def test_receiver_reports_hdmi_failure():
  display = FakeDisplay(succeeds=False)
  assert _exchange_frame(display) == (7, ACK_DISPLAY_ERROR)


def test_interface_network_uses_wifi_prefix(monkeypatch):
  monkeypatch.setattr(cluster_receiver, "_run_ip_json", lambda *_args: [{
    "ifname": "wlan0",
    "addr_info": [{"family": "inet", "scope": "global", "local": "192.168.43.27", "prefixlen": 24}],
  }])

  local_ip, network = cluster_receiver.get_interface_network("wlan0")
  assert local_ip == ipaddress.ip_address("192.168.43.27")
  assert network == ipaddress.ip_network("192.168.43.0/24")


def test_receiver_defaults_match_purchased_panel():
  args = cluster_receiver.parse_args([])
  assert (args.width, args.height) == HdmiDisplay().size == (1920, 480)
  assert args.rotation == 0
  assert args.touch_rotation is None
  assert not args.log_touch
  assert args.brightness == 80
  assert args.renderer == "auto"


def test_receiver_accepts_native_portrait_mode_and_touch_override():
  args = cluster_receiver.parse_args([
    "--width", "480", "--height", "1920", "--rotation", "90", "--touch-rotation", "0", "--log-touch",
  ])
  assert (args.width, args.height) == (480, 1920)
  assert args.rotation == 90
  assert args.touch_rotation == 0
  assert args.log_touch


@pytest.mark.parametrize("brightness", [10, 100])
def test_receiver_accepts_brightness_bounds(brightness):
  assert cluster_receiver.parse_args(["--brightness", str(brightness)]).brightness == brightness


@pytest.mark.parametrize("argv", [
  ["--port", "65536"], ["--port", "0"], ["--width", "0"], ["--height", "-1"],
  ["--scan-workers", "0"], ["--scan-timeout", "nan"], ["--reconnect-delay", "-1"],
  ["--frame-timeout", "inf"], ["--display-index", "-1"], ["--host", "invalid"],
  ["--rotation", "45"], ["--rotation", "-90"], ["--touch-rotation", "360"],
  ["--brightness", "9"], ["--brightness", "101"], ["--brightness", "nan"], ["--renderer", "invalid"],
])
def test_receiver_rejects_invalid_options(argv):
  with pytest.raises(SystemExit):
    cluster_receiver.parse_args(argv)


def test_partial_frame_times_out_while_pumping_events():
  c4_sock, pi_sock = socket.socketpair()
  display = FakeDisplay()
  try:
    c4_sock.sendall(pack_frame_header(1, 10) + b"partial")
    with pytest.raises(TimeoutError):
      cluster_receiver.receive_frames(pi_sock, display, 1, frame_timeout=0.1)
    assert display.frames == []
    assert display.poll_count >= 2
  finally:
    c4_sock.close()
    pi_sock.close()


def test_fragmented_packet_keeps_received_bytes():
  c4_sock, pi_sock = socket.socketpair()
  chunks = iter([b"ab", b"cd", b"ef"])
  stats = ReceiveStats()
  try:
    data = pi_recv_exact(pi_sock, 6, deadline=time.monotonic() + 1.0,
                         poll_events=lambda: c4_sock.sendall(next(chunks)), stats=stats)
    assert data == b"abcdef"
    assert stats.read_calls == 3
    assert stats.select_timeouts == 0
  finally:
    c4_sock.close()
    pi_sock.close()


def test_receive_diagnostics_distinguish_readiness_waits_from_partial_reads(monkeypatch):
  now = [0.0]
  waits = iter([(0.05, False), (0.003, True), (0.004, True)])
  chunks = iter([(b"ab", 0.002), (b"cdef", 0.001)])
  requested_sizes = []

  def select_readable(readers, _writers, _errors, _timeout):
    duration, ready = next(waits)
    now[0] += duration
    return (readers if ready else []), [], []

  class FragmentedSocket:
    def recv_into(self, view):
      requested_sizes.append(len(view))
      chunk, elapsed = next(chunks)
      now[0] += elapsed
      view[:len(chunk)] = chunk
      return len(chunk)

  monkeypatch.setattr(cluster_protocol, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(cluster_protocol.select, "select", select_readable)
  stats = ReceiveStats()
  assert pi_recv_exact(FragmentedSocket(), 6, deadline=1.0, stats=stats) == b"abcdef"
  assert requested_sizes == [6, 4]
  assert stats.read_calls == 2
  assert stats.wait_time == pytest.approx(0.057)
  assert stats.wait_max == pytest.approx(0.05)
  assert stats.read_max == pytest.approx(0.002)
  assert stats.select_timeouts == 1


def test_receive_without_diagnostics_does_not_add_timing_calls(monkeypatch):
  class CompleteSocket:
    def recv_into(self, view):
      view[:] = b"jpeg"
      return len(view)

  monkeypatch.setattr(cluster_protocol, "time", SimpleNamespace(monotonic=lambda: pytest.fail("unnecessary timing call")))
  assert pi_recv_exact(CompleteSocket(), 4) == b"jpeg"


def test_runtime_versions_log_on_the_calling_thread_and_allow_missing_apis(caplog):
  caller = threading.get_ident()

  def sdl_version():
    assert threading.get_ident() == caller
    return (2, 28, 4)

  pygame_stub = SimpleNamespace(version=SimpleNamespace(ver="2.6.1"), get_sdl_version=sdl_version,
                                image=SimpleNamespace(get_sdl_image_version=lambda: (2, 8, 2)))
  with caplog.at_level("INFO", logger="cluster_receiver"):
    cluster_receiver.log_runtime_versions(pygame_stub)
    cluster_receiver.log_runtime_versions(None)
  assert "pygame=2.6.1 | SDL=2.28.4 | SDL_image=2.8.2" in caplog.text
  assert "pygame=unknown | SDL=unknown | SDL_image=unknown" in caplog.text


def test_socket_buffer_diagnostics_report_effective_sizes_without_changing_them(caplog):
  sock = socket.socket()
  try:
    before = [sock.getsockopt(socket.SOL_SOCKET, option) for option in (socket.SO_RCVBUF, socket.SO_SNDBUF)]
    with caplog.at_level("INFO", logger="cluster_receiver"):
      cluster_receiver.log_socket_buffers(sock)
      cluster_receiver.log_socket_buffers(None)
    after = [sock.getsockopt(socket.SOL_SOCKET, option) for option in (socket.SO_RCVBUF, socket.SO_SNDBUF)]
    assert after == before
    assert f"recv_buffer={before[0]}B | send_buffer={before[1]}B" in caplog.text
    assert "recv_buffer=unknownB | send_buffer=unknownB" in caplog.text
  finally:
    sock.close()


def test_quit_interrupts_idle_receive(monkeypatch):
  c4_sock, pi_sock = socket.socketpair()
  display = FakeDisplay()
  monkeypatch.setattr(display, "pump_events", lambda: False)
  try:
    with pytest.raises(KeyboardInterrupt):
      cluster_receiver.receive_frames(pi_sock, display)
  finally:
    c4_sock.close()
    pi_sock.close()


def test_discovery_quit_closes_probe_sockets(monkeypatch):
  sockets = []
  monkeypatch.setattr(cluster_receiver, "get_interface_network", lambda _interface: (
    ipaddress.ip_address("192.168.1.1"), ipaddress.ip_network("192.168.1.0/29"),
  ))

  def probe(*_args):
    sock = socket.socket()
    sockets.append(sock)
    return sock

  def quit_display():
    raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  with pytest.raises(KeyboardInterrupt):
    cluster_receiver.discover_c4("wlan0", 9200, 0.1, 2, quit_display)
  assert sockets
  assert all(sock.fileno() == -1 for sock in sockets)


def test_main_shows_waiting_at_startup_and_disconnect_before_retry(monkeypatch, caplog):
  events = []
  display_options = {}
  display = FakeDisplay()

  def create_display(**kwargs):
    display_options.update(kwargs)
    return display

  monkeypatch.setattr(hdmi_display, "HdmiDisplay", create_display)
  monkeypatch.setattr(display, "open", lambda: True, raising=False)
  monkeypatch.setattr(display, "show_waiting", lambda: events.append("waiting") or True, raising=False)
  monkeypatch.setattr(display, "close", lambda: events.append("close"), raising=False)
  args = cluster_receiver.parse_args([
    "--host", "127.0.0.1", "--width", "480", "--height", "1920", "--rotation", "90",
    "--touch-rotation", "0", "--log-touch", "--interface", "wlan1",
    "--brightness", "65", "--renderer", "surface",
  ])
  monkeypatch.setattr(cluster_receiver, "parse_args", lambda: args)
  monkeypatch.setattr(cluster_receiver.signal, "signal", lambda *_args: None)
  monkeypatch.setattr(cluster_receiver, "discover_c4", lambda *_args: pytest.fail("should bypass scanning"))

  class Connection:
    def settimeout(self, _timeout):
      pass

    def close(self):
      events.append("disconnect")

  def probe(address, *_args):
    assert address == "127.0.0.1"
    events.append("probe")
    return Connection()

  def fail_receive(*_args, **_kwargs):
    raise ConnectionError("lost Wi-Fi")

  def retry(*_args):
    events.append("retry")
    raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  monkeypatch.setattr(cluster_receiver, "receive_frames", fail_receive)
  monkeypatch.setattr(cluster_receiver, "wait_for_reconnect", retry)
  with caplog.at_level("INFO"):
    cluster_receiver.main()
  assert events == ["waiting", "probe", "disconnect", "waiting", "retry", "close"]
  assert "Cluster receiver ready" in caplog.text
  assert display_options["width"] == 480
  assert display_options["height"] == 1920
  assert display_options["rotation"] == 90
  assert display_options["touch_rotation"] == 0
  assert display_options["log_touch"]
  assert display_options["network_interface"] == "wlan1"
  assert display_options["brightness"] == 65
  assert display_options["renderer_mode"] == "surface"


@pytest.mark.parametrize("wifi_available", [False, True])
def test_main_keeps_waiting_when_wifi_or_c4_is_unavailable(monkeypatch, wifi_available):
  events = []
  display = FakeDisplay()
  monkeypatch.setattr(hdmi_display, "HdmiDisplay", lambda **kwargs: display)
  monkeypatch.setattr(display, "open", lambda: True, raising=False)
  monkeypatch.setattr(display, "show_waiting", lambda: events.append("waiting") or True, raising=False)
  monkeypatch.setattr(display, "close", lambda: events.append("close"), raising=False)
  args = cluster_receiver.parse_args([])
  monkeypatch.setattr(cluster_receiver, "parse_args", lambda: args)
  monkeypatch.setattr(cluster_receiver.signal, "signal", lambda *_args: None)

  def discover(*_args):
    events.append("discover")
    if not wifi_available:
      raise RuntimeError("No IPv4 address assigned to wlan0")
    return None

  def retry(*_args):
    events.append("retry")
    raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "discover_c4", discover)
  monkeypatch.setattr(cluster_receiver, "wait_for_reconnect", retry)
  cluster_receiver.main()
  assert events == ["waiting", "discover", "waiting", "retry", "close"]


def test_main_does_not_report_ready_when_waiting_screen_fails(monkeypatch, caplog):
  events = []
  display = FakeDisplay()
  monkeypatch.setattr(hdmi_display, "HdmiDisplay", lambda **kwargs: display)
  monkeypatch.setattr(display, "open", lambda: True, raising=False)
  monkeypatch.setattr(display, "show_waiting", lambda: False, raising=False)
  monkeypatch.setattr(display, "close", lambda: events.append("close"), raising=False)
  args = cluster_receiver.parse_args([])
  monkeypatch.setattr(cluster_receiver, "parse_args", lambda: args)
  monkeypatch.setattr(cluster_receiver.signal, "signal", lambda *_args: None)
  with pytest.raises(RuntimeError, match="connection waiting screen"):
    cluster_receiver.main()
  assert events == ["close"]
  assert "Cluster receiver ready" not in caplog.text
