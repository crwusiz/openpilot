import os
import time
from collections import OrderedDict
import cv2
import numpy as np

from PIL import Image, ImageDraw, ImageFont
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_config import Colors, NO_THROTTLE_COLORS, STEERING_COLORS, THROTTLE_COLORS, colors_alpha


DISTANCE_ICONS = {
  1: "dist1",
  2: "dist2",
  3: "dist3",
  4: "dist4",
}

WIFI_ICONS = {
  1: "wifi_strength_low",
  2: "wifi_strength_medium",
  3: "wifi_strength_high",
  4: "wifi_strength_full",
}

WHEEL_ICONS = {
  "default": "steer",
  "enabled": "steer_green",
  "steering": "steer_blue",
  "critical": "steer_critical",
}

TRAFFIC_ICONS = {
  0: "traffic_off",
  1: "traffic_red",
  2: "traffic_green",
}

BLINKER_DRAW_COUNT = 10
BLINKER_SEQUENCE_MS = 400.0
BLINKER_PAUSE_MS = 250.0
CAMERA_OVERLAY_ICON_HEIGHT = 94
CLIP_MARGIN = 500
GRADIENT_BANDS = 8

class ClusterRenderer:
  def __init__(self, config):
    cloudlog.info("Initializing ClusterRenderer...")
    self.config = config

    self.target_w = config.width
    self.target_h = config.height
    self.border_size = getattr(config, "border_size", 10)
    self.content_x = self.border_size
    self.content_y = self.border_size
    self.content_w = self.target_w - self.border_size * 2
    self.content_h = self.target_h - self.border_size * 2
    self.panel_w = getattr(config, "side_panel_width", self.content_h // 3)
    self.side_w = self.panel_w
    self.left_panel_x = self.content_x
    self.left_aux_x = self.left_panel_x + self.panel_w
    self.camera_x = self.left_aux_x + self.panel_w
    self.camera_w = self.content_w - self.panel_w * 4
    self.camera_y = self.content_y
    self.camera_h = self.content_h
    self.right_aux_x = self.camera_x + self.camera_w
    self.right_panel_x = self.right_aux_x + self.panel_w
    self.row_h = self.content_h / 3.0
    self.row_centers = tuple(self.content_y + self.row_h * (row + 0.5) for row in range(3))
    self._source_to_panel = np.eye(3, dtype=np.float32)
    self._blend_filter = FirstOrderFilter(1.0, 0.25, 1.0 / self.config.fps)

    try:
      self.font_speed = ImageFont.truetype(self.config.font_bold, 58)
      self.font_current_speed = ImageFont.truetype(self.config.font_bold, 92)
      self.font_value = ImageFont.truetype(self.config.font_bold, 42)
      self.font_unit = ImageFont.truetype(self.config.font_regular, 20)
      self.font_current_unit = ImageFont.truetype(self.config.font_regular, 24)
      self.font_small = ImageFont.truetype(self.config.font_regular, 18)
      self.font_label = ImageFont.truetype(self.config.font_bold, 18)
      self.font_warning = ImageFont.truetype(self.config.font_bold, 42)
    except Exception as e:
      cloudlog.error(f"Failed to load fonts: {e}")
      self.font_speed = ImageFont.load_default()
      self.font_current_speed = ImageFont.load_default()
      self.font_value = ImageFont.load_default()
      self.font_unit = ImageFont.load_default()
      self.font_current_unit = ImageFont.load_default()
      self.font_small = ImageFont.load_default()
      self.font_label = ImageFont.load_default()
      self.font_warning = ImageFont.load_default()

    self.blank_canvas = np.full(
      (self.target_h, self.target_w, 3), Colors.BLACK, dtype=np.uint8,
    )
    self.camera_height = 1.22
    self.focal_length = 910.0
    self.icons = {}
    self._icon_cache = {}
    self._rotated_icon_cache = OrderedDict()
    self._text_cache = OrderedDict()
    self._blink_started_at = None
    icon_dir = os.path.join(str(self.config.BASEDIR), "selfdrive", "assets", "icons")
    for name in (
      *WHEEL_ICONS.values(), *WIFI_ICONS.values(),
      *TRAFFIC_ICONS.values(), *DISTANCE_ICONS.values(),
      "accel_pressed", "brake_pressed", "tpms", "gps", "compass",
      "speed_bump", "school_zone", "speed_camera",
      "turnsignal_l", "turnsignal_r", "blind_spot_left", "blind_spot_right",
    ):
      try:
        self.icons[name] = Image.open(os.path.join(icon_dir, f"{name}.png")).convert("RGBA")
      except Exception:
        pass

  def render(self, camera, models):
    # Fetch once so a stale-stream reset cannot clear the frame between a
    # separate has_frame() check and get_frame() call.
    camera_frame = camera.get_frame()
    has_camera = camera_frame is not None
    if has_camera:
      if camera_frame.shape[:2] == (self.camera_h, self.camera_w) and \
          hasattr(camera, "get_source_to_panel_transform"):
        frame = self.blank_canvas.copy()
        frame[self.camera_y:self.camera_y + self.camera_h,
              self.camera_x:self.camera_x + self.camera_w] = camera_frame
        self._source_to_panel = camera.get_source_to_panel_transform(self.camera_x, self.camera_y)
      else:
        frame = self._crop_and_resize(camera_frame)
    else:
      frame = self.blank_canvas.copy()

    model_valid, hud_data, path_data = models.get_render_data()
    if has_camera and model_valid:
      frame = self._draw_model_path(frame, path_data, hud_data)

    pil_img = Image.fromarray(frame)
    self._draw_hud(pil_img, hud_data, has_camera)
    # Keep the composed frame as PIL through the USB worker. Converting the
    # complete 1920x462 image back to NumPy here only for it to be rotated and
    # JPEG-encoded in the next thread was a full-frame copy on every update.
    return pil_img

  def _crop_and_resize(self, frame):
    source_h, source_w = frame.shape[:2]
    source_aspect = source_w / source_h
    target_aspect = self.camera_w / self.camera_h
    if source_aspect < target_aspect:
      crop_w = source_w
      crop_h = max(1, int(round(source_w / target_aspect)))
    else:
      crop_w = max(1, int(round(source_h * target_aspect)))
      crop_h = source_h
    crop_x = (source_w - crop_w) // 2
    crop_y = (source_h - crop_h) // 2
    cropped = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
    camera = cv2.resize(cropped, (self.camera_w, self.camera_h), interpolation=cv2.INTER_AREA)

    scale_x = self.camera_w / crop_w
    scale_y = self.camera_h / crop_h
    self._source_to_panel = np.array([
      [scale_x, 0.0, self.camera_x - crop_x * scale_x],
      [0.0, scale_y, self.camera_y - crop_y * scale_y],
      [0.0, 0.0, 1.0],
    ], dtype=np.float32)
    canvas = self.blank_canvas.copy()
    canvas[self.camera_y:self.camera_y + self.camera_h,
           self.camera_x:self.camera_x + self.camera_w] = camera
    return canvas

  def _project_points(self, points, calib_transform, clip_margin=0):
    points = np.asarray(points, dtype=np.float32)
    if points.size == 0:
      return np.empty((0, 2), dtype=np.float32), np.empty((0,), dtype=bool)
    projected = np.asarray(calib_transform, dtype=np.float32) @ points.T
    valid = projected[2] > 1e-3
    pixels = np.zeros((len(points), 2), dtype=np.float32)
    if np.any(valid):
      normalized = projected[:, valid] / projected[2, valid]
      panel = self._source_to_panel @ normalized
      pixels[valid] = panel[:2].T
      valid &= (
        (pixels[:, 0] >= self.camera_x - clip_margin) &
        (pixels[:, 0] < self.camera_x + self.camera_w + clip_margin) &
        (pixels[:, 1] >= self.camera_y - clip_margin) &
        (pixels[:, 1] < self.camera_y + self.camera_h + clip_margin)
      )
    return pixels, valid

  def _map_line_to_polygon(self, xs, ys, zs, width, z_offset, calib_transform, max_distance=100.0, allow_invert=True):
    count = min(len(xs), len(ys), len(zs))
    if count < 2:
      return None
    raw = np.column_stack((xs[:count], ys[:count], zs[:count])).astype(np.float32)
    raw = raw[(raw[:, 0] >= 0.0) & (raw[:, 0] <= max_distance)]
    if len(raw) < 2:
      return None
    left = raw + np.array([0.0, -width, z_offset], dtype=np.float32)
    right = raw + np.array([0.0, width, z_offset], dtype=np.float32)
    left_px, left_valid = self._project_points(left, calib_transform, CLIP_MARGIN)
    right_px, right_valid = self._project_points(right, calib_transform, CLIP_MARGIN)
    valid = left_valid & right_valid
    if np.count_nonzero(valid) < 2:
      return None

    left_px = left_px[valid]
    right_px = right_px[valid]

    # Prevent the path polygon from folding back over itself on hills.
    if not allow_invert and len(left_px) > 1:
      keep = left_px[:, 1] == np.minimum.accumulate(left_px[:, 1])
      left_px = left_px[keep]
      right_px = right_px[keep]
      if len(left_px) < 2:
        return None

    return np.vstack((left_px, right_px[::-1])).astype(np.int32)

  @staticmethod
  def _fill_polygon_alpha(image, polygon, color, alpha):
    alpha = float(np.clip(alpha, 0.0, 1.0))
    if len(color) == 4:
      alpha *= color[3] / 255.0
      color = color[:3]
    if alpha <= 0.0:
      return

    # Lane and road-edge polygons used to copy and blend the whole camera
    # panel for every line. Blend only the polygon's bounding ROI instead;
    # this removes several full-frame allocations per render without changing
    # pixels outside the polygon.
    x, y, width, height = cv2.boundingRect(polygon)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + width, image.shape[1]), min(y + height, image.shape[0])
    if x0 >= x1 or y0 >= y1:
      return

    roi = image[y0:y1, x0:x1]
    local_polygon = polygon - np.array([x0, y0], dtype=np.int32)
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [local_polygon], 255)
    blended = cv2.addWeighted(np.full_like(roi, color), alpha, roi, 1.0 - alpha, 0)
    cv2.copyTo(blended, mask, roi)

  @staticmethod
  def _blend_colors(begin_colors, end_colors, factor):
    factor = float(np.clip(factor, 0.0, 1.0))
    inverse = 1.0 - factor
    return [
      tuple(int(inverse * start[channel] + factor * end[channel]) for channel in range(4))
      for start, end in zip(begin_colors, end_colors, strict=True)
    ]

  @staticmethod
  def _fill_polygon_gradient(image, polygon, colors, stops):
    x, y, width, height = cv2.boundingRect(polygon)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + width, image.shape[1]), min(y + height, image.shape[0])
    if x0 >= x1 or y0 >= y1:
      return

    roi = image[y0:y1, x0:x1]
    local_polygon = polygon - np.array([x0, y0], dtype=np.int32)
    denominator = max(image.shape[0] - 1, 1)
    color_array = np.asarray(colors, dtype=np.float32)

    # The former full-resolution float alpha buffer allocated several large
    # arrays for every frame and dominated render time on-device. Eight bands
    # retain the low-alpha visual gradient while keeping blending in OpenCV and
    # bounding temporary memory to one small band.
    band_height = max(1, (roi.shape[0] + GRADIENT_BANDS - 1) // GRADIENT_BANDS)
    for band_y0 in range(0, roi.shape[0], band_height):
      band_y1 = min(band_y0 + band_height, roi.shape[0])
      band = roi[band_y0:band_y1]
      band_polygon = local_polygon - np.array([0, band_y0], dtype=np.int32)
      mask = np.zeros(band.shape[:2], dtype=np.uint8)
      cv2.fillPoly(mask, [band_polygon], 255)
      if not cv2.countNonZero(mask):
        continue

      midpoint_y = y0 + (band_y0 + band_y1 - 1) * 0.5
      gradient_position = 1.0 - midpoint_y / denominator
      color = tuple(int(np.interp(gradient_position, stops, color_array[:, channel])) for channel in range(4))
      alpha = color[3] / 255.0
      if alpha <= 0.0:
        continue
      blended = cv2.addWeighted(np.full_like(band, color[:3]), alpha, band, 1.0 - alpha, 0)
      cv2.copyTo(blended, mask, band)

  def _draw_model_path(self, frame, path_data, hud_data):
    if not path_data["path_x"]:
      return frame
    calib_transform = path_data.get("calib_transform")
    if calib_transform is None:
      return frame

    camera_region = frame[self.camera_y:self.camera_y + self.camera_h,
                          self.camera_x:self.camera_x + self.camera_w]
    camera_height = float(path_data.get("camera_height", self.camera_height) or self.camera_height)

    # Equivalent to ui_state.status != UIStatus.DISENGAGED for the cluster process.
    if self._get_border_color(hud_data) != Colors.DISENGAGED:
      self._draw_lane_lines(camera_region, path_data, calib_transform)
      self._draw_path(camera_region, path_data, hud_data, calib_transform, camera_height)

    self._draw_lead_indicators(frame, path_data, hud_data, calib_transform, camera_height)

    return frame

  def _draw_path(self, camera_region, path_data, hud_data, calib_transform, camera_height):
    path_z = path_data.get("path_z") or [0.0] * len(path_data["path_x"])
    path_poly = self._map_line_to_polygon(
      path_data["path_x"], path_data["path_y"], path_z,
      0.9, camera_height, calib_transform, max_distance=100.0, allow_invert=False,
    )
    if path_poly is not None:
      local_path_poly = path_poly - np.array([self.camera_x, self.camera_y], dtype=np.int32)
      if hud_data.get("steering_pressed"):
        self._fill_polygon_gradient(camera_region, local_path_poly, STEERING_COLORS, [0.0, 0.5, 1.0])
      else:
        allow_throttle = hud_data.get("allow_throttle", True) or not hud_data.get("longitudinal_control", False)
        blend_factor = round(self._blend_filter.update(int(allow_throttle)) * 100) / 100
        colors = self._blend_colors(NO_THROTTLE_COLORS, THROTTLE_COLORS, blend_factor)
        self._fill_polygon_gradient(camera_region, local_path_poly, colors, [0.0, 0.5, 1.0])

  def _draw_lane_lines(self, camera_region, path_data, calib_transform):
    lane_lines = path_data.get("lane_lines") or []
    lane_probs = path_data.get("lane_line_probs") or []
    for index, line in enumerate(lane_lines):
      if len(line) != 3:
        continue
      probability = float(lane_probs[index]) if index < len(lane_probs) else 0.0
      width = 0.025 * probability
      polygon = self._map_line_to_polygon(*line, width, 0.0, calib_transform)
      if polygon is not None:
        local_polygon = polygon - np.array([self.camera_x, self.camera_y], dtype=np.int32)
        self._fill_polygon_alpha(camera_region, local_polygon, Colors.WHITE, np.clip(probability, 0.0, 0.7))

    road_edges = path_data.get("road_edges") or []
    road_stds = path_data.get("road_edge_stds") or []
    for index, edge in enumerate(road_edges):
      if len(edge) != 3:
        continue
      confidence = 1.0 - float(road_stds[index]) if index < len(road_stds) else 0.0
      polygon = self._map_line_to_polygon(*edge, 0.025, 0.0, calib_transform)
      if polygon is not None:
        local_polygon = polygon - np.array([self.camera_x, self.camera_y], dtype=np.int32)
        self._fill_polygon_alpha(camera_region, local_polygon, colors_alpha(Colors.RED, 100), np.clip(confidence, 0.0, 1.0))

  def _draw_lead_indicators(self, frame, path_data, hud_data, calib_transform, camera_height):
    path_x = path_data.get("path_x") or []
    path_z = path_data.get("path_z") or []
    drawn_distances = []
    for lead in path_data.get("leads") or []:
      if not lead.get("present"):
        continue
      distance = float(lead.get("d_rel", 0.0))
      if any(abs(distance - previous) <= 12.0 for previous in drawn_distances):
        continue
      drawn_distances.append(distance)
      z = 0.0
      if path_x and path_z:
        index = int(np.argmin(np.abs(np.asarray(path_x) - distance)))
        if index < len(path_z):
          z = float(path_z[index])
      point, valid = self._project_points(
        [[distance, -float(lead.get("y_rel", 0.0)), z + camera_height]], calib_transform,
      )
      if not valid[0]:
        continue
      x, y = map(int, point[0])
      half_width = max(28, int(20 + 600 / max(distance + 10.0, 10.0)))
      alpha = int(np.clip(255 * (1.0 - distance / 40.0) + max(0.0, -lead.get("v_rel", 0.0)) * 25, 70, 255))
      color = (255, max(35, 150 - alpha // 3), max(35, 150 - alpha // 3))
      cv2.line(frame, (x - half_width, y), (x + half_width, y), color, 6)

      conversion = 3.6 if getattr(self.config, "is_metric", True) else 2.236936
      unit = "km/h" if getattr(self.config, "is_metric", True) else "mph"
      lead_speed = max(0.0, (float(hud_data.get("v_ego", 0.0) or 0.0) +
                             float(lead.get("v_rel", 0.0) or 0.0)) * conversion)
      labels = (f"{distance:.0f} m", f"{lead_speed:.0f} {unit}")
      for label, baseline_y in zip(labels, (y - 13, y + 27), strict=True):
        text_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.68, 2)
        origin = (x - text_size[0] // 2, baseline_y)
        # An outline keeps the text legible while leaving the camera image visible.
        cv2.putText(frame, label, origin, cv2.FONT_HERSHEY_SIMPLEX,
                    0.68, Colors.BLACK, 6, cv2.LINE_AA)
        cv2.putText(frame, label, origin, cv2.FONT_HERSHEY_SIMPLEX,
                    0.68, Colors.WHITE, 2, cv2.LINE_AA)

  def _base_icon(self, name, size, active=True, opacity=255):
    source = self.icons.get(name)
    if source is None:
      return None
    dimensions = (int(size), int(size)) if isinstance(size, (int, float)) else tuple(map(int, size))
    opacity = max(0, min(255, int(opacity)))
    key = (name, dimensions, bool(active), opacity)
    image = self._icon_cache.get(key)
    if image is None:
      image = source.resize(dimensions, Image.Resampling.LANCZOS)
      image = image.copy()
      alpha_scale = opacity if active else opacity * 70 // 255
      if alpha_scale < 255:
        alpha = image.getchannel("A").point(lambda p: p * alpha_scale // 255)
        image.putalpha(alpha)
      self._icon_cache[key] = image
    return image

  def _icon(self, image, name, cx, cy, size, active=True, rotation=0.0, opacity=255):
    icon = self._base_icon(name, size, active, opacity)
    if icon is None:
      return
    if rotation:
      # Steering angle and bearing usually change by fractions of a degree.
      # Re-running PIL's bicubic transform for both icons on every camera frame
      # is expensive on-device and has no visible benefit at these icon sizes.
      rotation_key = int(round(float(rotation) / 2.0) * 2) % 360
      key = (name, icon.size, bool(active), int(opacity), rotation_key)
      rotated = self._rotated_icon_cache.get(key)
      if rotated is None:
        rotated = icon.rotate(rotation_key, resample=Image.Resampling.BICUBIC, expand=False)
        self._rotated_icon_cache[key] = rotated
        if len(self._rotated_icon_cache) > 256:
          self._rotated_icon_cache.popitem(last=False)
      else:
        self._rotated_icon_cache.move_to_end(key)
      icon = rotated
    image.paste(icon, (int(cx - icon.width / 2), int(cy - icon.height / 2)), icon)

  def _icon_size_for_height(self, name, height):
    source = self.icons.get(name)
    if source is None or source.height <= 0:
      return int(height), int(height)
    return max(1, int(round(height * source.width / source.height))), int(height)

  def _centered(self, draw, cx, cy, value, font, color, stroke_width=0, stroke_fill=None):
    text = str(value)
    fill = tuple(color) if isinstance(color, (tuple, list)) else color
    # ImageDraw text on an RGB target with an RGBA drawing context ignores the
    # fill alpha (unlike rectangles). Mirror that existing behavior so cached
    # glyphs are pixel-identical to the former direct draw.text() calls.
    if getattr(draw, "mode", None) == "RGBA" and isinstance(fill, tuple) and len(fill) == 4:
      fill = (*fill[:3], 255)
    stroke = tuple(stroke_fill) if isinstance(stroke_fill, (tuple, list)) else stroke_fill
    key = (id(font), text, fill, int(stroke_width), stroke)
    cached = self._text_cache.get(key)
    if cached is None:
      probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
      probe_draw = ImageDraw.Draw(probe)
      bbox = probe_draw.textbbox(
        (0, 0), text, font=font, anchor="mm", stroke_width=stroke_width,
      )
      padding = max(2, int(stroke_width) + 1)
      width = max(1, bbox[2] - bbox[0] + padding * 2)
      height = max(1, bbox[3] - bbox[1] + padding * 2)
      anchor_x = padding - bbox[0]
      anchor_y = padding - bbox[1]
      sprite = Image.new("RGBA", (width, height), (0, 0, 0, 0))
      ImageDraw.Draw(sprite).text(
        (anchor_x, anchor_y), text, font=font, fill=fill, anchor="mm",
        stroke_width=stroke_width, stroke_fill=stroke,
      )
      cached = (sprite, anchor_x, anchor_y)
      self._text_cache[key] = cached
      if len(self._text_cache) > 512:
        self._text_cache.popitem(last=False)
    else:
      self._text_cache.move_to_end(key)

    sprite, anchor_x, anchor_y = cached
    target = draw._image
    target.paste(sprite, (int(cx) - anchor_x, int(cy) - anchor_y), sprite)

  def _value(self, value, disabled="--"):
    try:
      return disabled if value is None or float(value) <= 0 or float(value) >= 255 else str(round(float(value)))
    except (TypeError, ValueError):
      return disabled

  @staticmethod
  def _active_speed_limit(data):
    """Select one coherent speed-limit source for all cluster widgets."""
    if data.get("nda_state"):
      if data.get("section_limit_speed", 0) > 0 and data.get("section_left_dist", 0) > 0:
        return float(data.get("section_limit_speed")), float(data.get("section_left_dist"))
      if data.get("cam_limit_speed", 0) > 0 and data.get("cam_limit_speed_left_dist", 0) > 0:
        return float(data.get("cam_limit_speed")), float(data.get("cam_limit_speed_left_dist"))
      return float(data.get("nav_limit_speed", 0.0) or 0.0), 0.0
    if data.get("stock_limit_speed", 0) > 0:
      return float(data.get("stock_limit_speed")), 0.0
    return float(data.get("nav_limit_speed", 0.0) or 0.0), 0.0

  def _draw_hud(self, image, data, has_camera):
    draw = ImageDraw.Draw(image)
    content_right = self.content_x + self.content_w - 1
    content_bottom = self.content_y + self.content_h - 1

    # All HUD content stays inside the dedicated outer-border rectangle.
    draw.rectangle([self.content_x, self.content_y, self.camera_x - 1, content_bottom], fill=Colors.PANEL)
    draw.rectangle([self.right_aux_x, self.content_y, content_right, content_bottom], fill=Colors.PANEL)
    for x in (self.left_aux_x, self.camera_x, self.right_aux_x, self.right_panel_x):
      draw.line((x, self.content_y, x, content_bottom), fill=Colors.DIVIDER, width=2)
    for row in (1, 2):
      y = int(round(self.content_y + row * self.row_h))
      draw.line((self.content_x, y, self.camera_x - 1, y), fill=Colors.DIVIDER, width=1)
      draw.line((self.right_aux_x, y, content_right, y), fill=Colors.DIVIDER, width=1)

    self._draw_left_panel(image, draw, data, Colors.MUTED_TEXT)
    self._draw_left_aux_panel(image, draw, data, Colors.WHITE, Colors.RED)
    self._draw_camera_overlays(draw, data, Colors.WHITE)
    self._draw_blinkers(image, data)
    self._draw_blind_spot_detect(image, data)
    self._draw_right_aux_panel(image, data)
    self._draw_right_panel(image, draw, data, Colors.RED)

    if not has_camera:
      self._centered(draw, self.camera_x + self.camera_w / 2, self.camera_y + self.camera_h / 2,
                     "WAITING FOR CAMERA SIGNAL...", self.font_warning, Colors.RED)

    status_color = self._get_border_color(data)
    draw.rectangle([0, 0, self.target_w - 1, self.target_h - 1],
                   outline=status_color, width=self.border_size)
    self._draw_ignore_limit_timer(image, data)

  @staticmethod
  def _get_border_color(data):
    steering_pressed = bool(data.get("steering_pressed"))
    enabled = bool(data.get("enabled"))
    lat_active = bool(data.get("lat_active"))

    if data.get("pre_enabled_or_overriding") and not steering_pressed:
      return Colors.OVERRIDE
    elif enabled and not lat_active:
      if steering_pressed:
        return Colors.STEERING
      elif data.get("brake_pressed"):
        return Colors.RED
      elif data.get("left_blinker") or data.get("right_blinker"):
        return Colors.ORANGE
      return Colors.ENGAGED
    elif enabled and lat_active:
      if steering_pressed:
        return Colors.STEERING
      elif data.get("brake_pressed"):
        return Colors.RED
      elif data.get("left_blinker") or data.get("right_blinker"):
        return Colors.ORANGE
      return Colors.ACTIVE
    elif data.get("reverse"):
      return Colors.RED
    elif data.get("cruise_available") and lat_active:
      return Colors.READY
    elif data.get("cruise_available") and not lat_active and float(data.get("v_ego", 0.0) or 0.0) > 0.3:
      return Colors.ORANGE
    return Colors.DISENGAGED

  def _draw_ignore_limit_timer(self, image, data):
    max_ticks = 3000.0
    try:
      timer = float(data.get("ignore_limit_timer", 0.0) or 0.0)
    except (TypeError, ValueError):
      return
    if timer <= 0.0 or timer >= max_ticks:
      return

    ratio = max(0.0, min(1.0, (max_ticks - timer) / max_ticks))
    bar_width = int(round(self.target_w * ratio))
    if bar_width <= 0:
      return
    bar_x = (self.target_w - bar_width) // 2
    bar_draw = ImageDraw.Draw(image, "RGBA")
    bar_draw.rectangle(
      [bar_x, 0, bar_x + bar_width - 1, self.border_size - 1],
      fill=colors_alpha(Colors.ORANGE, 150),
    )

  def _draw_blinkers(self, image, data):
    left_blinker = bool(data.get("left_blinker"))
    right_blinker = bool(data.get("right_blinker"))
    if not (left_blinker or right_blinker):
      self._blink_started_at = None
      return

    now_ms = time.monotonic() * 1000.0
    if self._blink_started_at is None:
      self._blink_started_at = now_ms

    cycle_ms = BLINKER_SEQUENCE_MS + BLINKER_PAUSE_MS
    phase_ms = (now_ms - self._blink_started_at) % cycle_ms
    if phase_ms >= BLINKER_SEQUENCE_MS:
      return

    step_ms = BLINKER_SEQUENCE_MS / BLINKER_DRAW_COUNT
    blink_index = min(BLINKER_DRAW_COUNT - 1, int(phase_ms / step_ms))
    center_x = self.camera_x + self.camera_w / 2
    center_y = self.camera_y + self.camera_h / 2
    alpha_base = 0.8

    for is_active, direction, name in (
      (left_blinker, -1, "turnsignal_l"),
      (right_blinker, 1, "turnsignal_r"),
    ):
      if not is_active:
        continue
      icon_size = self._icon_size_for_height(name, CAMERA_OVERLAY_ICON_HEIGHT)
      icon_w = icon_size[0]
      first_center_x = center_x + direction * icon_w / 2
      spacing = icon_w * 0.6
      for index in range(BLINKER_DRAW_COUNT):
        distance = abs(blink_index - index)
        alpha = alpha_base if distance == 0 else alpha_base / (distance * 2)
        if alpha <= 0.05:
          continue
        icon_x = first_center_x + direction * index * spacing
        self._icon(
          image, name, icon_x, center_y, icon_size, True,
          opacity=int(alpha * 255),
        )

  def _draw_blind_spot_detect(self, image, data):
    center_y = self.camera_y + self.camera_h / 2
    inset = 16
    for is_active, side, name in (
      (bool(data.get("left_blindspot")), -1, "blind_spot_left"),
      (bool(data.get("right_blindspot")), 1, "blind_spot_right"),
    ):
      if not is_active:
        continue
      icon_size = self._icon_size_for_height(name, CAMERA_OVERLAY_ICON_HEIGHT)
      if side < 0:
        icon_x = self.camera_x + inset + icon_size[0] / 2
      else:
        icon_x = self.camera_x + self.camera_w - inset - icon_size[0] / 2
      self._icon(image, name, icon_x, center_y, icon_size, True)

  def _draw_left_panel(self, image, draw, data, muted):
    cx = self.left_panel_x + self.side_w / 2
    cruise = self._value(data.get("cruise_speed"))
    is_cruise_set = bool(data.get("is_cruise_set"))
    set_speed = self._value(data.get("set_speed", data.get("cruise_speed"))) if is_cruise_set else "--"

    max_color = colors_alpha(Colors.WHITE, 200)
    speed_color = colors_alpha(Colors.WHITE, 200)
    if is_cruise_set:
      speed_color = colors_alpha(Colors.WHITE, 255)
      max_color = Colors.MAX_ACTIVE if data.get("enabled") else colors_alpha(Colors.OVERRIDE, 255)

      limit_speed, _ = self._active_speed_limit(data)

      cruise_speed = float(data.get("cruise_speed", 0.0) or 0.0)
      if limit_speed > 0 and data.get("enabled"):
        if cruise_speed > limit_speed + 25:
          speed_color = Colors.RED
        elif cruise_speed > limit_speed + 15:
          speed_color = Colors.ORANGE
        elif cruise_speed > limit_speed + 5:
          speed_color = Colors.CAUTION

    speed_draw = ImageDraw.Draw(image, "RGBA")

    # max box
    self._centered(speed_draw, cx, self.content_y + self.row_h * 0.28, "MAX", self.font_label, max_color)
    self._centered(speed_draw, cx, self.content_y + self.row_h * 0.58, cruise, self.font_speed, speed_color)
    self._centered(draw, cx, self.content_y + self.row_h * 0.79, self.config.speed_unit, self.font_unit, muted)

    # set box
    self._centered(speed_draw, cx, self.content_y + self.row_h * 1.28, "SET", self.font_label, max_color)
    self._centered(speed_draw, cx, self.content_y + self.row_h * 1.58, set_speed, self.font_speed, speed_color)
    self._centered(draw, cx, self.content_y + self.row_h * 1.79, self.config.speed_unit, self.font_unit, muted)

    # wheel icon
    if data.get("enabled") and data.get("steering_pressed"):
      wheel_name = WHEEL_ICONS["steering"]
    elif data.get("left_blinker") or data.get("right_blinker") or data.get("brake_pressed"):
      wheel_name = WHEEL_ICONS["critical"]
    elif data.get("enabled"):
      wheel_name = WHEEL_ICONS["enabled"]
    else:
      wheel_name = WHEEL_ICONS["default"]
    self._icon(image, wheel_name, cx, self.row_centers[2], 94, True,
               rotation=float(data.get("steering_angle", 0.0) or 0.0))

  def _draw_left_aux_panel(self, image, draw, data, white, red):
    left_cx = self.left_aux_x + self.panel_w / 2
    speed_limit_y = self.row_centers[0]
    limit, left_dist = self._active_speed_limit(data)

    # speed limit
    outer_radius = 49
    inner_radius = 39
    draw.ellipse([left_cx - outer_radius, speed_limit_y - outer_radius,
                  left_cx + outer_radius, speed_limit_y + outer_radius], fill=red)
    draw.ellipse([left_cx - inner_radius, speed_limit_y - inner_radius,
                  left_cx + inner_radius, speed_limit_y + inner_radius], fill=white)
    self._centered(draw, left_cx, speed_limit_y, self._value(limit), self.font_value,
                   Colors.SIGN_TEXT)

    # Draw distance from the same source used for the displayed limit.
    if left_dist > 0:
      dist_text = f"{left_dist / 1000:.1f} km" if left_dist >= 1000 else f"{int(left_dist)} m"
      text_y = speed_limit_y + outer_radius + 10
      bbox = draw.textbbox((left_cx, text_y), dist_text, font=self.font_small, anchor="mm")
      padding_x, padding_y = 7, 3
      draw.rounded_rectangle(
        [bbox[0] - padding_x, bbox[1] - padding_y, bbox[2] + padding_x, bbox[3] + padding_y],
        radius=5, fill=Colors.DISTANCE_BADGE,
      )
      self._centered(draw, left_cx, text_y, dist_text, self.font_small, white)

    # road_sign
    road_sign = None
    if data.get("speed_bump"):
      road_sign = "speed_bump"
    elif data.get("school_zone"):
      road_sign = "school_zone"
    elif data.get("speed_camera"):
      road_sign = "speed_camera"
    if road_sign:
      self._icon(image, road_sign, left_cx, self.row_centers[1], 92, True)

    # pedal_icon
    pedal_icon = "brake_pressed" if data.get("brake_pressed") else \
                 "accel_pressed" if data.get("gas_pressed") else None
    if pedal_icon is not None:
      self._icon(image, pedal_icon, left_cx, self.row_centers[2], 94, True)

  def _draw_right_aux_panel(self, image, data):
    wifi = int(data.get("wifi_strength", 0) or 0)
    wifi_name = WIFI_ICONS[min(max(wifi, 1), 4)]
    right_cx = self.right_aux_x + self.panel_w / 2
    self._icon(image, wifi_name, right_cx, self.row_centers[0], 88, wifi > 0)
    self._icon(image, "gps", right_cx, self.row_centers[1], 88,
               data.get("gps_satellites", 0) > 0)
    self._icon(image, "compass", right_cx, self.row_centers[2], 88,
               data.get("gps_satellites", 0) > 0, rotation=float(data.get("gps_bearing", 0.0) or 0.0))

  def _draw_right_panel(self, image, draw, data, red):
    cx = self.right_panel_x + self.panel_w / 2

    # traffic icon
    traffic_name = TRAFFIC_ICONS.get(data.get("traffic_state"), TRAFFIC_ICONS[0])
    self._icon(image, traffic_name, cx, self.row_centers[0], (68, 136), True)

    # distance icon
    gap = min(max(int(data.get("distance_level", 1) or 1), 1), 4)
    gap_name = DISTANCE_ICONS[gap]
    self._icon(image, gap_name, cx, self.row_centers[1], (56, 132), True)

    # tpms icon
    tpms = self._base_icon("tpms", (108, 140), True)
    if tpms is not None:
      tpms_x = int(cx - tpms.width / 2)
      tpms_y = int(self.content_y + self.row_h * 2.0 + 7)
      image.paste(tpms, (tpms_x, tpms_y), tpms)
    raw_pressures = data.get("tpms") or [0, 0, 0, 0]
    pressures = list(raw_pressures)
    for i in range(4):
      pressure = pressures[i] if i < len(pressures) else 0
      try:
        pressure = float(pressure)
      except (TypeError, ValueError):
        pressure = 0.0
      px = cx + (-36 if i % 2 == 0 else 36)
      py = self.content_y + self.row_h * 2.0 + (38 if i < 2 else 115)
      value = "--" if not pressure or pressure < 5 or pressure > 60 else str(round(pressure))
      color = Colors.ORANGE if value == "--" else red if float(pressure) < 31 else Colors.SIGN_TEXT
      self._centered(draw, px, py, value, self.font_small, color)

  def _draw_camera_overlays(self, draw, data, white):
    # current speed
    accel = float(data.get("accel", 0.0) or 0.0)
    if accel > 0:
      alpha = max(80, min(255, int(255 - 180 * min(accel / 3.0, 1.0))))
      speed_color = (alpha, 255, alpha)
    else:
      alpha = max(60, min(255, int(255 - 255 * min(abs(accel) / 4.0, 1.0))))
      speed_color = (255, alpha, alpha)

    conversion = 3.6 if getattr(self.config, "is_metric", True) else 2.236936
    speed = round(max(0.0, float(data.get("v_ego", 0.0) or 0.0) * conversion))
    center_x = self.camera_x + self.camera_w / 2
    # Align the current-speed value with the center of b's speed-limit cell.
    self._centered(draw, center_x, self.row_centers[0],
                   speed, self.font_current_speed, speed_color,
                   stroke_width=2, stroke_fill=Colors.BLACK)
    self._centered(draw, center_x, self.content_y + self.row_h * 0.86, self.config.speed_unit,
                   self.font_current_unit, white, stroke_width=1, stroke_fill=Colors.BLACK)
