import time
import threading
from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_h264_decoder import ClusterH264Decoder

LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"

# v4l2 버퍼 플래그: 키프레임(IDR) 여부 (linux/videodev2.h)
V4L2_BUF_FLAG_KEYFRAME = 0x8

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
    self.last_msg_time = time.time()   # 메시지가 마지막으로 온 시각 (재연결 판단용)
    self.msg_count = 0
    self.seen_keyframe = False         # 첫 키프레임을 만났는지 여부

    self.sock = None
    self.decoder = None
    self._init_socket()

    self._start_thread()

  def _init_socket(self):
    try:
      flog("[CLUSTER_CAM] Connecting to roadEncodeData socket...")
      # conflate=True는 최신 메시지 1개만 남기고 나머지를 버리는데, 이러면 H264 GOP
      # 연속성(키프레임 -> 델타프레임 체인)이 깨져서 디코딩이 거의 항상 실패함.
      # 반드시 conflate=False로 순서대로 다 받아야 함.
      self.sock = messaging.sub_sock('roadEncodeData', conflate=False)
      if self.decoder is None:
        self.decoder = ClusterH264Decoder()
      self.seen_keyframe = False
      self.last_msg_time = time.time()
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

        # 쌓여있는 프레임을 전부 순서대로 소진 (하나만 받으면 GOP 연속성이 깨짐)
        got_any = False
        while True:
          msg = messaging.recv_one_or_none(self.sock)
          if msg is None:
            break
          got_any = True
          self.last_msg_time = time.time()

          self.msg_count += 1
          error_count = 0

          which = msg.which()
          encode_data = getattr(msg, which)
          is_keyframe = bool(encode_data.idx.flags & V4L2_BUF_FLAG_KEYFRAME)

          if self.msg_count <= 20:
            flog(f"[CLUSTER_CAM] Got msg #{self.msg_count} | keyframe={is_keyframe} | "
                 f"seen_keyframe={self.seen_keyframe} | header_len={len(encode_data.header)} | "
                 f"data_len={len(encode_data.data)}")

          # 첫 키프레임을 만나기 전까지는 디코딩 시도 자체를 건너뜀
          # (키프레임 없이 델타프레임만 넣으면 디코더가 계속 실패함)
          if not self.seen_keyframe:
            if is_keyframe:
              self.seen_keyframe = True
              flog("[CLUSTER_CAM_SUCCESS] First keyframe found! Starting decode from here.")
            else:
              continue  # 키프레임 나올 때까지 계속 버림

          frame_data = encode_data.header + encode_data.data

          if frame_data and self.decoder:
            rgb_frame = self.decoder.process(frame_data)
            if rgb_frame is not None:
              self.latest_frame = rgb_frame

        if not got_any:
          # 5초 이상 메시지 자체가 안 들어오면 소켓 재연결 시도
          # (디코딩 실패와 무관하게 '메시지 수신' 기준으로만 판단)
          if time.time() - self.last_msg_time > 5.0:
            flog("[CLUSTER_CAM_WARN] No message for 5s, re-initializing socket...")
            self._init_socket()
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
