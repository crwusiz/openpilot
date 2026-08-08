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
    try:
      self.codec = av.CodecContext.create('h264', 'r')
    except Exception as e:
      flog(f"[CLUSTER_DECODER_ERROR] Failed to create H264 codec: {e}")
      self.codec = None

  def process(self, data):
    if self.codec is None or not data:
      return None

    self.call_count += 1
    if self.call_count <= 10:
      flog(f"[CLUSTER_DECODER] process() called #{self.call_count} | data_len={len(data)} bytes")

    try:
      packets = self.codec.parse(data)
      latest_image = None

      packet_list = list(packets)
      if self.call_count <= 10:
        flog(f"[CLUSTER_DECODER] parse() -> {len(packet_list)} packet(s)")

      for packet in packet_list:
        try:
          frames = self.codec.decode(packet)
          frame_list = list(frames)
          if self.call_count <= 10:
            flog(f"[CLUSTER_DECODER] decode() -> {len(frame_list)} frame(s)")

          for frame in frame_list:
            latest_image = frame.to_ndarray(format='rgb24')

            if not self.first_frame_decoded:
              self.first_frame_decoded = True
              flog(f"[CLUSTER_DECODER_SUCCESS] First H264 frame decoded! shape={latest_image.shape}")

        except Exception as e:
          self.decode_error_count += 1
          if self.decode_error_count <= 10:
            flog(f"[CLUSTER_DECODER_ERROR] decode() failed ({self.decode_error_count}): {e}")

      return latest_image

    except Exception as e:
      self.parse_error_count += 1
      if self.parse_error_count <= 10:
        flog(f"[CLUSTER_DECODER_ERROR] parse() failed ({self.parse_error_count}): {e}")
      return None

  def close(self):
    cloudlog.info("Closing H264 Decoder.")
    if self.codec is not None:
      self.codec = None
