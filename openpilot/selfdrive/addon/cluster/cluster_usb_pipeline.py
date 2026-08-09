import threading
import queue
import time
from openpilot.common.swaglog import cloudlog

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
    self.queue = queue.Queue(maxsize=1)
    self.running = False
    self.thread = None
    flog("ClusterUsbPipeline initialized.")

  def start(self):
    if not self.running:
      self.running = True
      self.thread = threading.Thread(target=self._worker_loop, daemon=True)
      self.thread.start()
      flog("Turing USB Display Pipeline thread started.")

  def push(self, frame_image):
    if not self.running or frame_image is None:
      return

    if self.queue.full():
      try:
        self.queue.get_nowait()
      except queue.Empty:
        pass

    try:
      self.queue.put_nowait(frame_image)
    except queue.Full:
      pass

  def _worker_loop(self):
    """
    백그라운드에서 큐를 감시하며 이미지를 전송합니다.
    [가장 중요한 로직]: USB 타임아웃에 의존하지 않고, 전송 후 일정 시간을
    강제로 쉬어(Breathing room) MCU 버퍼가 넘치는 것을 원천 차단합니다.
    """
    fps = getattr(self.display.config, 'usb_fps', getattr(self.display.config, 'fps', 10))
    target_interval = 1.0 / fps if fps > 0 else 0.1

    while self.running:
      try:
        frame_image = self.queue.get(timeout=0.1)

        if not self.display.connected:
          success = self.display.open()
          if not success:
            time.sleep(1.0)
            continue

        # 전송 소요 시간 측정
        t0 = time.time()
        self.display.send_image(frame_image)
        t1 = time.time()

        elapsed = t1 - t0
        sleep_time = target_interval - elapsed

        # [핵심] 전송이 아무리 늦게 끝났어도, 최소 30ms(0.03초)는 무조건 휴식하여
        # 디스플레이 MCU가 받은 데이터를 압축해제 할 시간을 벌어줍니다.
        if sleep_time < 0.03:
          sleep_time = 0.03

        time.sleep(sleep_time)

      except queue.Empty:
        continue
      except Exception as e:
        flog(f"[CLUSTER_PIPELINE_ERROR] Worker exception: {e}")
        self.display.connected = False
        time.sleep(0.5)

  def close(self):
    self.running = False
    if self.thread is not None:
      self.thread.join(timeout=1.0)
    flog("Turing USB Display Pipeline thread stopped.")
