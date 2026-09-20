import socket
import time

from openpilot.selfdrive.addon.cluster.cluster_jpeg import ClusterJpegEncoder
from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  ACK_OK, ACK_PACKET, pack_frame_header, recv_exact, unpack_ack,
)


class ClusterNetworkDisplay:
  def __init__(self, config):
    self.config = config
    self.bind_host = config.network_bind_host
    self.port = config.network_port
    self.accept_timeout = config.network_accept_timeout
    self.ack_timeout = config.network_ack_timeout
    self.encoder = ClusterJpegEncoder(config, transport="network")
    self.connected = False
    self.listener = None
    self.sock = None
    self.client_address = None
    self.sequence = 0
    self.frame_count = 0
    self._perf_started = None
    self._perf_frames = 0
    self._perf_prepare_time = 0.0
    self._perf_network_time = 0.0
    self._perf_size_kb = 0
    flog(
      f"ClusterNetworkDisplay initialized ({self.bind_host}:{self.port}, "
      + f"JPEG quality={self.encoder.jpeg_quality}).",
    )

  def _disconnect_client(self):
    sock = self.sock
    self.connected = False
    self.sock = None
    self.client_address = None
    if sock is not None:
      try:
        sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      try:
        sock.close()
      except OSError:
        pass

  def _close_listener(self):
    listener = self.listener
    self.listener = None
    if listener is not None:
      try:
        listener.close()
      except OSError:
        pass

  def _ensure_listener(self):
    if self.listener is not None:
      return True

    listener = None
    try:
      listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      listener.bind((self.bind_host, self.port))
      listener.listen(1)
      listener.settimeout(self.accept_timeout)
      self.listener = listener
      flog(f"[CLUSTER_NETWORK_LISTEN] Waiting for Orange Pi on {self.bind_host}:{self.port}.")
      return True
    except OSError as e:
      flog(f"[CLUSTER_NETWORK_ERROR] Failed to listen on {self.bind_host}:{self.port}: {e}")
      if listener is not None:
        try:
          listener.close()
        except OSError:
          pass
      return False

  def open(self):
    if self.connected:
      return True

    self._disconnect_client()
    if not self._ensure_listener():
      return False

    try:
      sock, address = self.listener.accept()
      sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
      sock.settimeout(self.ack_timeout)
      self.sock = sock
      self.client_address = address
      self.connected = True
      flog(f"[CLUSTER_NETWORK_SUCCESS] Orange Pi connected from {address[0]}:{address[1]}.")
      return True
    except TimeoutError:
      return False
    except OSError as e:
      flog(f"[CLUSTER_NETWORK_WARN] Failed to accept Orange Pi connection: {e}")
      self._close_listener()
      return False

  def prepare_image(self, frame_image):
    return self.encoder.prepare_image(frame_image)

  def send_prepared(self, prepared):
    if not self.connected or self.sock is None or prepared is None:
      return False

    sequence = (self.sequence + 1) & 0xFFFFFFFF
    try:
      started = time.monotonic()
      self.sock.sendall(pack_frame_header(sequence, len(prepared.jpeg)))
      self.sock.sendall(prepared.jpeg)
      ack_sequence, status = unpack_ack(recv_exact(self.sock, ACK_PACKET.size))
      if ack_sequence != sequence:
        raise ConnectionError(f"Cluster ACK sequence mismatch: sent={sequence}, received={ack_sequence}")

      self.sequence = sequence
      if status != ACK_OK:
        flog(f"[CLUSTER_NETWORK_WARN] Orange Pi rejected frame#{sequence}: status={status}")
        return False

      elapsed = time.monotonic() - started
      self.frame_count += 1
      now = time.monotonic()
      if self._perf_started is None:
        self._perf_started = now
      self._perf_frames += 1
      self._perf_prepare_time += prepared.prepare_elapsed
      self._perf_network_time += elapsed
      self._perf_size_kb += prepared.size_kb

      if self.frame_count == 1:
        flog(
          f"[CLUSTER_NETWORK_TX] frame#{sequence} | Size: {prepared.size_kb} KB | "
          + f"elapsed={elapsed:.3f}s | prep={prepared.prepare_elapsed * 1000:.1f}ms (ACK received)",
        )

      perf_interval_frames = max(1, int(getattr(self.config, "status_interval_frames", self.config.fps * 10)))
      if self._perf_frames >= perf_interval_frames:
        perf_elapsed = max(now - self._perf_started, 1e-6)
        flog(
          f"[CLUSTER_NETWORK_PERF] fps={self._perf_frames / perf_elapsed:.2f} | "
          + f"size_avg={self._perf_size_kb / self._perf_frames:.1f}KB | "
          + f"prep_avg={self._perf_prepare_time * 1000 / self._perf_frames:.1f}ms | "
          + f"network_avg={self._perf_network_time * 1000 / self._perf_frames:.1f}ms",
        )
        self._perf_started = now
        self._perf_frames = 0
        self._perf_prepare_time = 0.0
        self._perf_network_time = 0.0
        self._perf_size_kb = 0

      return True
    except (ConnectionError, OSError, ValueError) as e:
      flog(f"[CLUSTER_NETWORK_ERROR] Failed to send frame: {e}")
      self._disconnect_client()
      return False

  def send_image(self, frame_image):
    if not self.connected:
      return False
    return self.send_prepared(self.prepare_image(frame_image))

  def close(self):
    flog("Closing cluster network connection.")
    self._disconnect_client()
    self._close_listener()
