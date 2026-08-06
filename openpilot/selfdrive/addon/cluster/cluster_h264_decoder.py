import av
from openpilot.common.swaglog import cloudlog


class ClusterH264Decoder:
  def __init__(self):
    cloudlog.info("Initializing PyAV H264 Decoder...")
    try:
      self.codec = av.CodecContext.create('h264', 'r')
    except Exception as e:
      cloudlog.error(f"Failed to create H264 codec: {e}")
      self.codec = None

  def process(self, data):
    if self.codec is None or not data:
      return None

    try:
      packets = self.codec.parse(data)

      latest_image = None
      for packet in packets:
        frames = self.codec.decode(packet)

        for frame in frames:
          latest_image = frame.to_ndarray(format='rgb24')

      return latest_image

    except Exception as e:
      cloudlog.error(f"Error decoding H264 frame: {e}")
      return None

  def close(self):
    cloudlog.info("Closing H264 Decoder.")
    if self.codec is not None:
      self.codec = None
