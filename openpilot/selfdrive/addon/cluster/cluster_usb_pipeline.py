import threading
import queue
import time
from openpilot.common.swaglog import cloudlog


class ClusterUsbPipeline:
  def __init__(self, display):
    self.display = display
    self.queue = queue.Queue(maxsize=1)
    self.running = False
    self.thread = None

  def start(self):
    if not self.running:
      self.running = True
      self.thread = threading.Thread(target=self._worker_loop, daemon=True)
      self.thread.start()
      cloudlog.info("Turing USB Display Pipeline thread started.")

  def push(self, frame_image):
    if not self.running or frame_image is None:
      return

    if self.queue.full():
      try:
        self.queue.get_nowait()
      except queue.Empty:
        pass

    # 새로운 최신 프레임 삽입
    try:
      self.queue.put_nowait(frame_image)
    except queue.Full:
      pass

  def _worker_loop(self):
    while self.running:
      try:
        frame_image = self.queue.get(timeout=0.1)

        if self.display.connected:
          self.display.send_image(frame_image)

      except queue.Empty:
        continue
      except Exception as e:
        cloudlog.error(f"Error in USB pipeline worker: {e}")
        time.sleep(0.1)

  def close(self):
    self.running = False
    if self.thread is not None:
      self.thread.join(timeout=1.0)
    cloudlog.info("Turing USB Display Pipeline thread stopped.")
