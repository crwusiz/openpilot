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
    """Draw the right pane using the same compact icon-first HUD language as onroad."""
    draw = ImageDraw.Draw(pil_img)
    x0 = self.config.camera_panel_width
    draw.rectangle([x0 + 5, 5, self.target_w - 5, self.target_h - 5], fill=(7, 12, 19))

    def text(x, y, value, font=None, color=(220, 225, 232), anchor=None):
      draw.text((int(x), int(y)), str(value), font=font or self.font_small, fill=color, anchor=anchor)

    def centered(cx, y, value, font=None, color=(220, 225, 232)):
      text(cx, y, value, font, color, anchor="ma")

    def icon(name, x, y, size=52, active=True):
      source = self.icons.get(name)
      if source is None:
        return
      image = source.resize((size, size), Image.Resampling.LANCZOS)
      if not active:
        alpha = image.getchannel("A").point(lambda p: p * 70 // 255)
        image.putalpha(alpha)
      pil_img.paste(image, (int(x), int(y)), image)

    white, muted, green, amber, red = (245, 248, 252), (105, 115, 130), (110, 235, 150), (255, 195, 80), (255, 105, 95)
    current = data.get("v_ego", 0.0) * (3.6 if self.config.is_metric else 2.236936)
    cruise = data.get("cruise_speed", 0.0)
    if cruise > 100:
      cruise /= 100.0
    cruise_s = f"{int(cruise)}" if cruise > 0 else "--"

    # Top-left: cruise/set/limit and road status (two rows).
    text(x0 + 24, 17, f"CRUISE  {cruise_s}", self.font_small, white)
    text(x0 + 24, 53, f"SET  {cruise_s}", self.font_small, (170, 215, 255))
    limit = data.get("nav_limit_speed", 0.0)
    text(x0 + 170, 53, f"LIMIT  {int(limit) if limit else '--'}", self.font_small, muted)
    traffic_icon = "traffic_green" if data.get("traffic_state") == 1 else "traffic_red" if data.get("traffic_state") == 2 else "traffic_off"
    icon(traffic_icon, x0 + 305, 14, 38, bool(data.get("traffic_state")))
    if data.get("school_zone"):
      icon("school_zone", x0 + 355, 14, 38)
    elif data.get("speed_bump"):
      icon("speed_bump", x0 + 355, 14, 38)
    if data.get("speed_camera"):
      icon("speed_camera", x0 + 405, 14, 38)

    # Current speed is centered in the top middle of the information pane.
    speed_cx = x0 + 480
    centered(speed_cx, 3, int(current), self.font_speed, white)
    centered(speed_cx, 69, self.config.speed_unit, self.font_small, muted)

    # Top-right connectivity: icon-only, dimmed when unavailable.
    gps_ok = data.get("gps_satellites", 0) > 0
    wifi = data.get("wifi_strength", 0)
    wifi_name = "wifi_strength_full" if wifi >= 4 else "wifi_strength_high" if wifi == 3 else "wifi_strength_medium" if wifi == 2 else "wifi_strength_low"
    icon("direction", x0 + 700, 17, 48, gps_ok)
    icon("gps", x0 + 765, 17, 48, gps_ok)
    icon(wifi_name, x0 + 830, 17, 48, wifi > 0)

    # Bottom-left: wheel, accelerator, brake in the requested order.
    controls = (("wheel_green" if data.get("enabled") else "wheel", "STEER", data.get("enabled"), green),
                ("disengage_on_accelerator", "ACCEL", data.get("gas_pressed"), amber),
                ("brake_disc", "BRAKE", data.get("brake_pressed"), red))
    for i, (name, label, active, color) in enumerate(controls):
      cx = x0 + 62 + i * 145
      icon(name, cx - 32, 344, 64, bool(active))
      centered(cx, 414, label, self.font_label, color if active else muted)

    # Bottom-right: gap indicator above a larger TPMS icon and 2x2 pressures.
    gap = int(data.get("distance_level", 0) or 0)
    gap_name = f"dist{min(max(gap, 1), 4)}"
    icon(gap_name, x0 + 665, 165, 78, True)
    centered(x0 + 704, 244, "GAP", self.font_label, muted)
    icon("tpms", x0 + 660, 284, 112, True)
    pressures = data.get("tpms", [0, 0, 0, 0])
    for i, (label, value) in enumerate(zip(("FL", "FR", "RL", "RR"), pressures)):
      px = x0 + 800 + (i % 2) * 58
      py = 302 + (i // 2) * 62
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
