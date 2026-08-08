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
    # VisionIPC NV12 has aligned Y and UV planes; it is not one contiguous
    # height*1.5*stride image. This is the same plane layout used by camerad's
    # snapshot utility.
    height, width, stride = buffer.height, buffer.width, buffer.stride
    uv_height = ((height // 2) + 15) // 16 * 16
    uv_plane_size = stride * uv_height

    y = np.frombuffer(buffer.data[:buffer.uv_offset], dtype=np.uint8)
    y = y.reshape((-1, stride))[:height, :width]
    uv = memoryview(buffer.data)[buffer.uv_offset:buffer.uv_offset + uv_plane_size]
    u = np.array(uv[::2], dtype=np.uint8).reshape((-1, stride // 2))[:height // 2, :width // 2]
    v = np.array(uv[1::2], dtype=np.uint8).reshape((-1, stride // 2))[:height // 2, :width // 2]

    # OpenCV accepts planar I420. The original NV12 U/V samples are unpacked
    # above because their plane is stride-aligned.
    i420 = np.concatenate((y.reshape(-1), u.reshape(-1), v.reshape(-1)))
    return cv2.cvtColor(i420.reshape((height * 3 // 2, width)), cv2.COLOR_YUV2RGB_I420)

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
