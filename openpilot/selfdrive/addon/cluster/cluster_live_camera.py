import time
import threading
from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_h264_decoder import ClusterH264Decoder

LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"

def flog(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except:
        pass


class ClusterLiveCamera:
  def __init__(self, config):
    self.config = config
    self.latest_frame = None
    self.running = False
    self.thread = None
    self.last_frame_time = time.time()
    self.msg_count = 0

    self.sock = None
    self.decoder = None
    self._init_socket()

    self._start_thread()

  def _init_socket(self):
    try:
      flog("[CLUSTER_CAM] Connecting to roadEncodeData socket...")
      self.sock = messaging.sub_sock('roadEncodeData', conflate=True)
      if self.decoder is None:
        self.decoder = ClusterH264Decoder()
    except Exception as e:
      flog(f"[CLUSTER_CAM_ERROR] Failed to initialize camera socket/decoder: {e}")

  def _start_thread(self):
    self.running = True
    self.thread = threading.Thread(target=self._camera_thread, daemon=True)
    self.thread.start()

  def _camera_thread(self):
    error_count = 0
    while self.running:
      try:
        if self.sock is None:
          self._init_socket()
          time.sleep(1.0)
          continue

        msg = messaging.recv_one_or_none(self.sock)

        if msg is not None:
          self.msg_count += 1
          error_count = 0

          which = msg.which()
          if self.msg_count <= 10:
            flog(f"[CLUSTER_CAM] Got msg #{self.msg_count} | which={which}")

          encode_data = getattr(msg, which)
          frame_data = encode_data.header + encode_data.data

          if self.msg_count <= 10:
            flog(f"[CLUSTER_CAM] frame_data len={len(frame_data)} "
                 f"(header={len(encode_data.header)}, data={len(encode_data.data)})")

          if frame_data and self.decoder:
            rgb_frame = self.decoder.process(frame_data)
            if rgb_frame is not None:
              self.latest_frame = rgb_frame
              self.last_frame_time = time.time()
        else:
          # 3초 이상 프레임이 안 들어오면 소켓 재연결 시도
          if time.time() - self.last_frame_time > 3.0:
            flog("[CLUSTER_CAM_WARN] Camera stream timeout (no msg for 3s), re-initializing socket...")
            self.sock = messaging.sub_sock('roadEncodeData', conflate=True)
            self.last_frame_time = time.time()
          else:
            time.sleep(0.005)
      except Exception as e:
        error_count += 1
        flog(f"[CLUSTER_CAM_ERROR] Camera thread error ({error_count}): {e}")
        time.sleep(0.5)
        if error_count > 10:
          try:
            self._init_socket()
          except:
            pass
          error_count = 0

  def update(self):
    pass

  def has_frame(self):
    return self.latest_frame is not None

  def get_frame(self):
    return self.latest_frame

  def close(self):
    self.running = False
    if self.thread is not None:
      self.thread.join(timeout=1.0)
    if self.decoder:
      self.decoder.close()
