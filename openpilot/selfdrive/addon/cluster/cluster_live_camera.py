import time
import threading
from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_h264_decoder import ClusterH264Decoder


class ClusterLiveCamera:
  def __init__(self, config):
    self.config = config
    self.latest_frame = None
    self.running = False
    self.thread = None

    cloudlog.info("Connecting to roadEncodeData socket...")
    self.sock = messaging.sub_sock('roadEncodeData', conflate=True)
    self.decoder = ClusterH264Decoder()

    self._start_thread()

  def _start_thread(self):
    self.running = True
    self.thread = threading.Thread(target=self._camera_thread, daemon=True)
    self.thread.start()

  def _camera_thread(self):
    while self.running:
      msg = messaging.recv_one_or_none(self.sock)

      if msg is not None:
        encode_data = getattr(msg, msg.which())
        frame_data = encode_data.header + encode_data.data

        if frame_data and self.decoder:
          rgb_frame = self.decoder.process(frame_data)
          if rgb_frame is not None:
            self.latest_frame = rgb_frame
      else:
        time.sleep(0.005)

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
