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
    # 큐 사이즈를 1로 제한하여 최신 프레임만 유지하고 지연 방지
    self.queue = queue.Queue(maxsize=1)
    self.running = False
    self.thread = None
    flog("ClusterUsbPipeline initialized.")

  def start(self):
    """
    백그라운드 전송 스레드를 시작합니다.
    """
    if not self.running:
      self.running = True
      self.thread = threading.Thread(target=self._worker_loop, daemon=True)
      self.thread.start()
      flog("Turing USB Display Pipeline thread started.")

  def push(self, frame_image):
    """
    메인 렌더러 루프에서 호출되어 전송할 이미지를 큐에 넣습니다.
    """
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
    백그라운드에서 큐를 감시하다가 이미지가 들어오면 USB로 전송합니다.
    스레드 멈춤(Blocking)을 방지하기 위한 안전장치가 포함되어 있습니다.
    """
    while self.running:
      try:
        # 1. 전송할 프레임 대기 (0.1초 타임아웃)
        frame_image = self.queue.get(timeout=0.1)

        # 2. 연결이 끊어져 있다면 자동 재연결 시도
        if not self.display.connected:
          success = self.display.open()
          if not success:
            time.sleep(1.0)
            continue

        # 3. 이미지 전송 수행
        self.display.send_image(frame_image)

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
