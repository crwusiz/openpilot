"""Read-only Linux TCP_INFO snapshots; no socket tuning or extra dependencies."""

import socket
import struct
import sys


class TcpDiagnostics:
  def __init__(self):
    self._last_total_retrans = None

  def sample(self, sock) -> str:
    if not sys.platform.startswith("linux"):
      return ""
    option = getattr(socket, "TCP_INFO", None)
    if option is None:
      return ""
    try:
      info = sock.getsockopt(socket.IPPROTO_TCP, option, 256)
      if len(info) < 104:
        return ""

      def value(offset):
        return struct.unpack_from("=I", info, offset)[0]

      total_retrans = value(100)
      retrans_delta = 0 if self._last_total_retrans is None else max(0, total_retrans - self._last_total_retrans)
      self._last_total_retrans = total_retrans
      fields = [
        f"tcp_rtt_ms={value(68) / 1000:.1f}",
        f"tcp_rttvar_ms={value(72) / 1000:.1f}",
        f"tcp_rto_ms={value(8) / 1000:.1f}",
        f"tcp_unacked={value(24)}",
        f"tcp_lost={value(32)}",
        f"tcp_cwnd={value(80)}",
        f"tcp_rcv_space={value(96)}",
        f"tcp_total_retrans={total_retrans}",
        f"tcp_retrans_delta={retrans_delta}",
      ]
      if len(info) >= 232:
        fields.append(f"tcp_snd_wnd={value(228)}")
      return " | ".join(fields)
    except (AttributeError, OSError, TypeError, ValueError, struct.error):
      return ""
