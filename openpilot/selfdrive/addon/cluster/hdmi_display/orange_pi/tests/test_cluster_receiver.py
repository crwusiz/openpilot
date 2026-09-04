import ipaddress
import socket
import threading
import time

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  ACK_DISPLAY_ERROR, ACK_OK, ACK_PACKET, pack_ack as pack_c4_ack, pack_frame_header, recv_exact, unpack_ack,
)
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import cluster_receiver, hdmi_display
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.cluster_protocol import (
  pack_ack as pack_orange_pi_ack, recv_exact as pi_recv_exact, unpack_frame_header,
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


def _exchange_frame(display):
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
    c4_sock.sendall(pack_frame_header(7, len(payload)) + payload)
    ack = unpack_ack(recv_exact(c4_sock, ACK_PACKET.size))
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


def test_receiver_acknowledges_successful_hdmi_frame():
  display = FakeDisplay()
  assert _exchange_frame(display) == (7, ACK_OK)
  assert display.frames == [b"jpeg-frame"]


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


@pytest.mark.parametrize("argv", [
  ["--port", "65536"], ["--port", "0"], ["--width", "0"], ["--height", "-1"],
  ["--scan-workers", "0"], ["--scan-timeout", "nan"], ["--reconnect-delay", "-1"],
  ["--frame-timeout", "inf"], ["--display-index", "-1"], ["--host", "invalid"],
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
  try:
    data = pi_recv_exact(pi_sock, 6, deadline=time.monotonic() + 1.0,
                         poll_events=lambda: c4_sock.sendall(next(chunks)))
    assert data == b"abcdef"
  finally:
    c4_sock.close()
    pi_sock.close()


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


def test_main_clears_disconnect_before_retry_and_uses_direct_host(monkeypatch):
  events = []
  display = FakeDisplay()
  monkeypatch.setattr(hdmi_display, "HdmiDisplay", lambda **_kwargs: display)
  monkeypatch.setattr(display, "open", lambda: True, raising=False)
  monkeypatch.setattr(display, "clear", lambda: events.append("clear"), raising=False)
  monkeypatch.setattr(display, "close", lambda: events.append("close"), raising=False)
  args = cluster_receiver.parse_args(["--host", "127.0.0.1"])
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
    return Connection()

  def fail_receive(*_args, **_kwargs):
    raise ConnectionError("lost Wi-Fi")

  def retry(*_args):
    events.append("retry")
    raise KeyboardInterrupt

  monkeypatch.setattr(cluster_receiver, "_probe", probe)
  monkeypatch.setattr(cluster_receiver, "receive_frames", fail_receive)
  monkeypatch.setattr(cluster_receiver, "wait_for_reconnect", retry)
  cluster_receiver.main()
  assert events == ["disconnect", "clear", "retry", "close"]
