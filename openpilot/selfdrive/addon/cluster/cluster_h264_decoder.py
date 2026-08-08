import time
import av
from openpilot.common.swaglog import cloudlog

LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"

def flog(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except:
        pass


class ClusterH264Decoder:
  def __init__(self):
    cloudlog.info("Initializing PyAV H264 Decoder...")
    self.first_frame_decoded = False
    self.call_count = 0
    self.parse_error_count = 0
    self.decode_error_count = 0
    self.decode_ok_count = 0
    self.extradata_set = False
    try:
      self.codec = av.CodecContext.create('h264', 'r')
    except Exception as e:
      flog(f"[CLUSTER_DECODER_ERROR] Failed to create H264 codec: {e}")
      self.codec = None

  def set_extradata(self, header_bytes: bytes):
    """AVCC 스타일 extradata(header)를 코덱에 직접 설정 (Annex-B 스타트코드로 이어붙일 수 없는 경우)."""
    if self.codec is None:
      return
    try:
      self.codec.extradata = header_bytes
      self.extradata_set = True
      flog(f"[CLUSTER_DECODER] extradata set ({len(header_bytes)} bytes)")
    except Exception as e:
      flog(f"[CLUSTER_DECODER_ERROR] Failed to set extradata: {e}")

  def process(self, data):
    if self.codec is None or not data:
      return None

    self.call_count += 1
    verbose = self.call_count <= 30

    try:
      packets = self.codec.parse(data)
      latest_image = None
      packet_list = list(packets)

      for packet in packet_list:
        try:
          frames = self.codec.decode(packet)
          frame_list = list(frames)

          for frame in frame_list:
            latest_image = frame.to_ndarray(format='rgb24')
            self.decode_ok_count += 1

            if not self.first_frame_decoded:
              self.first_frame_decoded = True
              flog(f"[CLUSTER_DECODER_SUCCESS] First H264 frame decoded! shape={latest_image.shape}")

          if verbose and not frame_list:
            flog(f"[CLUSTER_DECODER] call#{self.call_count} decode() -> 0 frames (buffering?)")

        except Exception as e:
          self.decode_error_count += 1
          if verbose or self.decode_error_count % 30 == 0:
            flog(f"[CLUSTER_DECODER_ERROR] call#{self.call_count} decode() failed "
                 f"(total_fail={self.decode_error_count}, total_ok={self.decode_ok_count}): {e}")

      return latest_image

    except Exception as e:
      self.parse_error_count += 1
      if verbose:
        flog(f"[CLUSTER_DECODER_ERROR] call#{self.call_count} parse() failed ({self.parse_error_count}): {e}")
      return None

  def close(self):
    cloudlog.info("Closing H264 Decoder.")
    if self.codec is not None:
      self.codec = None
