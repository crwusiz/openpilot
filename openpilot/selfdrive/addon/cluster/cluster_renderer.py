import os
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from openpilot.common.swaglog import cloudlog


class ClusterRenderer:
  def __init__(self, config):
    cloudlog.info("Initializing ClusterRenderer...")
    self.config = config

    # 9.2인치 순정 규격인 1920x462로 도화지 고정
    self.target_w = 1920
    self.target_h = 462

    try:
      self.font_speed = ImageFont.truetype(self.config.font_bold, 110)
      self.font_unit = ImageFont.truetype(self.config.font_regular, 38)
      self.font_warning = ImageFont.truetype(self.config.font_bold, 55)
    except Exception as e:
      cloudlog.error(f"Failed to load fonts: {e}")
      self.font_speed = ImageFont.load_default()
      self.font_unit = ImageFont.load_default()
      self.font_warning = ImageFont.load_default()

    self.blank_canvas = np.zeros((self.target_h, self.target_w, 3), dtype=np.uint8)
    self.camera_height = 1.22
    self.focal_length = 910.0
    self.icons = {}
    icon_dir = os.path.join(str(self.config.BASEDIR), "selfdrive", "assets", "icons")
    for name in (
      "wheel", "wheel_green", "disengage_on_accelerator", "brake_disc", "tpms",
      "gps", "direction", "wifi_strength_low", "wifi_strength_medium",
      "wifi_strength_high", "wifi_strength_full", "traffic_green", "traffic_red",
      "traffic_off", "speed_bump", "school_zone", "speed_camera",
    ):
      try:
        self.icons[name] = Image.open(os.path.join(icon_dir, f"{name}.png")).convert("RGBA")
      except Exception:
        pass

  def render(self, camera, models):
    has_camera = camera.has_frame()
    if has_camera:
      frame = camera.get_frame()
      frame = self._crop_and_resize(frame)
    else:
      frame = self.blank_canvas.copy()

    if self.config.draw_model_overlay and models.is_valid():
      frame = self._draw_model_path(frame, models.get_path_data(), models.get_hud_data())

    pil_img = Image.fromarray(frame)
    hud_data = models.get_hud_data()
    self._draw_info_panel(pil_img, hud_data)
    self._draw_hud(pil_img, hud_data, has_camera)

    return np.array(pil_img)

  def _crop_and_resize(self, frame):
    h, w = frame.shape[:2]
    # Preserve the complete source camera image in the left pane. Cropping to
    # fill even this 2.08:1 viewport still enlarged the road view noticeably.
    view_w = min(max(1, self.config.camera_panel_width), self.target_w)
    scale = min(view_w / w, self.target_h / h)
    resized_w = max(1, round(w * scale))
    resized_h = max(1, round(h * scale))
    resized = cv2.resize(frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((self.target_h, self.target_w, 3), dtype=np.uint8)
    offset_x = (view_w - resized_w) // 2
    offset_y = (self.target_h - resized_h) // 2
    canvas[offset_y:offset_y + resized_h, offset_x:offset_x + resized_w] = resized
    return canvas

  def _draw_info_panel(self, pil_img, data):
    """Draw the right-hand 5:5 information pane."""
    draw = ImageDraw.Draw(pil_img)
    x0 = self.config.camera_panel_width
    w = self.target_w - x0
    draw.rectangle([x0 + 5, 5, self.target_w - 5, self.target_h - 5], fill=(7, 12, 19))

    def text(x, y, value, size=24, color=(220, 225, 232)):
      draw.text((x, y), str(value), font=self.font_unit if size <= 38 else self.font_speed, fill=color)

    def icon(name, x, y, size=52):
      source = self.icons.get(name)
      if source is not None:
        image = source.resize((size, size), Image.Resampling.LANCZOS)
        pil_img.paste(image, (int(x), int(y)), image)

    def indicator(x, y, label, active, icon_name, active_color=(80, 220, 130)):
      color = active_color if active else (75, 82, 92)
      draw.rounded_rectangle([x, y, x + 142, y + 64], radius=8, fill=(19, 27, 38), outline=color, width=2)
      icon(icon_name, x + 7, y + 6, 46)
      text(x + 58, y + 20, label, 20, color)

    # Top row: current/set speed and core pedal/steering status.
    current = data.get("v_ego", 0.0) * (3.6 if self.config.is_metric else 2.236936)
    cruise = data.get("cruise_speed", 0.0)
    if cruise > 100:  # vCruise fields are commonly km/h*100 in some variants
      cruise /= 100.0
    text(x0 + 24, 20, f"{int(current)} {self.config.speed_unit}", 34, (255, 255, 255))
    text(x0 + 260, 20, f"SET {int(cruise) if cruise > 0 else '--'}", 30, (160, 210, 255))
    text(x0 + 24, 62, f"STEER {data.get('steering_angle', 0.0):+.1f} deg", 24)

    indicator(x0 + 24, 105, "WHEEL", data.get("enabled", False),
              "wheel_green" if data.get("enabled", False) else "wheel")
    indicator(x0 + 180, 105, "BRAKE", data.get("brake_pressed", False), "brake_disc", (255, 100, 90))
    indicator(x0 + 336, 105, "ACCEL", data.get("gas_pressed", False), "disengage_on_accelerator", (255, 190, 80))

    # TPMS grid.
    draw.rounded_rectangle([x0 + 24, 164, x0 + 470, 292], radius=10, fill=(12, 19, 29), outline=(65, 78, 95), width=2)
    icon("tpms", x0 + 35, 177, 72)
    text(x0 + 42, 174, "TPMS  FL       FR       RL       RR", 24, (180, 190, 205))
    pressures = data.get("tpms", [0, 0, 0, 0])
    text(x0 + 42, 222, "  ".join(f"{p:.1f}" if p else "--" for p in pressures), 30, (235, 240, 245))
    text(x0 + 42, 262, "tire pressure", 20, (120, 130, 145))

    # Connectivity/navigation status.
    gps = data.get("gps_satellites", 0)
    wifi = data.get("wifi_strength", 0)
    icon("gps", x0 + 24, 312, 38)
    icon("direction", x0 + 235, 312, 38)
    wifi_icon = ("wifi_strength_full" if wifi >= 4 else "wifi_strength_high" if wifi == 3 else
                 "wifi_strength_medium" if wifi == 2 else "wifi_strength_low")
    icon(wifi_icon, x0 + 24, 352, 38)
    text(x0 + 70, 320, f"{'OK' if gps > 0 else 'NO SIGNAL'}  SAT {gps}", 24,
         (100, 230, 140) if gps > 0 else (255, 120, 100))
    text(x0 + 280, 320, f"{data.get('gps_bearing', 0.0):.0f} deg", 24, (205, 215, 230))
    text(x0 + 70, 360, f"{'CONNECTED' if wifi > 0 else 'OFF'} ({wifi})", 24,
         (100, 210, 255) if wifi > 0 else (130, 140, 150))

    # Road-sign/traffic indicators. Numeric enums are retained until the
    # vehicle-specific enum mapping is finalized.
    signs = []
    if data.get("speed_bump"): signs.append("BUMP")
    if data.get("school_zone"): signs.append("SCHOOL")
    if data.get("speed_camera"): signs.append("CAMERA")
    if data.get("road_signs", 0): signs.append(f"SIGN {data['road_signs']}")
    if data.get("traffic_state", 0): signs.append(f"TRAFFIC {data['traffic_state']}")
    sign_icons = [name for name, flag in (("speed_bump", data.get("speed_bump")),
                  ("school_zone", data.get("school_zone")), ("speed_camera", data.get("speed_camera"))) if flag]
    for index, name in enumerate(sign_icons[:3]):
      icon(name, x0 + 24 + index * 58, 400, 44)
    text(x0 + 205, 410, "  ".join(signs) if signs else "ROAD STATUS  --", 22,
         (255, 190, 90) if signs else (120, 130, 145))

  def _project_pt(self, x, y, z):
    if x < 0.1: x = 0.1
    px_y = int((self.target_h / 2) + (self.focal_length * (self.camera_height + z) / x))
    px_x = int((self.target_w / 2) - (self.focal_length * y / x))
    return px_x, px_y

  def _draw_model_path(self, frame, path_data, hud_data):
    overlay = frame.copy()
    if not path_data['path_x']:
      return frame

    if hud_data['enabled']:
      pts_left, pts_right = [], []
      path_width = 1.8

      for i in range(len(path_data['path_x'])):
        x = path_data['path_x'][i]
        y = path_data['path_y'][i]
        if x > 50.0: break

        lx, ly = self._project_pt(x, y + path_width / 2, 0)
        rx, ry = self._project_pt(x, y - path_width / 2, 0)
        pts_left.append([lx, ly])
        pts_right.append([rx, ry])

      pts_right.reverse()
      poly_pts = np.array(pts_left + pts_right, dtype=np.int32)
      cv2.fillPoly(overlay, [poly_pts], self.config.colors["path_active"])
      cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)

    lane_color = self.config.colors["lane_line"]

    def draw_lane(xs, ys):
      if not xs: return
      pts = []
      for i in range(len(xs)):
        if xs[i] > 50.0: break
        px, py = self._project_pt(xs[i], ys[i], 0)
        pts.append([px, py])
      if len(pts) > 1:
        cv2.polylines(frame, [np.array(pts, dtype=np.int32)], False, lane_color, thickness=4)

    draw_lane(path_data['left_lane_x'], path_data['left_lane_y'])
    draw_lane(path_data['right_lane_x'], path_data['right_lane_y'])
    return frame

  def _draw_hud(self, pil_img, hud_data, has_camera):
    draw = ImageDraw.Draw(pil_img)

    border_color = self.config.colors["engaged"] if hud_data['enabled'] else self.config.colors["disengaged"]
    draw.rectangle([0, 0, self.target_w, self.target_h], outline=border_color, width=25)
    # Visual boundary between the live-camera and information panes.
    draw.line([(self.config.camera_panel_width, 0), (self.config.camera_panel_width, self.target_h)],
              fill=border_color, width=4)

    if not has_camera:
      warning_text = "WAITING FOR CAMERA SIGNAL..."
      draw.text((self.target_w // 2 - 380 + 3, self.target_h // 2 - 25 + 3), warning_text, font=self.font_warning,
                fill=(0, 0, 0))
      draw.text((self.target_w // 2 - 380, self.target_h // 2 - 25), warning_text, font=self.font_warning,
                fill=(255, 50, 50))

    blinker_color = (0, 255, 0)
    if hud_data['left_blinker']:
      draw.polygon([(40, 40), (80, 15), (80, 65)], fill=blinker_color)
    if hud_data['right_blinker']:
      draw.polygon([(self.target_w - 40, 40), (self.target_w - 80, 15), (self.target_w - 80, 65)], fill=blinker_color)

    speed = hud_data['v_ego']
    speed_val = speed * 3.6 if self.config.is_metric else speed * 2.236936
    speed_str = f"{int(speed_val)}"

    text_x, text_y = self.target_w - 260, 40
    draw.text((text_x + 3, text_y + 3), speed_str, font=self.font_speed, fill=(0, 0, 0))
    draw.text((text_x, text_y), speed_str, font=self.font_speed, fill=(255, 255, 255))
    unit_x, unit_y = text_x + 115, text_y + 70
    draw.text((unit_x + 2, unit_y + 2), self.config.speed_unit, font=self.font_unit, fill=(0, 0, 0))
    draw.text((unit_x, unit_y), self.config.speed_unit, font=self.font_unit, fill=(200, 200, 200))
