import ipaddress
import socket
import threading

from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  ACK_DISPLAY_ERROR, ACK_OK, ACK_PACKET, pack_ack as pack_c4_ack, pack_frame_header, recv_exact, unpack_ack,
)
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import cluster_receiver
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.cluster_protocol import (
  pack_ack as pack_orange_pi_ack, unpack_frame_header,
)


class FakeDisplay:
  def __init__(self, succeeds=True):
    self.succeeds = succeeds
    self.frames = []

  def send_jpeg(self, jpeg):
    self.frames.append(jpeg)
    return self.succeeds


def _exchange_frame(display):
  c4_sock, pi_sock = socket.socketpair()
  receiver = threading.Thread(
    target=cluster_receiver.receive_frames,
    args=(pi_sock, display, 1),
    daemon=True,
  )
  receiver.start()
  try:
    payload = b"jpeg-frame"
    c4_sock.sendall(pack_frame_header(7, len(payload)) + payload)
    ack = unpack_ack(recv_exact(c4_sock, ACK_PACKET.size))
    receiver.join(timeout=1.0)
    assert not receiver.is_alive()
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
