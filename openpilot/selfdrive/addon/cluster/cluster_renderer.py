import os
import time
import cv2
import numpy as np

from PIL import Image, ImageDraw, ImageFont
from openpilot.common.swaglog import cloudlog


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
  "default": "wheel",
  "enabled": "wheel_green",
  "steering": "wheel_blue",
  "critical": "wheel_critical",
}

TRAFFIC_ICONS = {
  0: "traffic_off",
  1: "traffic_red",
  2: "traffic_green",
}


class ClusterRenderer:
  def __init__(self, config):
    cloudlog.info("Initializing ClusterRenderer...")
    self.config = config

    self.target_w = config.width
    self.target_h = config.height
    # Five horizontal regions: a:b:c:d:e = 1:1:6:1:1.
    self.panel_w = self.target_w // 10
    self.side_w = self.panel_w
    self.left_aux_x = self.panel_w
    self.camera_x = self.panel_w * 2
    self.camera_w = self.panel_w * 6
    self.right_aux_x = self.camera_x + self.camera_w
    self.right_panel_x = self.right_aux_x + self.panel_w
    self.row_h = self.target_h / 3.0
    self._source_to_panel = np.eye(3, dtype=np.float32)

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

    self.blank_canvas = np.zeros((self.target_h, self.target_w, 3), dtype=np.uint8)
    self.camera_height = 1.22
    self.focal_length = 910.0
    self.icons = {}
    self._icon_cache = {}
    icon_dir = os.path.join(str(self.config.BASEDIR), "selfdrive", "assets", "icons")
    for name in (
      *WHEEL_ICONS.values(), *WIFI_ICONS.values(),
      *TRAFFIC_ICONS.values(), *DISTANCE_ICONS.values(),
      "accel_pressed", "brake_pressed", "tpms", "gps", "compass",
      "speed_bump", "school_zone", "speed_camera",
    ):
      try:
        self.icons[name] = Image.open(os.path.join(icon_dir, f"{name}.png")).convert("RGBA")
      except Exception:
        pass

  def render(self, camera, models):
    has_camera = camera.has_frame()
    if has_camera:
      camera_frame = camera.get_frame()
      if camera_frame.shape[:2] == (self.target_h, self.camera_w) and \
          hasattr(camera, "get_source_to_panel_transform"):
        frame = self.blank_canvas.copy()
        frame[:, self.camera_x:self.camera_x + self.camera_w] = camera_frame
        self._source_to_panel = camera.get_source_to_panel_transform(self.camera_x)
      else:
        frame = self._crop_and_resize(camera_frame)
    else:
      frame = self.blank_canvas.copy()

    hud_data = models.get_hud_data()
    if has_camera and models.is_valid():
      frame = self._draw_model_path(frame, models.get_path_data(), hud_data)

    pil_img = Image.fromarray(frame)
    self._draw_hud(pil_img, hud_data, has_camera)
    return np.asarray(pil_img)

  def _crop_and_resize(self, frame):
    source_h, source_w = frame.shape[:2]
    source_aspect = source_w / source_h
    target_aspect = self.camera_w / self.target_h
    if source_aspect < target_aspect:
      crop_w = source_w
      crop_h = max(1, int(round(source_w / target_aspect)))
    else:
      crop_w = max(1, int(round(source_h * target_aspect)))
      crop_h = source_h
    crop_x = (source_w - crop_w) // 2
    crop_y = (source_h - crop_h) // 2
    cropped = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
    camera = cv2.resize(cropped, (self.camera_w, self.target_h), interpolation=cv2.INTER_AREA)

    scale_x = self.camera_w / crop_w
    scale_y = self.target_h / crop_h
    self._source_to_panel = np.array([
      [scale_x, 0.0, self.camera_x - crop_x * scale_x],
      [0.0, scale_y, -crop_y * scale_y],
      [0.0, 0.0, 1.0],
    ], dtype=np.float32)
    canvas = self.blank_canvas.copy()
    canvas[:, self.camera_x:self.camera_x + self.camera_w] = camera
    return canvas

  def _project_points(self, points, calib_transform):
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
        (pixels[:, 0] >= self.camera_x) &
        (pixels[:, 0] < self.camera_x + self.camera_w) &
        (pixels[:, 1] >= 0) &
        (pixels[:, 1] < self.target_h)
      )
    return pixels, valid

  def _project_ribbon(self, xs, ys, zs, width, z_offset, calib_transform, max_distance=100.0):
    count = min(len(xs), len(ys), len(zs))
    if count < 2:
      return None
    raw = np.column_stack((xs[:count], ys[:count], zs[:count])).astype(np.float32)
    raw = raw[(raw[:, 0] >= 0.0) & (raw[:, 0] <= max_distance)]
    if len(raw) < 2:
      return None
    left = raw + np.array([0.0, -width, z_offset], dtype=np.float32)
    right = raw + np.array([0.0, width, z_offset], dtype=np.float32)
    left_px, left_valid = self._project_points(left, calib_transform)
    right_px, right_valid = self._project_points(right, calib_transform)
    valid = left_valid & right_valid
    if np.count_nonzero(valid) < 2:
      return None
    return np.vstack((left_px[valid], right_px[valid][::-1])).astype(np.int32)

  def _draw_model_path(self, frame, path_data, hud_data):
    if not path_data["path_x"]:
      return frame
    calib_transform = path_data.get("calib_transform")
    if calib_transform is None:
      return frame

    camera_region = frame[:, self.camera_x:self.camera_x + self.camera_w]
    camera_height = float(path_data.get("camera_height", self.camera_height) or self.camera_height)
    path_z = path_data.get("path_z") or [0.0] * len(path_data["path_x"])
    path_poly = self._project_ribbon(
      path_data["path_x"], path_data["path_y"], path_z,
      0.9, camera_height, calib_transform, max_distance=100.0,
    )
    if path_poly is not None and hud_data["enabled"]:
      overlay = camera_region.copy()
      local_path_poly = path_poly - np.array([self.camera_x, 0], dtype=np.int32)
      cv2.fillPoly(overlay, [local_path_poly], self.config.colors["path_active"])
      cv2.addWeighted(overlay, 0.35, camera_region, 0.65, 0, camera_region)

    lane_lines = path_data.get("lane_lines") or []
    lane_probs = path_data.get("lane_line_probs") or []
    feature_overlay = None
    features_drawn = False
    for index, line in enumerate(lane_lines):
      if len(line) != 3:
        continue
      probability = float(lane_probs[index]) if index < len(lane_probs) else 0.0
      if probability < 0.05:
        continue
      width = (0.16 if index in (1, 2) else 0.12) * probability
      polygon = self._project_ribbon(*line, width, 0.0, calib_transform)
      if polygon is not None:
        if feature_overlay is None:
          feature_overlay = camera_region.copy()
        base_color = self.config.colors["path_active"] if hud_data["enabled"] and index in (1, 2) else self.config.colors["lane_line"]
        color = tuple(int(component * (0.35 + probability * 0.65)) for component in base_color)
        local_polygon = polygon - np.array([self.camera_x, 0], dtype=np.int32)
        cv2.fillPoly(feature_overlay, [local_polygon], color)
        features_drawn = True

    road_edges = path_data.get("road_edges") or []
    road_stds = path_data.get("road_edge_stds") or []
    for index, edge in enumerate(road_edges):
      if len(edge) != 3:
        continue
      confidence = 1.0 - float(road_stds[index]) if index < len(road_stds) else 0.0
      if confidence < 0.2:
        continue
      polygon = self._project_ribbon(*edge, 0.12, 0.0, calib_transform)
      if polygon is not None:
        if feature_overlay is None:
          feature_overlay = camera_region.copy()
        level = int(100 + 115 * min(confidence, 1.0))
        local_polygon = polygon - np.array([self.camera_x, 0], dtype=np.int32)
        cv2.fillPoly(feature_overlay, [local_polygon], (level, level, level))
        features_drawn = True

    if features_drawn:
      cv2.addWeighted(feature_overlay, 0.75, camera_region, 0.25, 0, camera_region)

    self._draw_leads(frame, path_data, calib_transform, camera_height)

    return frame

  def _draw_leads(self, frame, path_data, calib_transform, camera_height):
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
      cv2.rectangle(frame, (x - half_width, y - 32), (x + half_width, y + 32), (12, 12, 12), -1)
      cv2.line(frame, (x - half_width, y), (x + half_width, y), color, 6)
      cv2.putText(frame, f"{distance:.0f}m", (x - 22, y - 9), cv2.FONT_HERSHEY_SIMPLEX,
                  0.42, (245, 248, 252), 1, cv2.LINE_AA)

  def _base_icon(self, name, size, active=True):
    source = self.icons.get(name)
    if source is None:
      return None
    dimensions = (int(size), int(size)) if isinstance(size, (int, float)) else tuple(map(int, size))
    key = (name, dimensions, bool(active))
    image = self._icon_cache.get(key)
    if image is None:
      image = source.resize(dimensions, Image.Resampling.LANCZOS)
      image = image.copy()
      if not active:
        alpha = image.getchannel("A").point(lambda p: p * 70 // 255)
        image.putalpha(alpha)
      self._icon_cache[key] = image
    return image

  def _icon(self, image, name, cx, cy, size, active=True, rotation=0.0):
    icon = self._base_icon(name, size, active)
    if icon is None:
      return
    if rotation:
      icon = icon.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=False)
    image.paste(icon, (int(cx - icon.width / 2), int(cy - icon.height / 2)), icon)

  @staticmethod
  def _centered(draw, cx, cy, value, font, color):
    draw.text((int(cx), int(cy)), str(value), font=font, fill=color, anchor="mm")

  def _value(self, value, disabled="--"):
    try:
      return disabled if value is None or float(value) <= 0 or float(value) >= 255 else str(round(float(value)))
    except (TypeError, ValueError):
      return disabled

  def _draw_hud(self, image, data, has_camera):
    draw = ImageDraw.Draw(image)
    white = (245, 248, 252)
    muted = (120, 132, 148)
    red = (255, 105, 95)
    panel = (7, 12, 18)
    divider = (42, 54, 68)

    # Opaque a/b and d/e panels around the 6/10-wide camera region.
    draw.rectangle([0, 0, self.camera_x - 1, self.target_h], fill=panel)
    draw.rectangle([self.right_aux_x, 0, self.target_w, self.target_h], fill=panel)
    for x in (self.left_aux_x, self.camera_x, self.right_aux_x, self.right_panel_x):
      draw.line((x, 0, x, self.target_h), fill=divider, width=2)
    for row in (1, 2):
      y = int(row * self.row_h)
      draw.line((0, y, self.camera_x, y), fill=divider, width=1)
      draw.line((self.right_aux_x, y, self.target_w, y), fill=divider, width=1)

    self._draw_left_panel(image, draw, data, muted)
    self._draw_left_aux_panel(image, draw, data, white, red)
    self._draw_camera_overlays(draw, data, white)
    self._draw_right_aux_panel(image, data)
    self._draw_right_panel(image, draw, data, red)

    if not has_camera:
      self._centered(draw, self.camera_x + self.camera_w / 2, self.target_h / 2,
                     "WAITING FOR CAMERA SIGNAL...", self.font_warning, red)

    status_color = self.config.colors["engaged"] if data["enabled"] else self.config.colors["disengaged"]
    draw.rectangle([0, 0, self.target_w - 1, self.target_h - 1], outline=status_color, width=6)
    self._draw_status_borders(draw, data)
    self._draw_ignore_limit_timer(image, data)

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
    bar_draw.rectangle([bar_x, 0, bar_x + bar_width - 1, 9], fill=(255, 149, 0, 150))

  def _draw_status_borders(self, draw, data):
    """Match mici's independent blinker/blind-spot side borders."""
    blinking = (time.monotonic() % 0.9) < 0.45
    border_size = 20
    red = (255, 80, 70)
    orange = (255, 170, 55)
    green = (90, 220, 120)

    def draw_side(x, blinker, blindspot):
      if data.get("brake_pressed") or blindspot:
        color = red
      elif blinker and blinking:
        color = orange
      elif data.get("enabled"):
        color = green
      else:
        return
      draw.rectangle([x, 0, x + border_size - 1, self.target_h - 1], fill=color)

    draw_side(0, data.get("left_blinker"), data.get("left_blindspot"))
    draw_side(self.target_w - border_size, data.get("right_blinker"), data.get("right_blindspot"))

  def _draw_left_panel(self, image, draw, data, muted):
    cx = self.side_w / 2
    cruise = self._value(data.get("cruise_speed"))
    is_cruise_set = bool(data.get("is_cruise_set"))
    set_speed = self._value(data.get("set_speed", data.get("cruise_speed"))) if is_cruise_set else "--"

    max_color = (255, 255, 255, 200)
    speed_color = (255, 255, 255, 200)
    if is_cruise_set:
      speed_color = (255, 255, 255, 255)
      max_color = (128, 216, 166, 255) if data.get("enabled") else (145, 155, 149, 255)

      if data.get("nda_state"):
        if data.get("cam_limit_speed", 0) > 0 and data.get("cam_limit_speed_left_dist", 0) > 0:
          limit_speed = float(data.get("cam_limit_speed"))
        elif data.get("section_limit_speed", 0) > 0 and data.get("section_left_dist", 0) > 0:
          limit_speed = float(data.get("section_limit_speed"))
        else:
          limit_speed = float(data.get("nav_limit_speed", 0.0) or 0.0)
      elif data.get("stock_limit_speed", 0) > 0:
        limit_speed = float(data.get("stock_limit_speed"))
      else:
        limit_speed = float(data.get("nav_limit_speed", 0.0) or 0.0)

      cruise_speed = float(data.get("cruise_speed", 0.0) or 0.0)
      if limit_speed > 0 and data.get("enabled"):
        if cruise_speed > limit_speed + 25:
          speed_color = (201, 34, 49, 255)
        elif cruise_speed > limit_speed + 15:
          speed_color = (255, 149, 0, 255)
        elif cruise_speed > limit_speed + 5:
          speed_color = (255, 200, 100, 255)

    speed_draw = ImageDraw.Draw(image, "RGBA")

    # max box
    self._centered(speed_draw, cx, self.row_h * 0.28, "MAX", self.font_label, max_color)
    self._centered(speed_draw, cx, self.row_h * 0.58, cruise, self.font_speed, speed_color)
    self._centered(draw, cx, self.row_h * 0.79, self.config.speed_unit, self.font_unit, muted)

    # set box
    self._centered(speed_draw, cx, self.row_h * 1.28, "SET", self.font_label, max_color)
    self._centered(speed_draw, cx, self.row_h * 1.58, set_speed, self.font_speed, speed_color)
    self._centered(draw, cx, self.row_h * 1.79, self.config.speed_unit, self.font_unit, muted)

    # wheel icon
    if data.get("left_blinker") or data.get("right_blinker") or data.get("brake_pressed"):
      wheel_name = WHEEL_ICONS["critical"]
    elif data.get("enabled"):
      wheel_name = WHEEL_ICONS["enabled"]
    else:
      wheel_name = WHEEL_ICONS["default"]
    self._icon(image, wheel_name, cx, self.row_h * 2.50, 94, True,
               rotation=float(data.get("steering_angle", 0.0) or 0.0))

  def _draw_left_aux_panel(self, image, draw, data, white, red):
    left_cx = self.left_aux_x + self.panel_w / 2
    speed_limit_y = self.row_h * 0.50
    if data.get("nda_state"):
      if data.get("cam_limit_speed", 0) > 0 and data.get("cam_limit_speed_left_dist", 0) > 0:
        limit = float(data.get("cam_limit_speed"))
      elif data.get("section_limit_speed", 0) > 0 and data.get("section_left_dist", 0) > 0:
        limit = float(data.get("section_limit_speed"))
      else:
        limit = float(data.get("nav_limit_speed", 0.0) or 0.0)
    elif data.get("stock_limit_speed", 0) > 0:
      limit = float(data.get("stock_limit_speed"))
    else:
      limit = float(data.get("nav_limit_speed", 0.0) or 0.0)

    # speed limit
    outer_radius = 49
    inner_radius = 39
    draw.ellipse([left_cx - outer_radius, speed_limit_y - outer_radius,
                  left_cx + outer_radius, speed_limit_y + outer_radius], fill=red)
    draw.ellipse([left_cx - inner_radius, speed_limit_y - inner_radius,
                  left_cx + inner_radius, speed_limit_y + inner_radius], fill=white)
    self._centered(draw, left_cx, speed_limit_y, self._value(limit), self.font_value, (20, 25, 30))

    # Draw distance if available, matching the main HUD's camera/section priority.
    left_dist = 0.0
    if data.get("cam_limit_speed", 0) > 0 and data.get("cam_limit_speed_left_dist", 0) > 0:
      left_dist = float(data.get("cam_limit_speed_left_dist"))
    elif data.get("section_limit_speed", 0) > 0 and data.get("section_left_dist", 0) > 0:
      left_dist = float(data.get("section_left_dist"))

    if left_dist > 0:
      dist_text = f"{left_dist / 1000:.1f} km" if left_dist >= 1000 else f"{int(left_dist)} m"
      text_y = speed_limit_y + outer_radius + 10
      bbox = draw.textbbox((left_cx, text_y), dist_text, font=self.font_small, anchor="mm")
      padding_x, padding_y = 7, 3
      draw.rounded_rectangle(
        [bbox[0] - padding_x, bbox[1] - padding_y, bbox[2] + padding_x, bbox[3] + padding_y],
        radius=5, fill=(18, 25, 34),
      )
      self._centered(draw, left_cx, text_y, dist_text, self.font_small, white)

    # road_sign
    road_sign = None
    if data.get("road_signs") == 1 or data.get("school_zone"):
      road_sign = "school_zone"
    elif data.get("speed_bump"):
      road_sign = "speed_bump"
    elif data.get("speed_camera"):
      road_sign = "speed_camera"
    if road_sign:
      self._icon(image, road_sign, left_cx, self.row_h * 1.50, 92, True)

    # pedal_icon
    pedal_icon = "brake_pressed" if data.get("brake_pressed") else \
                 "accel_pressed" if data.get("gas_pressed") else None
    if pedal_icon is not None:
      self._icon(image, pedal_icon, left_cx, self.row_h * 2.50, 94, True)

  def _draw_right_aux_panel(self, image, data):
    wifi = int(data.get("wifi_strength", 0) or 0)
    wifi_name = WIFI_ICONS[min(max(wifi, 1), 4)]
    right_cx = self.right_aux_x + self.panel_w / 2
    self._icon(image, wifi_name, right_cx, self.row_h * 0.50, 88, wifi > 0)
    self._icon(image, "gps", right_cx, self.row_h * 1.50, 88,
               data.get("gps_satellites", 0) > 0)
    self._icon(image, "compass", right_cx, self.row_h * 2.50, 88,
               data.get("gps_satellites", 0) > 0, rotation=float(data.get("gps_bearing", 0.0) or 0.0))

  def _draw_right_panel(self, image, draw, data, red):
    cx = self.right_panel_x + self.panel_w / 2

    # traffic icon
    traffic_name = TRAFFIC_ICONS.get(data.get("traffic_state"), TRAFFIC_ICONS[0])
    self._icon(image, traffic_name, cx, self.row_h * 0.50, (68, 136), True)

    # distance icon
    gap = min(max(int(data.get("distance_level", 1) or 1), 1), 4)
    gap_name = DISTANCE_ICONS[gap]
    self._icon(image, gap_name, cx, self.row_h * 1.50, (56, 132), True)

    # tpms icon
    tpms = self._base_icon("tpms", (108, 140), True)
    if tpms is not None:
      tpms_x = int(cx - tpms.width / 2)
      tpms_y = int(self.row_h * 2.0 + 7)
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
      py = self.row_h * 2.0 + (38 if i < 2 else 115)
      value = "--" if not pressure or pressure < 5 or pressure > 60 else str(round(pressure))
      color = (230, 150, 45) if value == "--" else red if float(pressure) < 31 else (20, 25, 30)
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
    self._centered(draw, center_x, self.row_h * 0.29, speed, self.font_current_speed, speed_color)
    self._centered(draw, center_x, self.row_h * 0.69, self.config.speed_unit,
                   self.font_current_unit, white)
