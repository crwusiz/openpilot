"""CPU HUD rasterization and camera layers for the Pi's SDL GPU compositor."""

from dataclasses import dataclass
import time
from time import thread_time

import cv2
import numpy as np
from PIL import Image

try:
  from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer, ColorBlendWorkspace
  from openpilot.selfdrive.addon.cluster.cluster_render_support import Colors
except ImportError:
  from cluster_renderer import ClusterRenderer, ColorBlendWorkspace
  from cluster_render_support import Colors


@dataclass
class SceneLayers:
  overlay: Image.Image
  camera: np.ndarray | None
  camera_rect: tuple
  camera_uv: tuple
  rotate_180: bool = False
  path: np.ndarray | None = None

  def to_image(self):
    """Same retained layers on a software-only or failed GPU backend."""
    image = Image.new("RGBA", self.overlay.size, (0, 0, 0, 255))
    if self.camera is not None:
      x, y, width, height = self.camera_rect
      u0, v0, u1, v1 = self.camera_uv
      source_h, source_w = self.camera.shape[:2]
      scale_x, scale_y = (u1 - u0) * source_w / width, (v1 - v0) * source_h / height
      inverse = np.array([[scale_x, 0, u0 * source_w + scale_x * 0.5 - 0.5],
                          [0, scale_y, v0 * source_h + scale_y * 0.5 - 0.5]], dtype=np.float32)
      panel = cv2.warpAffine(self.camera, inverse, (width, height),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
      image.paste(Image.fromarray(panel), (x, y))
    if self.path is not None:
      height, width = self.path.shape[:2]
      path = Image.frombytes("RGBa", (width, height), self.path.tobytes()).convert("RGBA")
      image.alpha_composite(path, self.camera_rect[:2])
    image = Image.alpha_composite(image, self.overlay).convert("RGB")
    return image.transpose(Image.Transpose.ROTATE_180) if self.rotate_180 else image


class LayeredRenderer(ClusterRenderer):
  """Keep the common layout/projection; rasterize paths without camera pixels."""

  def __init__(self, config):
    super().__init__(config)
    self._straight_alpha_hud = True

  def render_overlay(self, transform, models, has_camera):
    started = time.monotonic()
    self._update_filter_intervals(started)
    self._source_to_panel = transform.copy()
    self._source_to_panel[0, 2] += self.camera_x
    self._source_to_panel[1, 2] += self.camera_y
    if not hasattr(self, "_overlay_base"):
      self._overlay_base = np.empty((*self.base_canvas.shape[:2], 4), dtype=np.uint8)
      self._overlay_base[:, :, :3] = self.base_canvas
      self._overlay_base[:, :, 3] = 255
    frame = self._overlay_base.copy()
    if has_camera:
      frame[self.camera_y:self.camera_y + self.camera_h, self.camera_x:self.camera_x + self.camera_w] = 0
    copied_at = time.monotonic()
    model_valid, hud, path = models.get_render_data()
    snapshot_at = time.monotonic()
    path_started = thread_time()
    path_pixels = None
    if has_camera and model_valid:
      if not hasattr(self, "_path_blender"):
        self._path_blender = ColorBlendWorkspace(self.camera_w, channels=4)
      rgb_blender, self._color_blender = self._color_blender, self._path_blender
      try:
        self._draw_model_path(frame, path, hud)
      finally:
        self._color_blender = rgb_blender
      region = frame[self.camera_y:self.camera_y + self.camera_h, self.camera_x:self.camera_x + self.camera_w]
      if cv2.countNonZero(cv2.extractChannel(region, 3)):
        path_pixels = region.copy()
      region[:] = 0
    else:
      self._reset_leads()
    path_cpu = thread_time() - path_started
    path_at = time.monotonic()
    image = Image.fromarray(frame)
    self._draw_hud(image, hud, has_camera)
    self.last_frame_timings = {"camera_copy": copied_at - started, "snapshot": snapshot_at - copied_at,
                               "path": path_at - snapshot_at, "path_cpu": path_cpu, "hud": time.monotonic() - path_at}
    return image, path_pixels

  @staticmethod
  def _fill_polygon_alpha(image, polygon, color, alpha, blender=None):
    if image.shape[2] == 4 and (blender is None or blender._shape[2] != 4):
      blender = ColorBlendWorkspace(image.shape[1], channels=4)
    return ClusterRenderer._fill_polygon_alpha(image, polygon, color, alpha, blender)

  @staticmethod
  def _fill_polygon_gradient(image, polygon, colors, stops, blender=None):
    if image.shape[2] == 4 and (blender is None or blender._shape[2] != 4):
      blender = ColorBlendWorkspace(image.shape[1], channels=4)
    return ClusterRenderer._fill_polygon_gradient(image, polygon, colors, stops, blender)

  def _draw_lead_indicator(self, camera_region, hud_data):
    if camera_region.shape[2] != 4:
      return super()._draw_lead_indicator(camera_region, hud_data)
    offset = np.array([self.camera_x, self.camera_y], dtype=np.float32)
    for lead, info in zip(self._lead_vehicles, self._lead_info, strict=True):
      opacity = lead.fade_filter.x
      if lead.bar.size == 0 or int(255 * opacity) == 0:
        continue
      self._fill_polygon_alpha(camera_region, (lead.bar - offset).astype(np.int32), Colors.RED, opacity, self._color_blender)
      x, y = map(int, lead.bar[[0, 3]].mean(axis=0) - offset)
      speed = max(0.0, float(hud_data.get("v_ego", 0) or 0) + float(info.get("v_rel", 0) or 0))
      metric = getattr(self.config, "is_metric", True)
      labels = (f"{float(info.get('d_rel', 0)):.0f} m", f"{speed * (3.6 if metric else 2.236936):.0f} {'km/h' if metric else 'mph'}")
      for label, baseline in zip(labels, (y - 13, y + 27), strict=True):
        glyph = self._text_glyph(label, self.font_unit, 2, anchor="ms")
        sprite = glyph.colored(Colors.WHITE, Colors.BLACK).copy()
        sprite.putalpha(sprite.getchannel("A").point(lambda value, opacity=opacity: round(value * opacity)))
        left, top = x - glyph.anchor_x, baseline - glyph.anchor_y
        x0, y0 = max(0, left), max(0, top)
        x1, y1 = min(camera_region.shape[1], left + sprite.width), min(camera_region.shape[0], top + sprite.height)
        if x1 > x0 and y1 > y0:
          roi = camera_region[y0:y1, x0:x1]
          sprite = sprite.crop((x0 - left, y0 - top, x1 - left, y1 - top))
          mask = sprite.getchannel("A")
          sprite.putalpha(255)
          image = Image.fromarray(roi)
          image.paste(sprite, (0, 0), mask)
          roi[:] = np.asarray(image)
