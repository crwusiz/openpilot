import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from openpilot.common.swaglog import cloudlog


class ClusterRenderer:
  def __init__(self, config):
    cloudlog.info("Initializing ClusterRenderer (Mici Style)...")
    self.config = config
    self.width = config.width
    self.height = config.height

    try:
      self.font_speed = ImageFont.truetype(self.config .font_bold, 120)
      self.font_unit = ImageFont.truetype(self.config.font_regular, 40)
    except Exception as e:
      cloudlog.error(f"Failed to load fonts: {e}")
      self.font_speed = ImageFont.load_default()
      self.font_unit = ImageFont.load_default()

    self.blank_canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)

    self.camera_height = 1.22  # m (차량 카메라 높이)
    self.focal_length = 910.0  # 초점 거리 (해상도에 따라 조절 필요)

  def render(self, camera, models):
    if camera.has_frame():
      frame = camera.get_frame()
      frame = self._crop_and_resize(frame)
    else:
      frame = self.blank_canvas.copy()

    if models.is_valid():
      frame = self._draw_model_path(frame, models.get_path_data(), models.get_hud_data())

    pil_img = Image.fromarray(frame)
    self._draw_hud(pil_img, models.get_hud_data())

    final_frame = np.array(pil_img)
    return final_frame

  def _crop_and_resize(self, frame):
    h, w = frame.shape[:2]

    target_ratio = self.width / self.height
    current_ratio = w / h

    if current_ratio < target_ratio:
      new_h = int(w / target_ratio)
      crop_y = (h - new_h) // 2
      cropped = frame[crop_y: crop_y + new_h, 0:w]
    else:
      new_w = int(h * target_ratio)
      crop_x = (w - new_w) // 2
      cropped = frame[0:h, crop_x: crop_x + new_w]

    return cv2.resize(cropped, (self.width, self.height), interpolation=cv2.INTER_LINEAR)

  def _project_pt(self, x, y, z):
    if x < 0.1:  # 0으로 나누기 방지
      x = 0.1

    px_y = int((self.height / 2) + (self.focal_length * (self.camera_height + z) / x))
    px_x = int((self.width / 2) - (self.focal_length * y / x))

    return px_x, px_y

  def _draw_model_path(self, frame, path_data, hud_data):
    overlay = frame.copy()

    if not path_data['path_x']:
      return frame

    if hud_data['enabled']:
      pts_left = []
      pts_right = []

      path_width = 1.8

      for i in range(len(path_data['path_x'])):
        x = path_data['path_x'][i]
        y = path_data['path_y'][i]

        if x > 50.0:
          break

        lx, ly = self._project_pt(x, y + path_width / 2, 0)
        rx, ry = self._project_pt(x, y - path_width / 2, 0)

        pts_left.append([lx, ly])
        pts_right.append([rx, ry])

      pts_right.reverse()
      poly_pts = np.array(pts_left + pts_right, dtype=np.int32)

      path_color = self.config.colors["path_active"]
      cv2.fillPoly(overlay, [poly_pts], path_color)

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

  def _draw_hud(self, pil_img, hud_data):
    draw = ImageDraw.Draw(pil_img)

    if hud_data['enabled']:
      border_color = self.config.colors["engaged"]  # 초록색
    else:
      border_color = self.config.colors["disengaged"]  # 어두운 파랑/회색

    border_width = 30
    draw.rectangle([0, 0, self.width, self.height], outline=border_color, width=border_width)

    blinker_color = (0, 255, 0)  # 밝은 초록
    if hud_data['left_blinker']:
      draw.polygon([(50, 50), (100, 20), (100, 80)], fill=blinker_color)
    if hud_data['right_blinker']:
      draw.polygon([(self.width - 50, 50), (self.width - 100, 20), (self.width - 100, 80)], fill=blinker_color)

    speed = hud_data['v_ego']

    if self.config.is_metric:
      speed_val = speed * 3.6
    else:
      speed_val = speed * 2.236936

    speed_str = f"{int(speed_val)}"
    unit_str = self.config.speed_unit

    margin_x = 100
    margin_y = 50

    text_x = self.width - margin_x - 180
    text_y = margin_y

    draw.text((text_x + 3, text_y + 3), speed_str, font=self.font_speed, fill=(0, 0, 0))
    draw.text((text_x, text_y), speed_str, font=self.font_speed, fill=(255, 255, 255))

    unit_x = text_x + 130
    unit_y = text_y + 90
    draw.text((unit_x + 2, unit_y + 2), unit_str, font=self.font_unit, fill=(0, 0, 0))
    draw.text((unit_x, unit_y), unit_str, font=self.font_unit, fill=(200, 200, 200))
