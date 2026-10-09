import socket
import threading
import time
from collections import deque

from openpilot.selfdrive.addon.cluster.cluster_jpeg import ClusterJpegEncoder
from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import (
  ACK_OK, ACK_PACKET, ACK_SCREEN_OFF, ACK_STREAM_SUPPORTED, FRAME_QUERY_STREAM, FRAME_STREAM,
  pack_frame_header, recv_exact, unpack_ack_info,
)
from openpilot.selfdrive.addon.cluster.hdmi_display.network_status import write_network_status


class ClusterNetworkDisplay:
  def __init__(self, config):
    self.config = config
    self.bind_host = config.network_bind_host
    self.port = config.network_port
    self.accept_timeout = config.network_accept_timeout
    self.ack_timeout = config.network_ack_timeout
    self.encoder = ClusterJpegEncoder(config, transport="network")
    self.connected = False
    self.screen_off = False
    self.listener = None
    self.sock = None
    self.client_address = None
    self.sequence = 0
    self.frame_count = 0
    self.max_in_flight = min(max(int(getattr(config, "network_max_in_flight", 2)), 1), 16)
    self.max_in_flight_bytes = max(int(getattr(config, "network_max_in_flight_bytes", 512 * 1024)), 1)
    self.max_frame_age = max(float(getattr(config, "network_max_frame_age", 0.25)), 0.01)
    self.max_ack_age = max(float(getattr(config, "network_max_ack_age", 0.25)), 0.01)
    self.streaming = False
    self._ack_condition = threading.Condition()
    self._pending_acks = deque()
    self._pending_bytes = 0
    self._ack_thread = None
    self._ack_generation = 0
    self._ack_closing = False
    self._ack_error = None
    self._perf_started = None
    self._last_ack_at = None
    self._reset_perf_totals()
    self._status_updated = 0.0
    self._status_error_logged = False
    self._publish_status(force=True)
    flog(
      f"ClusterNetworkDisplay initialized ({self.bind_host}:{self.port}, "
      + f"JPEG quality={self.encoder.jpeg_quality}).",
    )

  def _reset_perf_totals(self):
    self._perf_frames = 0
    self._perf_prepare_time = 0.0
    self._perf_network_time = 0.0
    self._perf_send_time = 0.0
    self._perf_ack_wait_time = 0.0
    self._perf_size_kb = 0
    self._perf_age_frames = 0
    self._perf_frame_age = self._perf_frame_age_max = 0.0
    self._perf_send_age = self._perf_send_age_max = 0.0
    self._perf_encode_age_frames = 0
    self._perf_encode_age = self._perf_encoded_wait = 0.0
    self._perf_ack_age_max = 0.0
    self._perf_ack_gap_max = 0.0
    self._perf_window_wait = self._perf_window_wait_max = 0.0
    self._perf_window_checks = 0
    self._perf_pending_frames_max = self._perf_pending_bytes_max = 0
    self._perf_stale_frames = 0

  def _publish_status(self, force=False):
    now = time.monotonic()
    if not force and now - self._status_updated < 1.0:
      return
    self._status_updated = now
    try:
      address = self.client_address
      write_network_status(address[0] if self.connected and address is not None else None)
      self._status_error_logged = False
    except OSError as e:
      if not self._status_error_logged:
        flog(f"[CLUSTER_NETWORK_WARN] Cannot publish Orange Pi connection status: {e}")
        self._status_error_logged = True

  def _disconnect_client(self):
    with self._ack_condition:
      sock = self.sock
      thread = self._ack_thread
      self.connected = False
      self.screen_off = False
      self.sock = None
      self.client_address = None
      # A reader can still be in status/log I/O when its bounded join ends.
      # Invalidate its connection before another client can reuse this queue.
      self._ack_generation += 1
      self._ack_closing = True
      self._pending_acks.clear()
      self._pending_bytes = 0
      self._last_ack_at = None
      self.streaming = False
      self._ack_condition.notify_all()
    self._publish_status(force=sock is not None)
    if sock is not None:
      try:
        sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      try:
        sock.close()
      except OSError:
        pass
    if thread is not None and thread is not threading.current_thread():
      thread.join(timeout=1.0)
    with self._ack_condition:
      if self._ack_thread is thread:
        self._ack_thread = None

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
      with self._ack_condition:
        self._ack_closing = False
        self._ack_error = None
      self._publish_status(force=True)
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

  def get_confirmed_frame_count(self):
    return self.frame_count

  def _oldest_ack_age_locked(self, now=None):
    if not self._pending_acks:
      return 0.0
    return max(0.0, (time.monotonic() if now is None else now) - self._pending_acks[0][2])

  def _has_send_capacity_locked(self, frame_size=None):
    if not self.connected or self._ack_closing or self._ack_error is not None:
      return False
    if not self.streaming:
      return True
    byte_capacity = self._pending_bytes < self.max_in_flight_bytes if frame_size is None else (
      not self._pending_acks or self._pending_bytes + frame_size <= self.max_in_flight_bytes
    )
    return (len(self._pending_acks) < self.max_in_flight and byte_capacity
            and self._oldest_ack_age_locked() <= self.max_ack_age)

  def has_send_capacity(self):
    with self._ack_condition:
      return self._has_send_capacity_locked()

  def wait_for_send_capacity(self, frame_size=None):
    """Wait before selecting latest JPEG; a slow old ACK blocks admission."""
    started = time.monotonic()
    with self._ack_condition:
      ready = self._ack_condition.wait_for(
        lambda: self._has_send_capacity_locked(frame_size) or self._ack_error is not None
        or self._ack_closing or not self.connected, timeout=self.ack_timeout,
      )
      elapsed = time.monotonic() - started
      self._perf_window_checks += 1
      self._perf_window_wait += elapsed
      self._perf_window_wait_max = max(self._perf_window_wait_max, elapsed)
      if self._ack_error is not None:
        raise ConnectionError(f"Stream ACK failed: {self._ack_error}")
      if self._ack_closing or not self.connected:
        return False
      if not ready:
        raise TimeoutError("Timed out waiting for a stream ACK window slot")
      return True

  def is_prepared_stale(self, prepared):
    created_at = getattr(prepared, "created_at", None)
    return created_at is not None and time.monotonic() - created_at > self.max_frame_age

  def record_stale_drop(self):
    with self._ack_condition:
      self._perf_stale_frames += 1

  def _start_streaming(self):
    with self._ack_condition:
      self.streaming = True
      thread = threading.Thread(target=self._ack_loop, args=(self.sock, self._ack_generation),
                                name="cluster-network-acks", daemon=True)
      self._ack_thread = thread
      thread.start()
    flog(f"[CLUSTER_NETWORK_MODE] stream: ACK on receive, maximum {self.max_in_flight} frames / "
         + f"{self.max_in_flight_bytes / 1024:.0f}KB in flight.")

  def _ack_connection_current(self, sock, generation):
    # Called with _ack_condition held; generations also reject a late error
    # from an old reader after a new connection clears _ack_closing.
    return self.sock is sock and self._ack_generation == generation and self.connected and not self._ack_closing

  def _ack_loop(self, sock, generation):
    try:
      while True:
        with self._ack_condition:
          self._ack_condition.wait_for(lambda: self._pending_acks or not self._ack_connection_current(sock, generation))
          if not self._ack_connection_current(sock, generation):
            return
          sequence, prepared, started, sent_at = self._pending_acks[0]
        ack_sequence, status, flags = unpack_ack_info(recv_exact(sock, ACK_PACKET.size))
        acknowledged_at = time.monotonic()
        with self._ack_condition:
          if not self._ack_connection_current(sock, generation):
            return
          if ack_sequence != sequence or status != ACK_OK or not flags & ACK_STREAM_SUPPORTED:
            raise ConnectionError(f"Invalid stream ACK: expected={sequence}, received={ack_sequence}, status={status}, flags={flags}")
          self.screen_off = bool(flags & ACK_SCREEN_OFF)
          self._pending_acks.popleft()
          self._pending_bytes -= len(prepared.jpeg)
          # Count the validated receive ACK before log/status I/O can stall.
          # A later generation guard can discard old statistics without losing
          # a successfully acknowledged frame from the cumulative total.
          self.frame_count += 1
          self._ack_condition.notify_all()
        self._record_ack(prepared, sequence, started, sent_at, acknowledged_at, generation)
    except (ConnectionError, OSError, ValueError) as e:
      with self._ack_condition:
        if not self._ack_connection_current(sock, generation):
          return
        self._ack_error = e
        self.connected = False
        self._ack_condition.notify_all()
      flog(f"[CLUSTER_NETWORK_ERROR] Stream ACK failed: {e}")
      # Unblock a sender that might be in sendall. The sender/open/close path
      # owns cleanup and joins this reader before accepting another connection.
      try:
        sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      self._publish_status(force=True)

  def _send_streamed(self, prepared, sequence):
    with self._ack_condition:
      ready = self._has_send_capacity_locked(len(prepared.jpeg))
    if not ready and not self.wait_for_send_capacity(len(prepared.jpeg)):
      return False
    # Byte capacity may have required a second wait after the pipeline chose
    # its newest JPEG. Expire it before writing any header or sequence number.
    if self.is_prepared_stale(prepared):
      self.record_stale_drop()
      return None
    started = time.monotonic()
    self.sock.sendall(pack_frame_header(sequence, len(prepared.jpeg), FRAME_STREAM))
    self.sock.sendall(prepared.jpeg)
    sent_at = time.monotonic()
    with self._ack_condition:
      if self._ack_closing or not self.connected:
        return False
      self.sequence = sequence
      self._pending_acks.append((sequence, prepared, started, sent_at))
      self._pending_bytes += len(prepared.jpeg)
      self._perf_pending_frames_max = max(self._perf_pending_frames_max, len(self._pending_acks))
      self._perf_pending_bytes_max = max(self._perf_pending_bytes_max, self._pending_bytes)
      self._ack_condition.notify_all()
    return True

  def send_prepared(self, prepared):
    if not self.connected or self.sock is None or prepared is None:
      return False
    # None denotes an intentional stale drop, distinct from a transport error.
    if self.is_prepared_stale(prepared):
      self.record_stale_drop()
      return None

    sequence = (self.sequence + 1) & 0xFFFFFFFF
    try:
      if self.streaming:
        return self._send_streamed(prepared, sequence)
      started = time.monotonic()
      self.sock.sendall(pack_frame_header(sequence, len(prepared.jpeg), FRAME_QUERY_STREAM))
      self.sock.sendall(prepared.jpeg)
      sent_at = time.monotonic()
      ack_sequence, status, flags = unpack_ack_info(recv_exact(self.sock, ACK_PACKET.size))
      acknowledged_at = time.monotonic()
      if ack_sequence != sequence:
        raise ConnectionError(f"Cluster ACK sequence mismatch: sent={sequence}, received={ack_sequence}")

      self.sequence = sequence
      if status != ACK_OK:
        flog(f"[CLUSTER_NETWORK_WARN] Orange Pi rejected frame#{sequence}: status={status}")
        return False

      self.screen_off = bool(flags & ACK_SCREEN_OFF)
      self._record_ack(prepared, sequence, started, sent_at, acknowledged_at)
      if flags & ACK_STREAM_SUPPORTED:
        self._start_streaming()
      elif self.frame_count == 1:
        flog("[CLUSTER_NETWORK_MODE] legacy: Pi update required for ACK on receive.")
      return True
    except (ConnectionError, OSError, ValueError) as e:
      flog(f"[CLUSTER_NETWORK_ERROR] Failed to send frame: {e}")
      self._disconnect_client()
      return False

  def _record_ack(self, prepared, sequence, started, sent_at, acknowledged_at, generation=None):
    first_message = perf_message = None
    elapsed = acknowledged_at - started
    with self._ack_condition:
      if generation is not None and generation != self._ack_generation:
        return
      if generation is None:
        self.frame_count += 1
      now = time.monotonic()
      if self._perf_started is None:
        self._perf_started = started
      self._perf_frames += 1
      self._perf_prepare_time += prepared.prepare_elapsed
      self._perf_network_time += elapsed
      self._perf_send_time += sent_at - started
      self._perf_ack_wait_time += acknowledged_at - sent_at
      self._perf_ack_age_max = max(self._perf_ack_age_max, acknowledged_at - sent_at)
      self._perf_size_kb += prepared.size_kb
      if self._last_ack_at is not None:
        self._perf_ack_gap_max = max(self._perf_ack_gap_max, acknowledged_at - self._last_ack_at)
      self._last_ack_at = acknowledged_at
      created_at, encoded_at = getattr(prepared, "created_at", None), getattr(prepared, "encoded_at", None)
      if created_at is not None:
        frame_age = max(0.0, acknowledged_at - created_at)
        send_age = max(0.0, started - created_at)
        self._perf_age_frames += 1
        self._perf_frame_age += frame_age
        self._perf_frame_age_max = max(self._perf_frame_age_max, frame_age)
        self._perf_send_age += send_age
        self._perf_send_age_max = max(self._perf_send_age_max, send_age)
        if encoded_at is not None:
          self._perf_encode_age_frames += 1
          self._perf_encode_age += max(0.0, encoded_at - created_at)
          self._perf_encoded_wait += max(0.0, started - encoded_at)

      if self.frame_count == 1:
        first_message = (f"[CLUSTER_NETWORK_TX] frame#{sequence} | Size: {prepared.size_kb} KB | "
                         + f"elapsed={elapsed:.3f}s | prep={prepared.prepare_elapsed * 1000:.1f}ms (ACK received)")

      perf_interval_frames = max(1, int(getattr(self.config, "status_interval_frames", self.config.fps * 10)))
      if self._perf_frames >= perf_interval_frames or now - self._perf_started >= 10.0:
        perf_elapsed = max(now - self._perf_started, 1e-6)

        def average_ms(total, count):
          return f"{total * 1000 / count:.1f}ms" if count else "n/a"

        perf_message = (
          f"[CLUSTER_NETWORK_PERF] fps={self._perf_frames / perf_elapsed:.2f} | "
          + f"size_avg={self._perf_size_kb / self._perf_frames:.1f}KB | "
          + f"prep_avg={self._perf_prepare_time * 1000 / self._perf_frames:.1f}ms | "
          + f"network_avg={self._perf_network_time * 1000 / self._perf_frames:.1f}ms | "
          + f"send_avg={self._perf_send_time * 1000 / self._perf_frames:.1f}ms | "
          + f"ack_wait_avg={self._perf_ack_wait_time * 1000 / self._perf_frames:.1f}ms | "
          + f"ack_wait_max={self._perf_ack_age_max * 1000:.1f}ms | ack_gap_max={self._perf_ack_gap_max * 1000:.1f}ms | "
          + f"encode_age_avg={average_ms(self._perf_encode_age, self._perf_encode_age_frames)} | "
          + f"encoded_wait_avg={average_ms(self._perf_encoded_wait, self._perf_encode_age_frames)} | "
          + f"send_age_avg={average_ms(self._perf_send_age, self._perf_age_frames)} | send_age_max={self._perf_send_age_max * 1000:.1f}ms | "
          + f"frame_age_avg={average_ms(self._perf_frame_age, self._perf_age_frames)} | frame_age_max={self._perf_frame_age_max * 1000:.1f}ms | "
          + f"window_wait_avg={average_ms(self._perf_window_wait, self._perf_window_checks)} | window_wait_max={self._perf_window_wait_max * 1000:.1f}ms | "
          + f"pending_frames={len(self._pending_acks)} | pending_kb={self._pending_bytes / 1024:.1f} | "
          + f"oldest_ack_age={self._oldest_ack_age_locked(now) * 1000:.1f}ms | "
          + f"pending_frames_max={self._perf_pending_frames_max} | pending_kb_max={self._perf_pending_bytes_max / 1024:.1f} | "
          + f"stale_drops={self._perf_stale_frames} | screen_off={int(self.screen_off)} | "
          + f"mode={'stream' if self.streaming else 'legacy'} | ack={'receive' if self.streaming else 'display'}"
        )
        self._perf_started = now
        self._reset_perf_totals()
    # Status files/log sinks can block: never hold queue credits while writing.
    self._publish_status()
    if first_message is not None:
      flog(first_message)
    if perf_message is not None:
      flog(perf_message)

  def send_image(self, frame_image):
    if not self.connected:
      return False
    return self.send_prepared(self.prepare_image(frame_image))

  def close(self):
    flog("Closing cluster network connection.")
    self._disconnect_client()
    self._close_listener()
