import time
import threading

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog

try:
  from openpilot.selfdrive.addon.cluster.cluster_h264_decoder import ClusterH264Decoder
except ImportError:
  cloudlog.error("ClusterH264Decoder import failed. Check cluster_h264_decoder.py")
  ClusterH264Decoder = None


class ClusterLiveCamera:
  def __init__(self, config):
    self.config = config
    self.latest_frame = None
    self.running = False
    self.thread = None

    cloudlog.info("Connecting to roadEncodeData socket...")
    self.sock = messaging.sub_sock("roadEncodeData", conflate=True)

    if ClusterH264Decoder is not None:
      self.decoder = ClusterH264Decoder()
      self._start_thread()
      cloudlog.info("ClusterLiveCamera initialized successfully.")
    else:
      self.decoder = None

  def _start_thread(self):
    self.running = True
    self.thread = threading.Thread(target=self._camera_thread, daemon=True)
    self.thread.start()

  def _camera_thread(self):
    while self.running:
      msg = messaging.recv_one_or_none(self.sock)

      if msg is not None:
        encode_data = getattr(msg, msg.which())
        data = encode_data.data
        if data:
          frame = self.decoder.process(data)
          if frame is not None:
            self.latest_frame = frame
      else:
        time.sleep(0.01)

  def update(self):
    pass

  def has_frame(self):
    return self.latest_frame is not None

  def get_frame(self):
    return self.latest_frame

  def close(self):
    cloudlog.info("Closing ClusterLiveCamera...")
    self.running = False
    if self.thread is not None:
      self.thread.join(timeout=1.0)
    if hasattr(self.decoder, 'close'):
      self.decoder.close()
