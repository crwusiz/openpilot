import os
import time
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
      self.font_small = ImageFont.truetype(self.config.font_regular, 22)
      self.font_label = ImageFont.truetype(self.config.font_bold, 19)
      self.font_warning = ImageFont.truetype(self.config.font_bold, 55)
    except Exception as e:
      cloudlog.error(f"Failed to load fonts: {e}")
      self.font_speed = ImageFont.load_default()
      self.font_unit = ImageFont.load_default()
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
      "wheel", "wheel_green", "disengage_on_accelerator", "brake_disc", "tpms",
      "gps", "direction", "wifi_strength_low", "wifi_strength_medium",
      "wifi_strength_high", "wifi_strength_full", "traffic_green", "traffic_red",
      "traffic_off", "speed_bump", "school_zone", "speed_camera",
      "dist1", "dist2", "dist3", "dist4",
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
    # The physical cluster uses one continuous onroad surface.  Keep the
    # complete ROAD frame and map it to the panel resolution; HUD elements are
    # composited on top instead of reserving a blank information pane.
    return cv2.resize(frame, (self.target_w, self.target_h), interpolation=cv2.INTER_AREA)

  def _draw_info_panel(self, pil_img, data):
    """Draw the right pane using the same compact icon-first HUD language as onroad."""
    draw = ImageDraw.Draw(pil_img)
    # Full-screen camera layout: HUD is drawn directly over the image.
    x0 = 0
    draw = ImageDraw.Draw(pil_img)

    def text(x, y, value, font=None, color=(220, 225, 232), anchor=None):
      draw.text((int(x), int(y)), str(value), font=font or self.font_small, fill=color, anchor=anchor)

    def centered(cx, y, value, font=None, color=(220, 225, 232)):
      text(cx, y, value, font, color, anchor="ma")

    def icon(name, x, y, size=52, active=True):
      source = self.icons.get(name)
      if source is None:
        return
      cache_key = (name, size, bool(active))
      image = self._icon_cache.get(cache_key)
      if image is None:
        image = source.resize((size, size), Image.Resampling.LANCZOS)
        image = image.copy()
        if not active:
          alpha = image.getchannel("A").point(lambda p: p * 70 // 255)
          image.putalpha(alpha)
        self._icon_cache[cache_key] = image
      pil_img.paste(image, (int(x), int(y)), image)

    white, muted, green, amber, red = (245, 248, 252), (105, 115, 130), (110, 235, 150), (255, 195, 80), (255, 105, 95)
    current = data.get("v_ego", 0.0) * (3.6 if self.config.is_metric else 2.236936)
    cruise = data.get("cruise_speed", 0.0)
    if cruise > 100:
      cruise /= 100.0
    cruise_s = f"{int(cruise)}" if cruise > 0 else "--"

    # Top-left: tall, centered boxes like hud_renderer's MAX/SET boxes.
    box_fill, box_outline = (16, 25, 36), (62, 78, 98)
    def value_box(x, y, label, value, color):
      draw.rounded_rectangle([x, y, x + 88, y + 88], radius=9, fill=box_fill, outline=box_outline, width=2)
      centered(x + 44, y + 9, label, self.font_label, muted)
      centered(x + 44, y + 39, value, self.font_unit, color)

    value_box(x0 + 40, 34, "CRUISE", cruise_s, white)
    limit = data.get("nav_limit_speed", 0.0)
    value_box(x0 + 40, 135, "SET", cruise_s, (170, 215, 255))
    # Speed limit uses a circular road-sign style indicator.
    limit_box = [x0 + 140, 135, x0 + 228, 223]
    draw.ellipse(limit_box, fill=(235, 238, 242), outline=(190, 35, 35), width=6)
    centered(x0 + 184, 163, f"{int(limit) if limit else '--'}", self.font_unit, (20, 25, 30))
    traffic_icon = "traffic_green" if data.get("traffic_state") == 1 else "traffic_red" if data.get("traffic_state") == 2 else "traffic_off"
    icon(traffic_icon, x0 + 305, 14, 38, bool(data.get("traffic_state")))
    if data.get("school_zone"):
      icon("school_zone", x0 + 355, 14, 38)
    elif data.get("speed_bump"):
      icon("speed_bump", x0 + 355, 14, 38)
    if data.get("speed_camera"):
      icon("speed_camera", x0 + 405, 14, 38)

    # Mici _draw_current_speed: color follows acceleration/deceleration.
    accel = float(data.get("accel", 0.0) or 0.0)
    if accel > 0:
      alpha = max(80, min(255, int(255 - 180 * min(accel / 3.0, 1.0))))
      speed_color = (alpha, 255, alpha)
    else:
      alpha = max(60, min(255, int(255 - 255 * min(abs(accel) / 4.0, 1.0))))
      speed_color = (255, alpha, alpha)

    # Vertical speed box, matching the boxed MAX/SET visual language.
    speed_cx = self.target_w // 2
    centered(speed_cx, 13, int(current), self.font_speed, speed_color)
    centered(speed_cx, 123, self.config.speed_unit, self.font_small, (225, 230, 238))

    # Top-right connectivity: icon-only, dimmed when unavailable.
    gps_ok = data.get("gps_satellites", 0) > 0
    wifi = data.get("wifi_strength", 0)
    wifi_name = "wifi_strength_full" if wifi >= 4 else "wifi_strength_high" if wifi == 3 else "wifi_strength_medium" if wifi == 2 else "wifi_strength_low"
    icon("direction", self.target_w - 270, 28, 64, gps_ok)
    icon("gps", self.target_w - 180, 28, 64, gps_ok)
    icon(wifi_name, self.target_w - 85, 28, 64, wifi > 0)

    # Bottom-left: wheel, accelerator, brake in the requested order.
    controls = (("wheel_green" if data.get("enabled") else "wheel", "STEER", data.get("enabled"), green),
                ("disengage_on_accelerator", "ACCEL", data.get("gas_pressed"), amber),
                ("brake_disc", "BRAKE", data.get("brake_pressed"), red))
    # Align the three lower controls with the upper box centers:
    # CRUISE/SET center=84, LIMIT center=184, then BRAKE at equal spacing.
    for cx, (name, label, active, color) in zip((x0 + 84, x0 + 184, x0 + 284), controls):
      icon(name, cx - 32, 344, 64, bool(active))
      centered(cx, 414, label, self.font_label, color if active else muted)

    # Bottom-right: gap indicator above a larger TPMS icon and 2x2 pressures.
    gap = int(data.get("distance_level", 0) or 0)
    gap_name = f"dist{min(max(gap, 1), 4)}"
    tpms_x = self.target_w - 130
    tpms_center = tpms_x + 56
    icon(gap_name, int(tpms_center - 39), 248, 78, True)
    icon("tpms", tpms_x, self.target_h - 122, 112, True)
    pressures = data.get("tpms", [0, 0, 0, 0])
    for i, (label, value) in enumerate(zip(("FL", "FR", "RL", "RR"), pressures)):
      # Values are deliberately overlaid on the TPMS vehicle silhouette.
      px = tpms_x + 20 + (i % 2) * 60
      py = self.target_h - 112 + (i // 2) * 58
      centered(px, py, label, self.font_label, muted)
      centered(px, py + 20, f"{value:.1f}" if value else "--", self.font_small, white)

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
    self._draw_borders(pil_img, hud_data)
    self._draw_ignore_limit_timer(pil_img, hud_data)

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

  def _draw_borders(self, pil_img, data):
    """Mici onroad-style status borders for blinkers, blind spots and state."""
    draw = ImageDraw.Draw(pil_img)
    border_size = 10
    blinking = (time.monotonic() % 0.9) < 0.45
    red, orange, green, steering = (255, 80, 70), (255, 170, 55), (90, 220, 120), (80, 180, 255)

    def side(x, blinker, blindspot):
      if data.get('brake_pressed') or blindspot:
        color, visible = red, True
      elif blinker:
        color, visible = orange, blinking
      elif data.get('enabled'):
        color, visible = green, True
      else:
        color, visible = steering, False
      if visible:
        draw.rectangle([x, 0, x + border_size - 1, self.target_h - 1], fill=color)

    side(0, data.get('left_blinker', False), data.get('left_blindspot', False))
    side(self.target_w - border_size, data.get('right_blinker', False), data.get('right_blindspot', False))

  def _draw_ignore_limit_timer(self, pil_img, data):
    """Mici orange countdown bar, shrinking as ignoreLimitTimer expires."""
    max_ticks = 3000.0
    timer = float(data.get('ignore_limit_timer', 0.0) or 0.0)
    if timer <= 0 or timer >= max_ticks:
      return
    ratio = max(0.0, min(1.0, (max_ticks - timer) / max_ticks))
    bar_width = int(self.target_w * ratio)
    if bar_width <= 0:
      return
    draw = ImageDraw.Draw(pil_img)
    # Match mici: centered, 10px orange translucent bar at the top edge.
    draw.rectangle([int((self.target_w - bar_width) / 2), 0,
                    int((self.target_w + bar_width) / 2), 9], fill=(190, 125, 40))
