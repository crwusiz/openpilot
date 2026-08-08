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
          header_len = len(encode_data.header)

          if self.msg_count <= 20:
            flog(f"[CLUSTER_CAM] Got msg #{self.msg_count} | keyframe={is_keyframe} | "
                 f"seen_keyframe={self.seen_keyframe} | header_len={header_len} | "
                 f"data_len={len(encode_data.data)}")
          elif header_len > 0:
            # 카운트 제한과 무관하게, header가 실린 메시지는 절대 놓치지 않고 로그
            flog(f"[CLUSTER_CAM_HEADER_FOUND] msg #{self.msg_count} | keyframe={is_keyframe} | "
                 f"header_len={header_len} | data_len={len(encode_data.data)}")

          # 첫 키프레임을 만나기 전까지는 디코딩 시도 자체를 건너뜀
          # (키프레임 없이 델타프레임만 넣으면 디코더가 계속 실패함)
          use_header_as_extradata = False
          if not self.seen_keyframe:
            if is_keyframe:
              self.seen_keyframe = True
              head_hex = encode_data.header.hex()  # 전체 header 덤프 (90바이트라 짧음)
              data_head_hex = encode_data.data[:16].hex()

              header_bytes = bytes(encode_data.header)
              is_annexb_header = header_bytes[:4] == b'\x00\x00\x00\x01' or header_bytes[:3] == b'\x00\x00\x01'

              flog(f"[CLUSTER_CAM_SUCCESS] First keyframe found! header_len={header_len} | "
                   f"is_annexb_header={is_annexb_header}\n"
                   f"    header (hex)={head_hex}\n"
                   f"    data first 16 bytes (hex)={data_head_hex}")

              if not is_annexb_header and header_bytes:
                # AVCC(avcC) 스타일 extradata로 판단 -> 이어붙이지 않고 codec.extradata로 별도 설정
                self.decoder.set_extradata(header_bytes)
                use_header_as_extradata = True
            else:
              continue  # 키프레임 나올 때까지 계속 버림
          elif self.decoder.extradata_set:
            # extradata를 이미 설정했으면, 매 키프레임마다 반복되는 header는 다시 이어붙이지 않음
            use_header_as_extradata = True

          if use_header_as_extradata:
            frame_data = encode_data.data
          else:
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
