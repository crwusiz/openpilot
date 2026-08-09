import os
import cv2
import numpy as np

from PIL import Image, ImageDraw, ImageFont
from openpilot.common.swaglog import cloudlog


class ClusterRenderer:
  def __init__(self, config):
    cloudlog.info("Initializing ClusterRenderer...")
    self.config = config

    self.target_w = config.width
    self.target_h = config.height
    self.side_w = self.target_w // 10
    self.camera_x = self.side_w
    self.camera_w = self.target_w - 2 * self.side_w
    self.cell_w = self.camera_w / 8.0
    self.row_h = self.target_h / 3.0

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
      "wheel", "wheel_green", "wheel_blue", "wheel_critical", "disengage_on_accelerator",
      "brake_disc", "tpms", "gps", "direction", "wifi_strength_low", "wifi_strength_medium",
      "wifi_strength_high", "wifi_strength_full", "traffic_green", "traffic_red", "traffic_off",
      "speed_bump", "school_zone", "speed_camera", "dist1", "dist2", "dist3", "dist4",
    ):
      try:
        self.icons[name] = Image.open(os.path.join(icon_dir, f"{name}.png")).convert("RGBA")
      except Exception:
        pass

  def render(self, camera, models):
    has_camera = camera.has_frame()
    if has_camera:
      frame = self._crop_and_resize(camera.get_frame())
    else:
      frame = self.blank_canvas.copy()

    hud_data = models.get_hud_data()
    if self.config.draw_model_overlay and models.is_valid():
      frame = self._draw_model_path(frame, models.get_path_data(), hud_data)

    pil_img = Image.fromarray(frame)
    self._draw_hud(pil_img, hud_data, has_camera)
    return np.array(pil_img)

  def _crop_and_resize(self, frame):
    camera = cv2.resize(frame, (self.camera_w, self.target_h), interpolation=cv2.INTER_AREA)
    canvas = self.blank_canvas.copy()
    canvas[:, self.camera_x:self.camera_x + self.camera_w] = camera
    return canvas

  def _project_pt(self, x, y, z):
    if x < 0.1:
      x = 0.1
    px_y = int((self.target_h / 2) + (self.focal_length * (self.camera_height + z) / x))
    px_x = int(self.camera_x + self.camera_w / 2 - (self.focal_length * y / x))
    return px_x, px_y

  def _draw_model_path(self, frame, path_data, hud_data):
    overlay = frame.copy()
    if not path_data["path_x"]:
      return frame

    if hud_data["enabled"]:
      pts_left, pts_right = [], []
      path_width = 1.8
      for x, y in zip(path_data["path_x"], path_data["path_y"]):
        if x > 50.0:
          break
        pts_left.append(self._project_pt(x, y + path_width / 2, 0))
        pts_right.append(self._project_pt(x, y - path_width / 2, 0))
      if pts_left and pts_right:
        poly_pts = np.array(pts_left + list(reversed(pts_right)), dtype=np.int32)
        cv2.fillPoly(overlay, [poly_pts], self.config.colors["path_active"])
        cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)

    lane_color = self.config.colors["lane_line"]
    for xs, ys in ((path_data["left_lane_x"], path_data["left_lane_y"]),
                   (path_data["right_lane_x"], path_data["right_lane_y"])):
      pts = [self._project_pt(x, y, 0) for x, y in zip(xs, ys) if x <= 50.0]
      if len(pts) > 1:
        cv2.polylines(frame, [np.array(pts, dtype=np.int32)], False, lane_color, thickness=4)

    frame[:, :self.camera_x] = self.blank_canvas[:, :self.camera_x]
    frame[:, self.camera_x + self.camera_w:] = self.blank_canvas[:, self.camera_x + self.camera_w:]
    return frame

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
    green = (110, 235, 150)
    blue = (100, 190, 255)
    amber = (255, 195, 80)
    red = (255, 105, 95)
    panel = (7, 12, 18)
    divider = (42, 54, 68)

    # Opaque 1:8:1 panels and light guides for the 8 x 3 centre grid.
    draw.rectangle([0, 0, self.side_w - 1, self.target_h], fill=panel)
    draw.rectangle([self.camera_x + self.camera_w, 0, self.target_w, self.target_h], fill=panel)
    draw.line((self.camera_x, 0, self.camera_x, self.target_h), fill=divider, width=2)
    draw.line((self.camera_x + self.camera_w, 0, self.camera_x + self.camera_w, self.target_h), fill=divider, width=2)
    for row in (1, 2):
      y = int(row * self.row_h)
      draw.line((0, y, self.side_w, y), fill=divider, width=1)
      draw.line((self.camera_x + self.camera_w, y, self.target_w, y), fill=divider, width=1)

    self._draw_left_panel(image, draw, data, white, muted, green, blue)
    self._draw_right_panel(image, draw, data, white, muted, red)
    self._draw_camera_overlays(image, draw, data, white, muted, green, amber, red)

    if not has_camera:
      self._centered(draw, self.camera_x + self.camera_w / 2, self.target_h / 2,
                     "WAITING FOR CAMERA SIGNAL...", self.font_warning, red)

    status_color = self.config.colors["engaged"] if data["enabled"] else self.config.colors["disengaged"]
    draw.rectangle([0, 0, self.target_w - 1, self.target_h - 1], outline=status_color, width=6)

  def _draw_left_panel(self, image, draw, data, white, muted, green, blue):
    cx = self.side_w / 2
    cruise = self._value(data.get("cruise_speed"))
    set_speed = self._value(data.get("set_speed", data.get("cruise_speed")))

    self._centered(draw, cx, self.row_h * 0.28, "CRUISE", self.font_label, muted)
    self._centered(draw, cx, self.row_h * 0.58, cruise, self.font_speed, white)
    self._centered(draw, cx, self.row_h * 0.79, self.config.speed_unit, self.font_unit, muted)

    self._centered(draw, cx, self.row_h * 1.28, "SET", self.font_label, muted)
    self._centered(draw, cx, self.row_h * 1.58, set_speed, self.font_speed,
                   green if data.get("enabled") else blue)
    self._centered(draw, cx, self.row_h * 1.79, self.config.speed_unit, self.font_unit, muted)

    if data.get("left_blinker") or data.get("right_blinker") or data.get("brake_pressed"):
      wheel_name, wheel_color = "wheel_critical", (255, 105, 95)
    elif data.get("enabled"):
      wheel_name, wheel_color = "wheel_green", green
    else:
      wheel_name, wheel_color = "wheel", muted
    self._icon(image, wheel_name, cx, self.row_h * 2.52, 94, True,
               rotation=-float(data.get("steering_angle", 0.0) or 0.0))
    self._centered(draw, cx, self.row_h * 2.91, "STEER", self.font_label, wheel_color)

  def _draw_right_panel(self, image, draw, data, white, muted, red):
    cx = self.camera_x + self.camera_w + self.side_w / 2
    traffic_name = {1: "traffic_red", 2: "traffic_green"}.get(data.get("traffic_state"), "traffic_off")
    self._icon(image, traffic_name, cx, self.row_h * 0.50, (68, 136), True)

    gap = int(data.get("distance_level", 0) or 0)
    gap_name = f"dist{min(max(gap + 1, 1), 4)}"
    self._icon(image, gap_name, cx, self.row_h * 1.50, (56, 132), True)

    tpms = self._base_icon("tpms", (102, 132), True)
    if tpms is not None:
      image.paste(tpms, (int(cx - tpms.width / 2), int(self.row_h * 2.04)), tpms)
    raw_pressures = data.get("tpms") or [0, 0, 0, 0]
    pressures = list(raw_pressures)
    for i in range(4):
      pressure = pressures[i] if i < len(pressures) else 0
      try:
        pressure = float(pressure)
      except (TypeError, ValueError):
        pressure = 0.0
      px = cx + (-30 if i % 2 == 0 else 30)
      py = self.row_h * 2.27 + (i // 2) * 65
      value = "--" if not pressure or pressure < 5 or pressure > 60 else str(round(pressure))
      color = red if value != "--" and float(pressure) < 31 else white
      self._centered(draw, px, py, value, self.font_small, color)

  def _draw_camera_overlays(self, image, draw, data, white, muted, green, amber, red):
    self._draw_current_speed(draw, data, white)

    left_cx = self.camera_x + self.cell_w / 2
    self._draw_speed_limit(draw, left_cx, self.row_h * 0.48, data, white, red)

    road_sign = None
    if data.get("road_signs") == 1 or data.get("school_zone"):
      road_sign = "school_zone"
    elif data.get("speed_bump"):
      road_sign = "speed_bump"
    elif data.get("speed_camera"):
      road_sign = "speed_camera"
    if road_sign:
      self._icon(image, road_sign, left_cx, self.row_h * 1.50, 92, True)
      self._centered(draw, left_cx, self.row_h * 1.91, "ROAD SIGN", self.font_label, muted)

    accel_cx = self.camera_x + self.cell_w * 0.5
    brake_cx = self.camera_x + self.cell_w * 1.5
    self._icon(image, "disengage_on_accelerator", accel_cx, self.row_h * 2.50, 94,
               bool(data.get("gas_pressed")))
    self._icon(image, "brake_disc", brake_cx, self.row_h * 2.50, 94,
               bool(data.get("brake_pressed")))
    self._centered(draw, accel_cx, self.row_h * 2.91, "ACCEL", self.font_label,
                   amber if data.get("gas_pressed") else muted)
    self._centered(draw, brake_cx, self.row_h * 2.91, "BRAKE", self.font_label,
                   red if data.get("brake_pressed") else muted)

    icon_y = self.row_h * 0.47
    wifi = data.get("wifi_strength", 0)
    wifi_name = "wifi_strength_full" if wifi >= 4 else "wifi_strength_high" if wifi == 3 else \
                "wifi_strength_medium" if wifi == 2 else "wifi_strength_low"
    compass_cx = self.camera_x + self.cell_w * 6.15
    gps_cx = self.camera_x + self.cell_w * 6.75
    wifi_cx = self.camera_x + self.cell_w * 7.35
    self._icon(image, "direction", compass_cx, icon_y, 88,
               data.get("gps_satellites", 0) > 0, rotation=float(data.get("gps_bearing", 0.0) or 0.0))
    self._icon(image, "gps", gps_cx, icon_y, 88,
               data.get("gps_satellites", 0) > 0)
    self._icon(image, wifi_name, wifi_cx, icon_y, 88, wifi > 0)
    self._centered(draw, compass_cx, self.row_h * 0.91, "COMPASS", self.font_label, muted)
    self._centered(draw, gps_cx, self.row_h * 0.91, "GPS", self.font_label, muted)
    self._centered(draw, wifi_cx, self.row_h * 0.91, "WIFI", self.font_label, muted)

  def _draw_current_speed(self, draw, data, white):
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

  def _draw_speed_limit(self, draw, cx, cy, data, white, red):
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
    radius = 39
    draw.ellipse([cx - radius - 8, cy - radius - 8, cx + radius + 8, cy + radius + 8], fill=white)
    draw.ellipse([cx - radius - 6, cy - radius - 6, cx + radius + 6, cy + radius + 6], outline=red, width=6)
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=white)
    self._centered(draw, cx, cy, self._value(limit), self.font_value, (20, 25, 30))
