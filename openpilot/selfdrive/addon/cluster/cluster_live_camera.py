import time
import threading

import cv2
import numpy as np
from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType


LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"


def flog(msg):
  try:
    with open(LOG_FILE, "a") as f:
      f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
  except Exception:
    pass


class ClusterLiveCamera:
  def __init__(self, config):
    self.config = config
    self.panel_width = config.camera_panel_width
    self.panel_height = config.height
    self.latest_frame = None
    self._frame_condition = threading.Condition()
    self._source_to_panel = np.eye(3, dtype=np.float32)
    self.running = False
    self.thread = None
    self.vipc = None
    self.frame_count = 0
    self._use_fast_yuv = hasattr(cv2, "cvtColorTwoPlane")
    self._perf_started = None
    self._perf_frames = 0
    self._perf_prepare_time = 0.0
    self._init_camera()
    self._start_thread()

  def _init_camera(self):
    try:
      flog("[CLUSTER_CAM] Connecting to raw VisionIPC road camera...")
      self.vipc = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_NARROW_ROAD, True)
    except Exception as e:
      self.vipc = None
      flog(f"[CLUSTER_CAM_ERROR] Failed to initialize VisionIPC camera: {e}")

  def _start_thread(self):
    self.running = True
    self.thread = threading.Thread(target=self._camera_thread, daemon=True)
    self.thread.start()

  @staticmethod
  def _rgb_from_vision_buffer(buffer):
    height, width, stride = buffer.height, buffer.width, buffer.stride
    uv_height = ((height // 2) + 15) // 16 * 16
    uv_plane_size = stride * uv_height

    y = np.frombuffer(buffer.data[:buffer.uv_offset], dtype=np.uint8)
    y = y.reshape((-1, stride))[:height, :width]
    uv = memoryview(buffer.data)[buffer.uv_offset:buffer.uv_offset + uv_plane_size]
    u = np.array(uv[::2], dtype=np.uint8).reshape((-1, stride // 2))[:height // 2, :width // 2]
    v = np.array(uv[1::2], dtype=np.uint8).reshape((-1, stride // 2))[:height // 2, :width // 2]

    i420 = np.concatenate((y.reshape(-1), u.reshape(-1), v.reshape(-1)))
    return cv2.cvtColor(i420.reshape((height * 3 // 2, width)), cv2.COLOR_YUV2RGB_I420)

  def _panel_rgb_from_vision_buffer(self, buffer):
    """Crop and scale NV12 before RGB conversion to avoid processing unused pixels."""
    height, width, stride = buffer.height, buffer.width, buffer.stride
    target_w, target_h = self.panel_width, self.panel_height

    source_aspect = width / height
    target_aspect = target_w / target_h
    if source_aspect < target_aspect:
      crop_w = width
      crop_h = min(height, int(round(width / target_aspect)))
    else:
      crop_w = min(width, int(round(height * target_aspect)))
      crop_h = height

    # NV12 chroma samples cover a 2x2 luma block, so all crop coordinates and
    # dimensions must be even.
    crop_w = max(2, crop_w & ~1)
    crop_h = max(2, crop_h & ~1)
    crop_x = max(0, ((width - crop_w) // 2) & ~1)
    crop_y = max(0, ((height - crop_h) // 2) & ~1)

    scale_x = target_w / crop_w
    scale_y = target_h / crop_h
    source_to_panel = np.array([
      [scale_x, 0.0, -crop_x * scale_x],
      [0.0, scale_y, -crop_y * scale_y],
      [0.0, 0.0, 1.0],
    ], dtype=np.float32)

    # VisionIPC provides NV12 with padded rows. Resize its luma and interleaved
    # chroma planes independently, then convert only the final 1152x462 image.
    # This replaces a full 1928x1208 RGB conversion and its temporary I420 copy.
    if self._use_fast_yuv:
      try:
        data = memoryview(buffer.data)
        uv_height = ((height // 2) + 15) // 16 * 16
        uv_plane_size = stride * uv_height
        y = np.frombuffer(data[:buffer.uv_offset], dtype=np.uint8)
        y = y.reshape((-1, stride))[:height, :width]
        uv = np.frombuffer(
          data[buffer.uv_offset:buffer.uv_offset + uv_plane_size],
          dtype=np.uint8,
        ).reshape((-1, stride // 2, 2))[:height // 2, :width // 2]

        y = cv2.resize(
          y[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w],
          (target_w, target_h), interpolation=cv2.INTER_LINEAR,
        )
        uv = cv2.resize(
          uv[crop_y // 2:(crop_y + crop_h) // 2, crop_x // 2:(crop_x + crop_w) // 2],
          (target_w // 2, target_h // 2), interpolation=cv2.INTER_LINEAR,
        )
        rgb = cv2.cvtColorTwoPlane(y, uv, cv2.COLOR_YUV2RGB_NV12)
        return rgb, source_to_panel
      except (AttributeError, cv2.error, ValueError) as e:
        self._use_fast_yuv = False
        flog(f"[CLUSTER_CAM_WARN] Fast NV12 conversion unavailable; using RGB fallback: {e}")

    # Compatibility path for OpenCV builds without cvtColorTwoPlane.
    rgb = self._rgb_from_vision_buffer(buffer)
    cropped = rgb[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
    return cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR), source_to_panel

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

        prepare_started = time.monotonic()
        frame, source_to_panel = self._panel_rgb_from_vision_buffer(buffer)
        prepare_elapsed = time.monotonic() - prepare_started

        with self._frame_condition:
          self._source_to_panel = source_to_panel
          self.latest_frame = frame
          self.frame_count += 1
          self._frame_condition.notify_all()
        now = time.monotonic()
        if self._perf_started is None:
          self._perf_started = prepare_started
        self._perf_frames += 1
        self._perf_prepare_time += prepare_elapsed
        error_count = 0
        if self.frame_count == 1:
          flog(f"[CLUSTER_CAM_SUCCESS] First optimized road frame: source={buffer.width}x{buffer.height}, "
               f"panel={self.latest_frame.shape[1]}x{self.latest_frame.shape[0]}")
        if self._perf_frames >= self.config.fps * 10:
          elapsed = max(now - self._perf_started, 1e-6)
          flog(f"[CLUSTER_CAM_PERF] fps={self._perf_frames / elapsed:.2f} | "
               f"prepare_avg={self._perf_prepare_time * 1000 / self._perf_frames:.1f}ms")
          self._perf_started = now
          self._perf_frames = 0
          self._perf_prepare_time = 0.0

      except Exception as e:
        error_count += 1
        flog(f"[CLUSTER_CAM_ERROR] VisionIPC camera error ({error_count}): {e}")
        self.vipc = None
        time.sleep(0.5)

  def has_frame(self):
    return self.latest_frame is not None

  def get_frame(self):
    return self.latest_frame

  def wait_for_frame(self, previous_frame_count, timeout):
    """Wait for a fresh camera frame so the render loop does not resample 20 Hz video out of phase."""
    with self._frame_condition:
      self._frame_condition.wait_for(
        lambda: not self.running or self.frame_count != previous_frame_count,
        timeout=timeout,
      )
      return self.frame_count

  def get_source_to_panel_transform(self, panel_x=0):
    transform = self._source_to_panel.copy()
    transform[0, 2] += panel_x
    return transform

  def close(self):
    with self._frame_condition:
      self.running = False
      self._frame_condition.notify_all()
    if self.thread is not None:
      self.thread.join(timeout=1.0)
    self.vipc = None
