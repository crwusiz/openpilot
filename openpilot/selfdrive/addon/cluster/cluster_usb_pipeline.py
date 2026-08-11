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
    self._closing = False
    self.running = False
    self.thread = None
    flog("ClusterUsbPipeline initialized.")

  def start(self):
    with self._condition:
      if self.running:
        return
      self.running = True
      self._closing = False
      self._pending_frame = None
      self.thread = threading.Thread(
        target=self._worker_loop, name="cluster-usb-jpeg", daemon=True,
      )
      self.thread.start()
    flog("Turing USB Display Pipeline thread started.")

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

  def _take_pending(self):
    with self._condition:
      self._condition.wait_for(lambda: self._pending_frame is not None or self._closing)
      if self._closing:
        return None
      frame_image = self._pending_frame
      self._pending_frame = None
      return frame_image

  def _wait_for_reconnect(self, timeout):
    with self._condition:
      self._condition.wait_for(lambda: self._closing, timeout=timeout)

  def _is_closing(self):
    with self._condition:
      return self._closing

  def _worker_loop(self):
    while True:
      frame_image = self._take_pending()
      if frame_image is None:
        return
      try:
        if not self.display.connected:
          success = self.display.open()
          if not success:
            self._wait_for_reconnect(1.0)
            continue

        if self._is_closing():
          return
        self.display.send_image(frame_image)
      except Exception as e:
        flog(f"[CLUSTER_PIPELINE_ERROR] Worker exception: {e}")
        self.display.connected = False
        self._wait_for_reconnect(0.5)

  def close(self):
    with self._condition:
      self.running = False
      self._closing = True
      self._pending_frame = None
      self._condition.notify_all()

    stopped = self.thread is None
    if not stopped:
      # A USB transaction may remain in its device timeout while shutting down.
      self.thread.join(timeout=3.0)
      if self.thread.is_alive():
        flog("[CLUSTER_PIPELINE_WARN] USB pipeline did not stop within 3 seconds.")
      else:
        self.thread = None
        stopped = True
    if stopped:
      flog("Turing USB Display Pipeline thread stopped.")
    return stopped
