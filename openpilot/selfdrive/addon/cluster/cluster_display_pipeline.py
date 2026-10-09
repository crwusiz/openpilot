import threading
import time

from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.cluster_jpeg import PreparedFrame


class ClusterDisplayPipeline:
  def __init__(self, display):
    self.display = display
    self._condition = threading.Condition()
    self._pending_frame = None
    self._pending_prepared = None
    self._closing = False
    self.running = False
    self.encoder_thread = None
    self.sender_thread = None
    self._reset_stats()
    flog("ClusterDisplayPipeline initialized.")

  def _reset_stats(self):
    self._input_frames = 0
    self._encoded_frames = 0
    self._sent_frames = 0
    self._dropped_raw_frames = 0
    self._dropped_prepared_frames = 0
    self._dropped_stale_frames = 0
    self._send_failures = 0

  def start(self):
    with self._condition:
      if self.running:
        return
      self.running = True
      self._closing = False
      self._pending_frame = None
      self._pending_prepared = None
      self._reset_stats()
      self.encoder_thread = threading.Thread(
        target=self._encoder_loop, name="cluster-jpeg-encoder", daemon=True,
      )
      self.sender_thread = threading.Thread(
        target=self._sender_loop, name="cluster-display-sender", daemon=True,
      )
      self.encoder_thread.start()
      self.sender_thread.start()
    flog("Cluster display pipeline threads started.")

  def push(self, frame_image, created_at=None):
    if frame_image is None:
      return

    # Keep exactly one pending frame and replace it atomically. If USB falls
    # behind, this minimizes display latency instead of replaying stale frames.
    with self._condition:
      if not self.running or self._closing:
        return
      self._input_frames += 1
      if self._pending_frame is not None:
        self._dropped_raw_frames += 1
      self._pending_frame = (frame_image, time.monotonic() if created_at is None else created_at)
      # Encoder and sender wait on different predicates of this condition.
      # Waking only the sender can leave a raw frame stuck until the next push.
      self._condition.notify_all()

  def has_render_capacity(self):
    """Avoid composing/encoding frames that a blocked transport would discard."""
    with self._condition:
      if not self.running or self._closing or self._pending_frame is not None or self._pending_prepared is not None:
        return False
    capacity = getattr(self.display, "has_send_capacity", None)
    return not callable(capacity) or capacity()

  def _take_pending_frame(self):
    with self._condition:
      self._condition.wait_for(lambda: self._pending_frame is not None or self._closing)
      if self._closing:
        return None
      frame_image = self._pending_frame
      self._pending_frame = None
      return frame_image

  def _publish_prepared(self, prepared):
    with self._condition:
      if self._closing:
        return
      # Encoding and transport each have a latest-only slot. This bounds memory and
      # prevents a slow/reconnecting display from replaying stale frames.
      self._encoded_frames += 1
      if self._pending_prepared is not None:
        self._dropped_prepared_frames += 1
      self._pending_prepared = prepared
      self._condition.notify_all()

  def _take_prepared(self):
    with self._condition:
      self._condition.wait_for(lambda: self._pending_prepared is not None or self._closing)
      if self._closing:
        return None
      prepared = self._pending_prepared
      self._pending_prepared = None
      return prepared

  def _replace_with_latest_prepared(self, prepared):
    with self._condition:
      if self._closing:
        return None
      if self._pending_prepared is not None:
        self._dropped_prepared_frames += 1
        prepared = self._pending_prepared
        self._pending_prepared = None
      return prepared

  def _requeue_prepared_if_empty(self, prepared):
    with self._condition:
      if self._closing:
        return
      # Never overwrite a frame encoded while reconnection was in progress.
      if self._pending_prepared is None:
        self._pending_prepared = prepared
      else:
        self._dropped_prepared_frames += 1
      self._condition.notify_all()

  def get_stats(self):
    with self._condition:
      confirmed_count = getattr(self.display, "get_confirmed_frame_count", None)
      sent = confirmed_count() if callable(confirmed_count) else self._sent_frames
      return {
        "input": self._input_frames,
        "encoded": self._encoded_frames,
        "sent": sent,
        "dropped_raw": self._dropped_raw_frames,
        "dropped_prepared": self._dropped_prepared_frames,
        "dropped_stale": self._dropped_stale_frames,
        "send_failures": self._send_failures,
      }

  def _wait_for_reconnect(self, timeout):
    with self._condition:
      self._condition.wait_for(lambda: self._closing, timeout=timeout)

  def _is_closing(self):
    with self._condition:
      return self._closing

  def _encoder_loop(self):
    while True:
      queued_frame = self._take_pending_frame()
      if queued_frame is None:
        return
      frame_image, created_at = queued_frame
      try:
        prepared = self.display.prepare_image(frame_image)
        if isinstance(prepared, PreparedFrame):
          prepared = prepared._replace(created_at=created_at)
        if prepared is not None:
          self._publish_prepared(prepared)
      except Exception as e:
        flog(f"[CLUSTER_PIPELINE_ERROR] Encoder exception: {e}")

  def _sender_loop(self):
    while True:
      prepared = self._take_prepared()
      if prepared is None:
        return
      try:
        if not self.display.connected:
          success = self.display.open()
          if not success:
            self._wait_for_reconnect(1.0)
            self._requeue_prepared_if_empty(prepared)
            continue

        if self._is_closing():
          return
        # Keep untransmitted JPEGs replaceable while TCP credits are exhausted.
        # Waiting inside send_prepared would pin an old image before a newer
        # one can be selected when the next receive ACK releases the window.
        wait_capacity = getattr(self.display, "wait_for_send_capacity", None)
        if callable(wait_capacity) and not wait_capacity():
          self._requeue_prepared_if_empty(prepared)
          continue
        if self._is_closing():
          return
        # Reconnection and credit waits can take seconds. Select latest after
        # either wait; an expired frame is an intentional drop, not a failure.
        prepared = self._replace_with_latest_prepared(prepared)
        if prepared is not None:
          is_stale = getattr(self.display, "is_prepared_stale", None)
          if callable(is_stale) and is_stale(prepared):
            record_drop = getattr(self.display, "record_stale_drop", None)
            if callable(record_drop):
              record_drop()
            with self._condition:
              self._dropped_prepared_frames += 1
              self._dropped_stale_frames += 1
            continue
          success = self.display.send_prepared(prepared)
          with self._condition:
            if success is None:
              self._dropped_prepared_frames += 1
              self._dropped_stale_frames += 1
            elif success:
              self._sent_frames += 1
            else:
              self._send_failures += 1
      except Exception as e:
        with self._condition:
          self._send_failures += 1
        flog(f"[CLUSTER_PIPELINE_ERROR] Sender exception: {e}")
        try:
          if hasattr(self.display, "close"):
            self.display.close()
          else:
            self.display.connected = False
        except Exception as close_error:
          flog(f"[CLUSTER_PIPELINE_ERROR] Display close exception: {close_error}")
          self.display.connected = False
        self._wait_for_reconnect(0.5)

  def close(self):
    with self._condition:
      self.running = False
      self._closing = True
      self._pending_frame = None
      self._pending_prepared = None
      self._condition.notify_all()

    threads = [thread for thread in (self.encoder_thread, self.sender_thread) if thread is not None]
    deadline = time.monotonic() + 3.0
    for thread in threads:
      thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [thread.name for thread in threads if thread.is_alive()]
    stopped = not alive
    if alive:
      flog(f"[CLUSTER_PIPELINE_WARN] Pipeline threads did not stop within 3 seconds: {', '.join(alive)}")
    else:
      self.encoder_thread = None
      self.sender_thread = None
    if stopped:
      flog("Cluster display pipeline threads stopped.")
    return stopped
