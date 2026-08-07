import av
from openpilot.common.swaglog import cloudlog


class ClusterH264Decoder:
  def __init__(self):
    cloudlog.info("Initializing PyAV H264 Decoder...")
    self.first_frame_decoded = False
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
        try:
          frames = self.codec.decode(packet)
          for frame in frames:
            latest_image = frame.to_ndarray(format='rgb24')

            if not self.first_frame_decoded:
              self.first_frame_decoded = True
              cloudlog.info("Success: First H264 Keyframe found and decoded!")

        except Exception as e:
          pass

      return latest_image

    except Exception as e:
      if self.first_frame_decoded:
        cloudlog.error(f"Error parsing H264 packet: {e}")
      return None

  def close(self):
    cloudlog.info("Closing H264 Decoder.")
    if self.codec is not None:
      self.codec = None
