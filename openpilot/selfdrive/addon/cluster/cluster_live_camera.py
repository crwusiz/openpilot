import time
import threading

import cv2
import numpy as np
from msgq.visionipc import VisionIpcClient, VisionStreamType


LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"


def flog(msg):
  try:
    with open(LOG_FILE, "a") as f:
      f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
  except Exception:
    pass


class ClusterLiveCamera:
  """Latest raw road frame, using the same source and conflation policy as UI."""

  def __init__(self, config):
    self.config = config
    self.latest_frame = None
    self.running = False
    self.thread = None
    self.vipc = None
    self.frame_count = 0
    self._init_camera()
    self._start_thread()

  def _init_camera(self):
    try:
      flog("[CLUSTER_CAM] Connecting to raw VisionIPC road camera...")
      self.vipc = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
    except Exception as e:
      self.vipc = None
      flog(f"[CLUSTER_CAM_ERROR] Failed to initialize VisionIPC camera: {e}")

  def _start_thread(self):
    self.running = True
    self.thread = threading.Thread(target=self._camera_thread, daemon=True)
    self.thread.start()

  @staticmethod
  def _rgb_from_vision_buffer(buffer):
    # VisionIPC road frames are NV12. Keep the stride while converting, then
    # crop only padding columns; this matches the UI's raw camera source.
    height, width, stride = buffer.height, buffer.width, buffer.stride
    nv12 = np.frombuffer(buffer.data, dtype=np.uint8).reshape((height * 3 // 2, stride))
    return cv2.cvtColor(nv12, cv2.COLOR_YUV2RGB_NV12)[:, :width]

  def _camera_thread(self):
    error_count = 0
    while self.running:
      try:
        if self.vipc is None:
          self._init_camera()
          time.sleep(1.0)
          continue

        if not self.vipc.is_connected():
          if not self.vipc.connect(False) or not self.vipc.num_buffers:
            time.sleep(0.1)
            continue
          flog(f"[CLUSTER_CAM_SUCCESS] VisionIPC connected: "
               f"{self.vipc.width}x{self.vipc.height}, stride={self.vipc.stride}")

        buffer = self.vipc.recv(timeout_ms=50)
        if buffer is None:
          continue

        self.latest_frame = self._rgb_from_vision_buffer(buffer)
        self.frame_count += 1
        error_count = 0
        if self.frame_count == 1:
          flog(f"[CLUSTER_CAM_SUCCESS] First raw road frame: shape={self.latest_frame.shape}")

      except Exception as e:
        error_count += 1
        flog(f"[CLUSTER_CAM_ERROR] VisionIPC camera error ({error_count}): {e}")
        self.vipc = None
        time.sleep(0.5)

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
    self.vipc = None
