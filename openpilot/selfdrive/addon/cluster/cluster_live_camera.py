import time
import threading

import cv2
import numpy as np
from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType

from openpilot.selfdrive.addon.cluster.cluster_logging import flog

CONNECTION_RETRY_SECONDS = 0.5
FRAME_STALE_SECONDS = 1.2

class ClusterLiveCamera:
  def __init__(self, config):
    self.config = config
    self.panel_width = config.camera_panel_width
    self.panel_height = getattr(config, "camera_panel_height", config.height)
    self.latest_frame = None
    self._frame_condition = threading.Condition()
    self._source_to_panel = np.eye(3, dtype=np.float32)
    self.running = False
    self.thread = None
    self.vipc = None
    self.frame_count = 0
    self._use_fast_yuv = hasattr(cv2, "cvtColorTwoPlane")
    contrast = min(max(float(getattr(config, "camera_contrast", 1.08)), 0.5), 2.0)
    self.camera_contrast = contrast
    self._contrast_lut = None
    if abs(contrast - 1.0) > 1e-3:
      values = np.arange(256, dtype=np.float32)
      self._contrast_lut = np.clip((values - 127.5) * contrast + 127.5, 0, 255).astype(np.uint8)
    self._last_connection_attempt = 0.0
    self._connected_at = 0.0
    self._last_frame_at = 0.0
    self._connection_wait_logged = False
    self._perf_started = None
    self._perf_frames = 0
    self._perf_prepare_time = 0.0
    self._init_camera()
    self._start_thread()

  def _init_camera(self):
    try:
      if not self._connection_wait_logged:
        flog("[CLUSTER_CAM] Connecting to raw VisionIPC road camera...")
        self._connection_wait_logged = True
      self.vipc = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_NARROW_ROAD, True)
    except Exception as e:
      self.vipc = None
      flog(f"[CLUSTER_CAM_ERROR] Failed to initialize VisionIPC camera: {e}")

  def _start_thread(self):
    self.running = True
    self.thread = threading.Thread(
      target=self._camera_thread, name="cluster-live-camera", daemon=True,
    )
    self.thread.start()

  def _wait_or_stopping(self, timeout):
    with self._frame_condition:
      self._frame_condition.wait_for(lambda: not self.running, timeout=timeout)

  def _reset_camera(self, reason=None):
    if reason:
      flog(f"[CLUSTER_CAM_WARN] {reason}; reconnecting VisionIPC.")
    self.vipc = None
    self._connected_at = 0.0
    self._last_frame_at = 0.0
    self._last_connection_attempt = 0.0
    self._connection_wait_logged = False
    with self._frame_condition:
      self.latest_frame = None
      self._frame_condition.notify_all()

  def _apply_camera_contrast(self, frame):
    if self._contrast_lut is not None:
      cv2.LUT(frame, self._contrast_lut, dst=frame)
    return frame

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
        return self._apply_camera_contrast(rgb), source_to_panel
      except (AttributeError, cv2.error, ValueError) as e:
        self._use_fast_yuv = False
        flog(f"[CLUSTER_CAM_WARN] Fast NV12 conversion unavailable; using RGB fallback: {e}")

    # Compatibility path for OpenCV builds without cvtColorTwoPlane.
    rgb = self._rgb_from_vision_buffer(buffer)
    cropped = rgb[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
    panel = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    return self._apply_camera_contrast(panel), source_to_panel

  def _camera_thread(self):
    error_count = 0
    while self.running:
      try:
        if self.vipc is None:
          self._init_camera()
          if self.vipc is None:
            self._wait_or_stopping(CONNECTION_RETRY_SECONDS)
            continue

        if not self.vipc.is_connected():
          now = time.monotonic()
          retry_wait = CONNECTION_RETRY_SECONDS - (now - self._last_connection_attempt)
          if retry_wait > 0.0:
            self._wait_or_stopping(retry_wait)
            continue
          self._last_connection_attempt = now
          if not self.vipc.connect(False) or not self.vipc.num_buffers:
            self._wait_or_stopping(CONNECTION_RETRY_SECONDS)
            continue
          self._connected_at = time.monotonic()
          self._last_frame_at = 0.0
          self._connection_wait_logged = False
          flog(f"[CLUSTER_CAM_SUCCESS] VisionIPC connected: "
               f"{self.vipc.width}x{self.vipc.height}, stride={self.vipc.stride}")

        buffer = self.vipc.recv(timeout_ms=50)
        if buffer is None:
          now = time.monotonic()
          if not self.vipc.is_connected():
            self._reset_camera("VisionIPC disconnected")
          elif self._connected_at > 0.0 and \
               now - max(self._connected_at, self._last_frame_at) > FRAME_STALE_SECONDS:
            self._reset_camera("Camera stream stale for 1.2 seconds")
          continue

        prepare_started = time.monotonic()
        frame, source_to_panel = self._panel_rgb_from_vision_buffer(buffer)
        prepare_elapsed = time.monotonic() - prepare_started

        with self._frame_condition:
          self._source_to_panel = source_to_panel
          self.latest_frame = frame
          self.frame_count += 1
          self._last_frame_at = time.monotonic()
          self._frame_condition.notify_all()
        now = time.monotonic()
        if self._perf_started is None:
          self._perf_started = prepare_started
        self._perf_frames += 1
        self._perf_prepare_time += prepare_elapsed
        error_count = 0
        if self.frame_count == 1:
          flog(f"[CLUSTER_CAM_SUCCESS] First optimized road frame: source={buffer.width}x{buffer.height}, "
               f"panel={self.latest_frame.shape[1]}x{self.latest_frame.shape[0]}, "
               f"contrast={self.camera_contrast:.2f}")
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
        self._reset_camera()
        self._wait_or_stopping(CONNECTION_RETRY_SECONDS)

  def has_frame(self):
    with self._frame_condition:
      return self.latest_frame is not None

  def get_frame(self):
    with self._frame_condition:
      return self.latest_frame

  def wait_for_frame(self, previous_frame_count, timeout):
    """Wait for a fresh camera frame so the render loop does not resample 20 Hz video out of phase."""
    with self._frame_condition:
      self._frame_condition.wait_for(
        lambda: not self.running or self.frame_count != previous_frame_count,
        timeout=timeout,
      )
      return self.frame_count

  def get_source_to_panel_transform(self, panel_x=0, panel_y=0):
    with self._frame_condition:
      transform = self._source_to_panel.copy()
      transform[0, 2] += panel_x
      transform[1, 2] += panel_y
      return transform

  def close(self):
    with self._frame_condition:
      self.running = False
      self.latest_frame = None
      self._frame_condition.notify_all()
    if self.thread is not None:
      self.thread.join(timeout=1.0)
    self.vipc = None
