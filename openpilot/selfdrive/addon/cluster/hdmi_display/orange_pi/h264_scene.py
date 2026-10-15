"""Compose the existing cluster HUD over locally decoded WebRTC camera frames."""

from copy import deepcopy
import math
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw

try:
  from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer
  from openpilot.selfdrive.addon.cluster.cluster_render_support import Colors
except ImportError:
  from cluster_renderer import ClusterRenderer
  from cluster_render_support import Colors

try:
  from .h264_protocol import validate_scene
except ImportError:
  from h264_protocol import validate_scene


DEFAULT_FRESHNESS_SECONDS = 0.5


def source_panel_geometry(source_size, panel_size):
  """Use the same even NV12 crop as ClusterLiveCamera, in raw-camera pixels."""
  width, height = source_size
  target_w, target_h = panel_size
  if width / height < target_w / target_h:
    crop_w, crop_h = width, min(height, int(round(width / (target_w / target_h))))
  else:
    crop_w, crop_h = min(width, int(round(height * target_w / target_h))), height
  crop_w, crop_h = max(2, crop_w & ~1), max(2, crop_h & ~1)
  crop_x = max(0, ((width - crop_w) // 2) & ~1)
  crop_y = max(0, ((height - crop_h) // 2) & ~1)
  scale_x, scale_y = target_w / crop_w, target_h / crop_h
  transform = np.array([
    [scale_x, 0.0, -crop_x * scale_x],
    [0.0, scale_y, -crop_y * scale_y],
    [0.0, 0.0, 1.0],
  ], dtype=np.float32)
  return (crop_x, crop_y, crop_w, crop_h), transform


def _arrival_time(value, clock):
  value = clock() if value is None else float(value)
  if not math.isfinite(value):
    raise ValueError("Arrival time must be finite")
  return value


class _SceneCamera:
  def __init__(self, frame, transform):
    self.frame, self.transform = frame, transform

  def get_frame(self):
    return self.frame

  def get_source_to_panel_transform(self, panel_x=0, panel_y=0):
    transform = self.transform.copy()
    transform[0, 2] += panel_x
    transform[1, 2] += panel_y
    return transform


class _SceneModels:
  def __init__(self, valid, hud, path):
    self.data = valid, hud, path

  def get_render_data(self):
    return self.data


class H264Scene:
  """Own one camera frame and one bounded, validated metadata snapshot.

  update() is called only for a newly received metadata message. Decoded camera
  frames have a separate arrival clock; rendering never refreshes either age.
  Wire schema/size validation is performed before update() by the receiver.
  All pygame/SDL operations remain in HdmiDisplay, outside this class.
  """

  def __init__(self, asset_root=None, width=1920, height=480, fps=20, clock=time.monotonic,
               state_timeout=DEFAULT_FRESHNESS_SECONDS, video_timeout=DEFAULT_FRESHNESS_SECONDS):
    width, height = int(width), int(height)
    if not 64 <= height <= 2160 or not 128 <= width <= 4096:
      raise ValueError("Unsupported cluster output dimensions")
    side_panel_width = (height - 20) // 3
    if width - 20 - side_panel_width * 4 < 2:
      raise ValueError("Output is too narrow for the cluster panels")
    for timeout in (state_timeout, video_timeout):
      if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Freshness timeouts must be positive and finite")
    asset_root = Path(__file__).resolve().parent / "assets" if asset_root is None else Path(asset_root)
    self.clock = clock
    self.state_timeout, self.video_timeout = state_timeout, video_timeout
    self.config = SimpleNamespace(
      width=width, height=height, fps=min(max(float(fps), 1.0), 60.0), border_size=10,
      side_panel_width=side_panel_width, BASEDIR=asset_root.parent, asset_dir=asset_root,
      font_bold=str(asset_root / "fonts" / "Inter-Bold.ttf"),
      font_regular=str(asset_root / "fonts" / "Inter-Regular.ttf"),
      is_metric=True, speed_unit="km/h", rotate_180=False,
    )
    self.renderer = ClusterRenderer(self.config)
    self._lock = threading.Lock()
    self._snapshot = None
    self._state_received_at = self._video_received_at = None
    self._video = None
    self._video_generation = 0
    self._panel_key = self._panel = self._panel_transform = None
    self._contrast = self._contrast_lut = None
    self._update_contrast(1.08)
    self.last_frame_timings = {}
    self.last_status = {"state_fresh": False, "video_fresh": False, "model_valid": False}

  def update(self, snapshot, received_at=None):
    if not isinstance(snapshot, dict):
      raise ValueError("Cluster scene metadata must be a dictionary")
    validate_scene(snapshot)
    source_size = snapshot.get("source_size")
    if source_size is not None and (not isinstance(source_size, (list, tuple)) or len(source_size) != 2 or any(
      type(value) is not int or not 2 <= value <= 8192 for value in source_size
    )):
      raise ValueError("Raw camera source_size must contain two bounded pixel dimensions")
    if not isinstance(snapshot.get("hud"), dict) or not isinstance(snapshot.get("path"), dict):
      raise ValueError("Cluster scene metadata requires hud and path dictionaries")
    snapshot = deepcopy(snapshot)
    snapshot["source_size"] = None if source_size is None else tuple(source_size)
    path = snapshot["path"]
    for name in ("path_x", "path_y", "path_z"):
      path.setdefault(name, [])
    calibration = path.get("calib_transform")
    if calibration is not None:
      calibration = np.asarray(calibration, dtype=np.float32)
      if calibration.shape != (3, 3) or not np.isfinite(calibration).all():
        raise ValueError("Camera calibration must be a finite 3x3 matrix")
      path["calib_transform"] = calibration
    arrival = _arrival_time(received_at, self.clock)
    with self._lock:
      self._snapshot, self._state_received_at = snapshot, arrival

  def set_video(self, rgb, received_at=None):
    if hasattr(rgb, "to_ndarray"):
      rgb = rgb.to_ndarray(format="rgb24")
    rgb = np.asarray(rgb)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or any(
      not 2 <= dimension <= 4096 for dimension in rgb.shape[:2]
    ):
      raise ValueError("Decoded video must be a bounded RGB24 frame")
    # Decode libraries can reuse their own storage. Retain one owned frame so
    # metadata updates and subsequent decodes cannot change an active render.
    rgb = np.array(rgb, copy=True, order="C")
    arrival = _arrival_time(received_at, self.clock)
    with self._lock:
      self._video, self._video_received_at = rgb, arrival
      self._video_generation += 1

  def _update_contrast(self, value):
    contrast = float(value) if type(value) in (float, int) and 0.5 <= value <= 2.0 else 1.08
    if self._contrast != contrast:
      values = np.arange(256, dtype=np.float32)
      self._contrast_lut = np.clip((values - 127.5) * contrast + 127.5, 0, 255).astype(np.uint8)
      self._contrast = contrast

  @staticmethod
  def _source_input_fresh(snapshot, field, elapsed_ms):
    health = snapshot.get("health", {})
    if field in snapshot:
      age = snapshot[field]
    elif isinstance(health, dict) and field in health:
      age = health[field]
    else:
      # Older snapshots have only local transport freshness information.
      return True
    return type(age) in (int, float) and math.isfinite(age) and 0 <= age and age + elapsed_ms <= 500

  def _camera_panel(self, rgb, source_size, generation):
    key = generation, source_size, self._contrast
    if key != self._panel_key:
      panel_size = self.renderer.camera_w, self.renderer.camera_h
      (crop_x, crop_y, crop_w, crop_h), transform = source_panel_geometry(source_size, panel_size)
      raw_w, raw_h = source_size
      decoded_h, decoded_w = rgb.shape[:2]
      if (decoded_w, decoded_h) == source_size:
        panel = cv2.resize(rgb[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w], panel_size,
                           interpolation=cv2.INTER_LINEAR)
      else:
        # H264 encodes a scaled full sensor image. Map the ORIGINAL even crop
        # into that image instead of cropping by the decoded aspect ratio.
        # Pixel-center offsets match OpenCV resize's source sampling convention.
        ratio_x, ratio_y = decoded_w / raw_w, decoded_h / raw_h
        inverse = np.array([
          [crop_w / panel_size[0] * ratio_x, 0.0, (crop_x + crop_w / panel_size[0] * 0.5) * ratio_x - 0.5],
          [0.0, crop_h / panel_size[1] * ratio_y, (crop_y + crop_h / panel_size[1] * 0.5) * ratio_y - 0.5],
        ], dtype=np.float32)
        panel = cv2.warpAffine(rgb, inverse, panel_size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REPLICATE)
      cv2.LUT(panel, self._contrast_lut, dst=panel)
      self._panel, self._panel_transform = panel, transform
      self._panel_key = key
    return self._panel, self._panel_transform

  def render(self, now=None):
    now = _arrival_time(now, self.clock)
    with self._lock:
      snapshot, state_at = self._snapshot, self._state_received_at
      rgb, video_at = self._video, self._video_received_at
      video_generation = self._video_generation
    metadata_fresh = state_at is not None and 0 <= now - state_at <= self.state_timeout
    video_fresh = video_at is not None and 0 <= now - video_at <= self.video_timeout
    snapshot = {} if snapshot is None else snapshot
    elapsed_ms = max(0.0, now - state_at) * 1000 if state_at is not None else 0.0
    state_fresh = metadata_fresh and self._source_input_fresh(snapshot, "car_state_age_ms", elapsed_ms)
    self.config.is_metric = snapshot.get("is_metric") is not False
    self.config.speed_unit = "km/h" if self.config.is_metric else "mph"
    self.config.rotate_180 = snapshot.get("rotate_180") is True
    self._update_contrast(snapshot.get("camera_contrast", 1.08))
    hud = snapshot.get("hud", {}) if state_fresh else {"_state_stale": True}
    path = snapshot.get("path", {"path_x": [], "path_y": [], "path_z": [], "calib_transform": None})
    model_valid = (state_fresh and video_fresh and snapshot.get("model_valid") is True
                   and self._source_input_fresh(snapshot, "model_age_ms", elapsed_ms))
    if video_fresh and snapshot.get("source_size") is not None:
      panel, transform = self._camera_panel(rgb, snapshot["source_size"], video_generation)
    else:
      panel, transform = None, np.eye(3, dtype=np.float32)
      model_valid = False
    image = self.renderer.render(_SceneCamera(panel, transform), _SceneModels(model_valid, hud, path))
    if not state_fresh and panel is not None:
      self.renderer._centered(ImageDraw.Draw(image), self.renderer.camera_x + self.renderer.camera_w / 2,
                              self.renderer.camera_y + self.renderer.camera_h * 0.60,
                              "WAITING FOR LIVE DATA...", self.renderer.font_warning, Colors.RED,
                              stroke_width=2, stroke_fill=Colors.BLACK)
    self.last_frame_timings = dict(self.renderer.last_frame_timings)
    self.last_status = {"state_fresh": state_fresh, "video_fresh": video_fresh, "model_valid": bool(model_valid)}
    return image.transpose(Image.Transpose.ROTATE_180) if self.config.rotate_180 else image
