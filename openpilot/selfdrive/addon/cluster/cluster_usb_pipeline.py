import threading
import time

LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"


def flog(msg):
  try:
    with open(LOG_FILE, "a") as f:
      f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
  except:
    pass


class ClusterUsbPipeline:
  def __init__(self, display):
    self.display = display
    self._condition = threading.Condition()
    self._pending_frame = None
    self._pending_prepared = None
    self._closing = False
    self.running = False
    self.encoder_thread = None
    self.sender_thread = None
    flog("ClusterUsbPipeline initialized.")

  def start(self):
    with self._condition:
      if self.running:
        return
      self.running = True
      self._closing = False
      self._pending_frame = None
      self._pending_prepared = None
      self.encoder_thread = threading.Thread(
        target=self._encoder_loop, name="cluster-jpeg-encoder", daemon=True,
      )
      self.sender_thread = threading.Thread(
        target=self._sender_loop, name="cluster-usb-sender", daemon=True,
      )
      self.encoder_thread.start()
      self.sender_thread.start()
    flog("Turing USB Display Pipeline threads started.")

  def push(self, frame_image):
    if frame_image is None:
      return

    # Keep exactly one pending frame and replace it atomically. If USB falls
    # behind, this minimizes display latency instead of replaying stale frames.
    with self._condition:
      if not self.running or self._closing:
        return
      self._pending_frame = frame_image
      self._condition.notify()

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
      # Encoding and USB each have a latest-only slot. This bounds memory and
      # prevents a slow/reconnecting display from replaying stale frames.
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
      self._condition.notify_all()

  def _wait_for_reconnect(self, timeout):
    with self._condition:
      self._condition.wait_for(lambda: self._closing, timeout=timeout)

  def _is_closing(self):
    with self._condition:
      return self._closing

  def _encoder_loop(self):
    while True:
      frame_image = self._take_pending_frame()
      if frame_image is None:
        return
      try:
        prepared = self.display.prepare_image(frame_image)
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
        # Reconnection can take seconds. Prefer an image encoded while it was
        # in progress over the stale image that triggered reconnection.
        prepared = self._replace_with_latest_prepared(prepared)
        if prepared is not None:
          self.display.send_prepared(prepared)
      except Exception as e:
        flog(f"[CLUSTER_PIPELINE_ERROR] Sender exception: {e}")
        if hasattr(self.display, 'close'):
          self.display.close()
        else:
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
      flog("Turing USB Display Pipeline threads stopped.")
    return stopped
