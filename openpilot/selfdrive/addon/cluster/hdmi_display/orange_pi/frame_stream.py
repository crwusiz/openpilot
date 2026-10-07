import logging
import socket
import threading
import time
from typing import NamedTuple

try:
  from .cluster_protocol import ACK_OK, ACK_STREAM_SUPPORTED, FRAME_HEADER, FRAME_STREAM, pack_ack, recv_exact, unpack_frame_header_info
except ImportError:
  from cluster_protocol import ACK_OK, ACK_STREAM_SUPPORTED, FRAME_HEADER, FRAME_STREAM, pack_ack, recv_exact, unpack_frame_header_info


LOG = logging.getLogger("cluster_receiver")


class ReceivedFrame(NamedTuple):
  sequence: int
  jpeg: bytes
  started: float
  header_at: float
  received_at: float


class LatestFrameReceiver:
  """Socket I/O owns one latest JPEG slot; SDL stays on its caller's thread."""

  def __init__(self, sock, first_frame, frame_timeout, max_frames=None):
    self.sock = sock
    self.first_frame = first_frame
    self.frame_timeout = frame_timeout
    self.max_frames = max_frames
    self.condition = threading.Condition()
    self.pending = None
    self.closing = False
    self.done = False
    self.error = None
    self.received_frames = 0
    self.thread = threading.Thread(target=self._receive_loop, name="cluster-jpeg-receiver", daemon=True)
    self._perf_started = time.monotonic()
    self._reset_perf()

  def _reset_perf(self):
    self._perf_received = self._perf_displayed = self._perf_dropped = self._perf_bytes = 0
    self._perf_rx = dict.fromkeys(("header_wait", "receive", "ack_send"), 0.0)
    self._perf_display = dict.fromkeys(("display", "decode", "rotate", "scale", "blit", "flip"), 0.0)

  def _check_stopping(self):
    if self.closing:
      raise ConnectionError("Cluster stream receiver is stopping")

  def _receive_loop(self):
    frame = self.first_frame
    self.first_frame = None
    try:
      while self.max_frames is None or self.received_frames < self.max_frames:
        self._check_stopping()
        ack_started = time.monotonic()
        # Acknowledge a complete JPEG, independently of decode/rotation/flip.
        # The C4 window bounds in-flight bytes; replacing pending bounds latency.
        with self.condition:
          # Publish together with the tiny ACK so presentation (or a display
          # failure) cannot race acknowledgement of the complete JPEG.
          self.sock.sendall(pack_ack(frame.sequence, ACK_OK, ACK_STREAM_SUPPORTED))
          acknowledged_at = time.monotonic()
          if self.pending is not None:
            self._perf_dropped += 1
          self.pending = frame
          self.received_frames += 1
          self._perf_received += 1
          self._perf_bytes += len(frame.jpeg)
          self._perf_rx["header_wait"] += frame.header_at - frame.started
          self._perf_rx["receive"] += frame.received_at - frame.header_at
          self._perf_rx["ack_send"] += acknowledged_at - ack_started
          self.condition.notify_all()
        self._log_perf()
        if self.max_frames is not None and self.received_frames >= self.max_frames:
          return
        started = time.monotonic()
        deadline = started + self.frame_timeout
        sequence, size, flags = unpack_frame_header_info(
          recv_exact(self.sock, FRAME_HEADER.size, deadline=deadline, poll_events=self._check_stopping),
        )
        header_at = time.monotonic()
        if not flags & FRAME_STREAM:
          raise ValueError("Expected a streaming cluster frame")
        jpeg = recv_exact(self.sock, size, deadline=deadline, poll_events=self._check_stopping)
        frame = ReceivedFrame(sequence, jpeg, started, header_at, time.monotonic())
    except Exception as e:
      with self.condition:
        if not self.closing:
          self.error = e
    finally:
      with self.condition:
        self.done = True
        self.condition.notify_all()

  def take_frame(self):
    with self.condition:
      self.condition.wait_for(lambda: self.pending is not None or self.error is not None or self.done, timeout=0.05)
      if self.error is not None:
        raise self.error
      frame = self.pending
      self.pending = None
      return frame

  def record_display(self, elapsed, timings):
    with self.condition:
      self._perf_displayed += 1
      self._perf_display["display"] += elapsed
      for stage in self._perf_display:
        if stage != "display":
          self._perf_display[stage] += timings.get(stage, 0.0)
    self._log_perf()

  def _log_perf(self):
    with self.condition:
      now = time.monotonic()
      elapsed = now - self._perf_started
      if elapsed < 10.0:
        return
      received, displayed, dropped = self._perf_received, self._perf_displayed, self._perf_dropped
      size = self._perf_bytes / (1024 * max(received, 1))
      stages = [(name, duration / max(received, 1)) for name, duration in self._perf_rx.items()]
      stages += [(name, duration / max(displayed, 1)) for name, duration in self._perf_display.items()]
      self._perf_started = now
      self._reset_perf()
    detail = " | ".join(f"{stage}_avg={duration * 1000:.1f}ms" for stage, duration in stages)
    LOG.info("[CLUSTER_RX_PERF] fps=%.2f | display_fps=%.2f | dropped=%d | size_avg=%.1fKB | %s | mode=stream",
             received / elapsed, displayed / elapsed, dropped, size, detail)

  def close(self):
    with self.condition:
      self.closing = True
      self.pending = None
      self.condition.notify_all()
    if self.thread.is_alive():
      try:
        self.sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      self.thread.join(timeout=1.0)
      if self.thread.is_alive():
        LOG.warning("Cluster JPEG receiver thread did not stop within one second")


def receive_stream_frames(sock, display, first_frame, frame_timeout, max_frames=None):
  receiver = LatestFrameReceiver(sock, first_frame, frame_timeout, max_frames)
  LOG.info("[CLUSTER_RX_MODE] stream: ACK on receive, display latest JPEG")
  receiver.thread.start()
  try:
    while True:
      # SDL event processing, JPEG decode and presentation stay on this thread.
      if not display.pump_events():
        raise KeyboardInterrupt
      frame = receiver.take_frame()
      if frame is None:
        if receiver.done:
          return receiver.received_frames
        continue
      started = time.monotonic()
      if not display.send_jpeg(frame.jpeg):
        if not display.pump_events():
          raise KeyboardInterrupt
        raise RuntimeError("Unable to display cluster frame")
      receiver.record_display(time.monotonic() - started, getattr(display, "last_frame_timings", {}))
  finally:
    receiver.close()
