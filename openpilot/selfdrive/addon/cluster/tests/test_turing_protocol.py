import hashlib
import struct
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.usb_display import turing_protocol


def test_h264_command_matches_the_previous_vendor_wire_packet(monkeypatch):
  # Golden packet captured from the previous vendor implementation with this
  # clock and a 32 KiB H.264 chunk. Keep the panel protocol unchanged.
  monkeypatch.setattr(turing_protocol, "time", SimpleNamespace(
    time=lambda: 12345.125, mktime=lambda _date: 10000.0,
    localtime=lambda: (2026, 10, 17, 0, 0, 0, 0, 0, 0),
  ))
  header = turing_protocol.build_command_packet_header(121)
  assert len(header) == 500
  assert header[:4] == b"\x79\x00\x1a\x6d"
  assert struct.unpack("<I", header[4:8])[0] == 2345125
  header[8:12] = (32768).to_bytes(4, "big")
  packet = turing_protocol.encrypt_command_packet(header)
  assert len(packet) == 512
  assert packet[510:] == b"\xa1\x1a"
  assert hashlib.sha256(packet).hexdigest() == "6a8d539318c371061e2e22e6a512bf37e0c8780a00268a0c4a1f0f0d8442e090"


@pytest.mark.parametrize("response,ok", [
  (None, False), (b"", False), (b"\x00", False), (b"\x00\xc8", True),
  (bytes(8) + b"\xc8", True), (bytes(9), False),
])
def test_panel_status_accepts_both_documented_ack_positions(response, ok):
  assert turing_protocol.response_ok(response) is ok
