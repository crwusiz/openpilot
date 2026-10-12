import socket
import struct

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import tcp_diagnostics
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.tcp_diagnostics import TcpDiagnostics


@pytest.fixture(autouse=True)
def linux_platform(monkeypatch):
  monkeypatch.setattr(tcp_diagnostics.sys, "platform", "linux")


def _info(total_retrans, size=104):
  data = bytearray(size)
  for offset, value in {8: 204000, 24: 2, 32: 1, 68: 12340, 72: 2040, 80: 10, 96: 65535, 100: total_retrans}.items():
    struct.pack_into("=I", data, offset, value)
  if size >= 232:
    struct.pack_into("=I", data, 228, 32768)
  return bytes(data)


class SnapshotSocket:
  def __init__(self, snapshots):
    self.snapshots = iter(snapshots)

  def getsockopt(self, level, option, size):
    assert (level, option, size) == (socket.IPPROTO_TCP, socket.TCP_INFO, 256)
    return next(self.snapshots)


@pytest.mark.parametrize("size", [104, 232])
def test_tcp_snapshot_reports_kernel_fields_and_per_connection_retransmission_delta(monkeypatch, size):
  monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)
  sock = SnapshotSocket([_info(7, size), _info(12, size), _info(2, size)])
  diagnostics = TcpDiagnostics()
  first = diagnostics.sample(sock)
  assert first == ("tcp_rtt_ms=12.3 | tcp_rttvar_ms=2.0 | tcp_rto_ms=204.0 | tcp_unacked=2 | tcp_lost=1"
                   + " | tcp_cwnd=10 | tcp_rcv_space=65535 | tcp_total_retrans=7 | tcp_retrans_delta=0"
                   + (" | tcp_snd_wnd=32768" if size == 232 else ""))
  assert "tcp_retrans_delta=5" in diagnostics.sample(sock)
  assert "tcp_retrans_delta=0" in diagnostics.sample(sock)
  assert "tcp_retrans_delta=0" in TcpDiagnostics().sample(SnapshotSocket([_info(15, size)]))


def test_short_tcp_info_and_missing_platform_support_do_not_interrupt_receiver(monkeypatch):
  monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)
  assert TcpDiagnostics().sample(SnapshotSocket([bytes(103)])) == ""
  assert TcpDiagnostics().sample(object()) == ""
  monkeypatch.delattr(socket, "TCP_INFO")
  assert TcpDiagnostics().sample(SnapshotSocket([])) == ""


def test_unavailable_kernel_tcp_info_does_not_interrupt_receiver(monkeypatch):
  monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)

  class UnavailableSocket:
    def getsockopt(self, *_args):
      raise OSError("TCP_INFO is unavailable")

  assert TcpDiagnostics().sample(UnavailableSocket()) == ""


def test_non_linux_tcp_info_layout_is_not_interpreted(monkeypatch):
  monkeypatch.setattr(tcp_diagnostics.sys, "platform", "freebsd")
  monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)
  assert TcpDiagnostics().sample(SnapshotSocket([])) == ""
