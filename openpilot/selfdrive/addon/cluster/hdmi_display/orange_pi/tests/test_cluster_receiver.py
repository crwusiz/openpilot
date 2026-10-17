import ipaddress
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import control_socket, cluster_receiver, hdmi_display
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.control_socket import ReceiveStats, ReceiveTimeout, recv_exact as pi_recv_exact
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_protocol import HELLO, pack_control
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.hdmi_display import HdmiDisplay


class FakeDisplay:
  def __init__(self):
    self.frames = []
    self.poll_count = 0
    self.set_stream_mode = lambda _mode: None

  def pump_events(self):
    self.poll_count += 1
    return True


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


def _scheduled_receive_socket(monkeypatch, packets):
  """Socket readiness follows a monotonic schedule, including partial reads."""
  now = [0.0]
  queued = list(packets)

  class ScheduledSocket:
    def __init__(self):
      self.acks = []

    def recv_into(self, view):
      available_at, data = queued.pop(0)
      assert available_at <= now[0] + 1e-9
      count = min(len(data), len(view))
      view[:count] = data[:count]
      if data[count:]:
        queued.insert(0, (available_at, data[count:]))
      return count

    def sendall(self, packet):
      self.acks.append(packet)

  sock = ScheduledSocket()

  def readable(readers, _writers, _errors, timeout):
    available_at = queued[0][0] if queued else float("inf")
    if available_at <= now[0] + timeout:
      now[0] = max(now[0], available_at)
      return readers, [], []
    now[0] += timeout
    return [], [], []

  clock = SimpleNamespace(monotonic=lambda: now[0])
  for module in (control_socket, cluster_receiver):
    monkeypatch.setattr(module, "time", clock)
  monkeypatch.setattr(control_socket.select, "select", readable)
  return sock, now


@pytest.mark.parametrize("fragment_header", [False, True])
def test_h264_handshake_has_a_separate_bounded_budget_after_idle(monkeypatch, fragment_header):
  packet = pack_control(HELLO, {"codec": "h264", "transport": "webrtc"})
  header, payload = packet[:16], packet[16:]
  packets = ([(1.9, header[:3]), (2.2, header[3:]), (2.4, payload)] if fragment_header
             else [(1.9, header), (2.1, payload)])
  sock, _now = _scheduled_receive_socket(monkeypatch, packets)
  calls = []
  monkeypatch.setattr(cluster_receiver, "receive_h264_session", lambda *args: calls.append(args) or 1)
  display = FakeDisplay()
  assert cluster_receiver.receive_frames(sock, display, max_frames=1, frame_timeout=2.0) == 1
  assert calls[0][2] == {"codec": "h264", "transport": "webrtc"}
  assert display.poll_count > 0


@pytest.mark.parametrize("phase,packets,received,expected", [
  ("idle_header", [], 0, 16),
  ("partial_header", [(0.1, pack_control(HELLO, {})[:3])], 3, 16),
  ("partial_h264_hello", [(0.1, pack_control(HELLO, {})[:16]), (0.7, b"{"), (2.2, b"}")], 1, 2),
])
def test_h264_handshake_progress_never_extends_a_partial_message(monkeypatch, phase, packets, received, expected):
  sock, now = _scheduled_receive_socket(monkeypatch, packets)
  with pytest.raises(ReceiveTimeout) as error:
    cluster_receiver.receive_frames(sock, FakeDisplay(), frame_timeout=2.0)
  assert error.value.phase == phase
  assert (error.value.received, error.value.expected) == (received, expected)
  assert now[0] == pytest.approx(2.0 if phase == "idle_header" else 2.1)


def test_unknown_wire_protocol_never_starts_a_video_session(monkeypatch):
  header = b"NONE" + pack_control(HELLO, {})[4:16]
  sock, _now = _scheduled_receive_socket(monkeypatch, [(0.1, header)])
  monkeypatch.setattr(cluster_receiver, "receive_h264_session", lambda *_args: pytest.fail("unsupported protocol"))
  with pytest.raises(ValueError, match="Unsupported H.264 cluster protocol"):
    cluster_receiver.receive_frames(sock, FakeDisplay(), frame_timeout=2.0)


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

  monkeypatch.setattr(control_socket, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(control_socket.select, "select", select_readable)
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
      view[:] = b"data"
      return len(view)

  monkeypatch.setattr(control_socket, "time", SimpleNamespace(monotonic=lambda: pytest.fail("unnecessary timing call")))
  assert pi_recv_exact(CompleteSocket(), 4) == b"data"


def test_late_readiness_cannot_restart_an_expired_idle_header_deadline(monkeypatch):
  now = [0.0]

  def late_readiness(readers, _writers, _errors, _timeout):
    # The syscall became ready, but the Python thread resumed after its limit.
    now[0] = 2.01
    return readers, [], []

  class ReadySocket:
    def recv_into(self, _view):
      pytest.fail("expired idle must not read a header and restart the frame budget")

  monkeypatch.setattr(control_socket, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(control_socket.select, "select", late_readiness)
  with pytest.raises(ReceiveTimeout) as raised:
    pi_recv_exact(ReadySocket(), 16, deadline=2.0, timeout_from_first_byte=2.0, phase="header", stats=ReceiveStats())
  assert raised.value.phase == "idle_header"
  assert raised.value.received == 0


def test_first_header_read_returning_late_cannot_renew_the_idle_deadline(monkeypatch):
  now = [0.0]

  def on_time_readiness(readers, _writers, _errors, _timeout):
    now[0] = 1.99
    return readers, [], []

  class LateReadSocket:
    def recv_into(self, view):
      now[0] = 2.01
      view[:] = pack_control(HELLO, {})[:16]
      return len(view)

  monkeypatch.setattr(control_socket, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(control_socket.select, "select", on_time_readiness)
  stats = ReceiveStats()
  with pytest.raises(ReceiveTimeout) as raised:
    pi_recv_exact(LateReadSocket(), 16, deadline=2.0, timeout_from_first_byte=2.0, phase="header", stats=stats)
  assert raised.value.phase == "idle_header"
  assert stats.first_byte_at is None


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


def test_known_c4_allows_handshake_longer_than_subnet_scan_timeout(monkeypatch):
  sock = SimpleNamespace(close=lambda: pytest.fail("successful socket must stay open"))
  attempts, polls = [], []

  def probe(address, port, timeout, stop):
    attempts.append((address, port, timeout))
    assert not stop.is_set()
    return sock if timeout >= 0.3 else None

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  assert cluster_receiver.connect_known_c4("192.168.77.40", 9200, lambda: polls.append(True)) is sock
  assert attempts == [("192.168.77.40", 9200, cluster_receiver.KNOWN_CONNECT_TIMEOUT)]
  assert cluster_receiver.DEFAULT_SCAN_TIMEOUT < 0.3 and polls


def test_known_c4_retries_transient_refusal_and_pumps_events_between_attempts(monkeypatch):
  now, polls, attempts = [0.0], [], []
  sock = SimpleNamespace(close=lambda: pytest.fail("successful socket must stay open"))
  monkeypatch.setattr(cluster_receiver, "time", SimpleNamespace(
    monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
  ))

  def probe(address, port, timeout, _stop):
    attempts.append((address, port, timeout))
    return None if len(attempts) == 1 else sock

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  assert cluster_receiver.connect_known_c4("192.168.77.40", 9200, lambda: polls.append(now[0])) is sock
  assert len(attempts) == 2 and all(attempt[2] == 1.0 for attempt in attempts)
  assert now[0] == pytest.approx(cluster_receiver.KNOWN_CONNECT_RETRY_DELAY)
  assert len(polls) >= 3


def test_known_c4_failed_handshakes_share_a_bounded_total_budget(monkeypatch):
  now, timeouts = [0.0], []
  monkeypatch.setattr(cluster_receiver, "time", SimpleNamespace(
    monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
  ))

  def probe(_address, _port, timeout, _stop):
    timeouts.append(timeout)
    now[0] += timeout
    return None

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  assert cluster_receiver.connect_known_c4("192.168.77.40", 9200) is None
  assert timeouts == pytest.approx([1.0, 0.85])
  assert now[0] == pytest.approx(cluster_receiver.KNOWN_CONNECT_BUDGET)


def test_known_c4_closes_a_connection_returned_after_total_budget(monkeypatch):
  now, closed = [0.0], []
  monkeypatch.setattr(cluster_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))

  def probe(*_args):
    now[0] = cluster_receiver.KNOWN_CONNECT_BUDGET + 0.01
    return SimpleNamespace(close=lambda: closed.append(True))

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  assert cluster_receiver.connect_known_c4("192.168.77.40", 9200) is None
  assert closed == [True]


def test_known_c4_quit_cancels_probe_and_closes_a_racing_socket(monkeypatch):
  started, closed = threading.Event(), []

  def probe(_address, _port, _timeout, stop):
    started.set()
    assert stop.wait(timeout=1.0)
    return SimpleNamespace(close=lambda: closed.append(True))

  def poll_events():
    if started.is_set():
      raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  with pytest.raises(KeyboardInterrupt):
    cluster_receiver.connect_known_c4("192.168.77.40", 9200, poll_events)
  assert started.is_set() and closed == [True]


@pytest.mark.parametrize("setup_error", [False, True])
def test_main_shows_waiting_at_startup_and_disconnect_before_retry(monkeypatch, caplog, setup_error):
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
    if setup_error:
      from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_receiver import H264ConfigurationError
      raise H264ConfigurationError("H.264 assets unavailable")
    raise ConnectionError("lost Wi-Fi")

  delays = []
  def retry(*_args):
    delays.append(_args[1])
    events.append("retry")
    raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  monkeypatch.setattr(cluster_receiver, "receive_frames", fail_receive)
  monkeypatch.setattr(cluster_receiver, "wait_for_reconnect", retry)
  with caplog.at_level("INFO"):
    cluster_receiver.main()
  assert events == ["waiting", "probe", "disconnect", "waiting", "retry", "close"]
  assert delays == [5.0 if setup_error else args.reconnect_delay]
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


def test_main_explicit_host_does_not_scan_after_known_peer_retries_fail(monkeypatch):
  events = []
  display = FakeDisplay()
  args = cluster_receiver.parse_args(["--host", "192.168.77.40"])
  monkeypatch.setattr(hdmi_display, "HdmiDisplay", lambda **kwargs: display)
  monkeypatch.setattr(display, "open", lambda: True, raising=False)
  monkeypatch.setattr(display, "show_waiting", lambda: True, raising=False)
  monkeypatch.setattr(display, "close", lambda: events.append("close"), raising=False)
  monkeypatch.setattr(cluster_receiver, "parse_args", lambda: args)
  monkeypatch.setattr(cluster_receiver.signal, "signal", lambda *_args: None)
  monkeypatch.setattr(cluster_receiver, "discover_c4", lambda *_args: pytest.fail("explicit host must never scan"))
  monkeypatch.setattr(cluster_receiver, "connect_known_c4", lambda address, port, _poll: events.append((address, port)))

  def retry(*_args):
    raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "wait_for_reconnect", retry)
  cluster_receiver.main()
  assert events == [("192.168.77.40", 9200), "close"]


@pytest.mark.parametrize("cached_available", [False, True])
@pytest.mark.parametrize("connection_duration", [0.1, 3.0])
def test_main_retries_last_c4_before_scanning_and_backs_off_only_short_sessions(monkeypatch, cached_available, connection_duration):
  events = []
  now = [0.0]
  display = FakeDisplay()
  monkeypatch.setattr(hdmi_display, "HdmiDisplay", lambda **kwargs: display)
  monkeypatch.setattr(display, "open", lambda: True, raising=False)
  monkeypatch.setattr(display, "show_waiting", lambda: True, raising=False)
  monkeypatch.setattr(display, "close", lambda: events.append("close"), raising=False)
  args = cluster_receiver.parse_args([])
  monkeypatch.setattr(cluster_receiver, "parse_args", lambda: args)
  monkeypatch.setattr(cluster_receiver.signal, "signal", lambda *_args: None)
  monkeypatch.setattr(cluster_receiver, "time", SimpleNamespace(
    monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
  ))
  monkeypatch.setattr(cluster_receiver, "wait_for_reconnect", lambda _display, delay: events.append(("retry", delay)))

  class Connection:
    def __init__(self, address):
      self.address = address

    def getpeername(self):
      return self.address, args.port

    def settimeout(self, timeout):
      assert timeout == args.frame_timeout

    def close(self):
      events.append(("disconnect", self.address))

  discoveries = iter(["192.168.77.40", "192.168.77.41"])

  def discover(*_args):
    address = next(discoveries)
    events.append(("discover", address))
    return Connection(address)

  def probe(address, *_args):
    events.append(("probe", address))
    assert address == "192.168.77.40"
    return Connection(address) if cached_available else None

  def receive(sock, *_args, **_kwargs):
    events.append(("receive", sock.address))
    if len([event for event in events if event[0] == "receive"]) == 1:
      now[0] += connection_duration
      raise TimeoutError("stalled H.264 video")
    raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "discover_c4", discover)
  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  monkeypatch.setattr(cluster_receiver, "receive_frames", receive)
  cluster_receiver.main()
  recovered_address = "192.168.77.40" if cached_available else "192.168.77.41"
  expected = [("discover", "192.168.77.40"), ("receive", "192.168.77.40"), ("disconnect", "192.168.77.40")]
  if connection_duration < args.reconnect_delay:
    expected.append(("retry", args.reconnect_delay))
  expected.append(("probe", "192.168.77.40"))
  if not cached_available:
    expected.append(("probe", "192.168.77.40"))
    expected.append(("discover", recovered_address))
  assert events == expected + [("receive", recovered_address), ("disconnect", recovered_address), "close"]


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
