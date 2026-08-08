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
    self._draw_hud(pil_img, models.get_hud_data(), has_camera)

    return np.array(pil_img)

  def _crop_and_resize(self, frame):
    h, w = frame.shape[:2]
    # Use the left half of the display for the camera and reserve the right half
    # for cluster data. This 2.08:1 viewport preserves far more vertical view
    # than filling the entire 4.16:1 display.
    view_w = min(max(1, self.config.camera_panel_width), self.target_w)
    view_ratio = view_w / self.target_h
    crop_h = min(h, max(1, round(w / view_ratio)))
    crop_y = (h - crop_h) // 2
    cropped = frame[crop_y:crop_y + crop_h, :]
    resized = cv2.resize(cropped, (view_w, self.target_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((self.target_h, self.target_w, 3), dtype=np.uint8)
    canvas[:, :view_w] = resized
    return canvas

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
