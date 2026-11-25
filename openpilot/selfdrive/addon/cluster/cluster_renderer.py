import math
import os
import time
from collections import OrderedDict
from functools import lru_cache
from time import thread_time
import cv2
import numpy as np

from PIL import Image, ImageDraw, ImageFont

try:
  from openpilot.selfdrive import hud_render_support, model_render_support
  from openpilot.selfdrive.color_support import STOCK_PATH_COLORS, hsla_to_rgba
except ImportError:
  import hud_render_support
  import model_render_support
  from color_support import STOCK_PATH_COLORS, hsla_to_rgba

try:
  from openpilot.common.filter_simple import FirstOrderFilter
  from openpilot.common.swaglog import cloudlog
  from openpilot.selfdrive.addon.cluster.cluster_config import Colors, colors_alpha
  from openpilot.selfdrive.addon.cluster.cluster_render_support import park_layout, park_upload_button_bounds
except ImportError:
  # deploy.sh copies this exact renderer alongside the portable support file.
  # The Pi does not need Params, cereal or an openpilot installation.
  import logging
  try:
    from .cluster_render_support import Colors, FirstOrderFilter, colors_alpha, park_layout, park_upload_button_bounds
  except ImportError:
    from cluster_render_support import Colors, FirstOrderFilter, colors_alpha, park_layout, park_upload_button_bounds
  cloudlog = logging.getLogger("cluster_renderer")


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

PANEL_ICON_NAMES = (
  *WHEEL_ICONS.values(), *WIFI_ICONS.values(),
  *TRAFFIC_ICONS.values(),
  "accel_pressed", "brake_pressed", "autohold_active", "autohold_warning", "tpms", "gps", "compass",
  "speed_bump", "school_zone", "speed_camera",
  "turnsignal_l", "turnsignal_r", "blind_spot_left", "blind_spot_right",
)
REQUIRED_ICON_PATHS = {
  **{name: f"icons/{name}.png" for name in PANEL_ICON_NAMES},
  **{name: f"{hud_render_support.LONG_INDICATOR_ASSET_DIR}/{name}.png" for name, _, _ in hud_render_support.LONG_INDICATOR_ICONS},
}

BLINKER_DRAW_COUNT = 10
BLINKER_SEQUENCE_MS = 400.0
BLINKER_PAUSE_MS = 250.0
CAMERA_OVERLAY_ICON_HEIGHT = 94
GRADIENT_BANDS = 8
ALPHA_BLEND_STRIP_HEIGHT = 64
RADAR_TO_CAMERA = 1.52  # m, matches radard for the standalone renderer
TEXT_CACHE_MAX_ENTRIES = 256
TEXT_CACHE_MAX_BYTES = 8 * 1024 * 1024
ICON_CACHE_MAX_ENTRIES = 256
PARK_CAR_IMAGE = "ioniq5_park.png"

STOCK_PATH_GRADIENT = STOCK_PATH_COLORS


def _acceleration_path_gradient(track_y, sample_indices, acceleration_x, height):
  colors, stops = [], []
  denominator = max(height - 1, 1)
  for y, index in zip(track_y, sample_indices, strict=True):
    if not 0 <= y < height or index >= len(acceleration_x):
      continue
    acceleration = acceleration_x[index]
    if not math.isfinite(acceleration):
      continue
    stop = 1.0 - y / denominator
    if stops and stop <= stops[-1]:
      continue

    # Use the main UI's HSL colors and distance fade for the projected path.
    hue = min(max(60.0 + acceleration * 35.0, 0.0), 120.0) / 360.0
    saturation = min(abs(acceleration * 1.5), 1.0)
    lightness = 0.95 - 0.33 * saturation
    alpha = 0.4 * min(max((0.75 - stop) / 0.375, 0.0), 1.0)
    colors.append(hsla_to_rgba(hue, saturation, lightness, alpha))
    stops.append(stop)

  return colors, stops


@lru_cache(maxsize=128)
def _alpha_blend_lut(color, alpha):
  """Precompute the same uint8 blend without allocating a solid-color image."""
  values = np.arange(256, dtype=np.uint8).reshape(256, 1)
  if color[0] == color[1] == color[2]:
    # Lane lines and a disengaged path use one lookup for all RGB channels.
    # OpenCV has a faster single-channel lookup for these common colors.
    solid_color = np.full_like(values, color[0])
  else:
    values = values[:, :, None].repeat(3, axis=2)
    solid_color = np.full_like(values, color)
  # Let OpenCV round the lookup exactly as it rounds addWeighted on camera
  # pixels. Colored tables keep the RGB channels independent.
  table = cv2.addWeighted(solid_color, alpha, values, 1.0 - alpha, 0)
  table.setflags(write=False)
  return table


class ColorBlendWorkspace:
  """Bounded constant-color strips and one output buffer, owned by a renderer."""

  def __init__(self, width, channels=3):
    self._shape = (ALPHA_BLEND_STRIP_HEIGHT, width, channels)
    self._output = np.empty(self._shape, dtype=np.uint8)
    self._colors = OrderedDict()

  def blend(self, image, color, alpha):
    if self._shape[2] == 4:
      # Premultiplied path layer: blend RGB and coverage together against the
      # previous layer, using opaque source colors. USB retains RGB defaults.
      color = (*color[:3], 255)
    height, width = image.shape[:2]
    if height > self._shape[0] or width > self._shape[1]:
      if self._shape[2] == 4:
        solid = np.empty_like(image)
        solid[:] = color
        return cv2.addWeighted(solid, alpha, image, 1.0 - alpha, 0)
      return cv2.LUT(image, _alpha_blend_lut(tuple(color), alpha))
    key = tuple(color)
    cached = self._colors.get(key)
    if cached is None:
      if len(self._colors) >= GRADIENT_BANDS + 4:
        _, evicted = self._colors.popitem(last=False)
        solid = evicted[0]
      else:
        solid = np.empty(self._shape, dtype=np.uint8)
      filled_height = filled_width = 0
    else:
      solid, filled_height, filled_width = cached
      self._colors.move_to_end(key)
    if height > filled_height or width > filled_width:
      # Dynamic gradients change colors often. Fill only the strip area used
      # so far for this color and reuse the allocation when it is evicted.
      filled_height, filled_width = max(height, filled_height), max(width, filled_width)
      cv2.rectangle(solid, (0, 0), (filled_width - 1, filled_height - 1), key, -1)
      self._colors[key] = solid, filled_height, filled_width
    # addWeighted processes RGB channels together; the colored lookup path
    # performs independent byte lookups. Reuse strips without per-band fills.
    output = self._output[:height, :width]
    cv2.addWeighted(solid[:height, :width], alpha, image, 1.0 - alpha, 0, dst=output)
    return output


class TextGlyph:
  """Rasterize a glyph once, then recolor its reusable sprite with PIL masks."""

  def __init__(self, text, font, stroke_width, anchor="mm"):
    # Retain the font while its identity is used by the renderer cache.
    self.font = font
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = probe.textbbox((0, 0), text, font=font, anchor=anchor, stroke_width=stroke_width)
    self.bbox = bbox
    padding = max(2, int(stroke_width) + 1)
    width = max(1, bbox[2] - bbox[0] + padding * 2)
    height = max(1, bbox[3] - bbox[1] + padding * 2)
    self.anchor_x, self.anchor_y = padding - bbox[0], padding - bbox[1]
    position = (self.anchor_x, self.anchor_y)
    self.fill_mask = Image.new("L", (width, height))
    ImageDraw.Draw(self.fill_mask).text(position, text, font=font, fill=255, anchor=anchor)
    self.stroke_mask = None
    if stroke_width:
      self.stroke_mask = Image.new("L", (width, height))
      # Equal stroke/fill inks make PIL draw only the full stroked mask.
      ImageDraw.Draw(self.stroke_mask).text(position, text, font=font, fill=255, anchor=anchor,
                                            stroke_width=stroke_width, stroke_fill=255)
    self.sprite = Image.new("RGBA", (width, height))
    self.draw = ImageDraw.Draw(self.sprite)
    self.bounds = (0, 0, width, height)
    self.byte_size = width * height * (5 + int(self.stroke_mask is not None))
    self._inks = None

  def colored(self, fill, stroke):
    ink = self.draw._getink(fill)[0]
    stroke_ink = self.draw._getink(stroke)[0] if stroke is not None else ink
    inks = (ink, stroke_ink)
    if self._inks != inks:
      self.sprite.paste((0, 0, 0, 0), self.bounds)
      if self.stroke_mask is not None:
        self.sprite.paste(stroke_ink, self.bounds, self.stroke_mask)
      # Mirror ImageDraw.text's rounding, alpha and equal-ink behavior.
      if self.stroke_mask is None or ink != stroke_ink:
        self.sprite.paste(ink, self.bounds, self.fill_mask)
      self._inks = inks
    return self.sprite


class LeadVehicle:
  def __init__(self, fps):
    self.bar = np.empty((0, 2), dtype=np.float32)
    self.d_filter = FirstOrderFilter(0.0, 0.2, 1 / fps, initialized=False)
    self.y_filter = FirstOrderFilter(0.0, 0.2, 1 / fps, initialized=False)
    self.fade_filter = FirstOrderFilter(0.0, 0.1, 1 / fps)


class StopPoint(model_render_support.PlannedStopTracker):
  def __init__(self, fps):
    super().__init__(1 / fps, RADAR_TO_CAMERA, FirstOrderFilter)
    self.bar = np.empty((0, 2), dtype=np.float32)
    self.model_frame_id = None


def _park_weather_kind(code):
  if code is None:
    return None, "날씨 정보 없음"
  if code == 0:
    return "clear", "맑음"
  if code in (1, 2):
    return "partly", "구름 조금"
  if code in (45, 48):
    return "cloudy", "안개"
  if code in (71, 73, 75, 77, 85, 86):
    return "snow", "눈"
  if code >= 95:
    return "rain", "뇌우"
  if 51 <= code <= 67 or 80 <= code <= 82:
    return "rain", "비"
  return "cloudy", "흐림"


class ParkScreen:
  """Compose the shared USB and Pi parking view at the display's logical size."""

  TEXT = (241, 246, 255)
  MUTED = (172, 191, 218)
  ACCENT = (98, 187, 255)
  TRIP_ICON_X = 1468
  TRIP_LABEL_X = 1504
  TRIP_TITLE_Y = 92
  TRIP_DISTANCE_Y = 181
  TRIP_DURATION_Y = 268
  TRIP_LABEL_SIZE = 28
  DUST_GRADES = (("좋음", (117, 211, 158)), ("보통", (255, 197, 101)),
                 ("나쁨", (255, 145, 133)), ("매우 나쁨", (210, 157, 255)))
  UPLOAD_LABELS = {
    "checking": "전송 준비 중…",
    "running": "로그 전송 중…",
    "succeeded": "로그 전송 완료",
    "warning": "일부 전송 완료 · 재시도",
    "failed": "전송 실패 · 다시 시도",
  }

  def __init__(self, size, font_path, asset_path):
    width, height = size
    self.scale, self.left, self.top = park_layout(size)
    self.upload_bounds = park_upload_button_bounds(size)
    self.fonts = {size: self._font(font_path, size) for size in (24, 26, self.TRIP_LABEL_SIZE, 30, 54, 124)}
    x = np.linspace(-1, 1, width, dtype=np.float32)[None, :]
    y = np.linspace(-1, 1, height, dtype=np.float32)[:, None]
    glow = np.exp(-2.8 * x**2 - 1.15 * (y + 0.35)**2)
    background = np.array((10, 17, 33), dtype=np.float32) + glow[:, :, None] * np.array((21, 35, 64), dtype=np.float32)
    self.base = Image.fromarray(background.astype(np.uint8))
    self._draw_fixed_labels(ImageDraw.Draw(self.base))
    try:
      with Image.open(asset_path) as image:
        car = image.convert("RGBA")
      car.thumbnail((round(850 * self.scale), round(382 * self.scale)), Image.Resampling.LANCZOS)
      cx, cy = self._point(960, 240)
      self.base.paste(car, (cx - car.width // 2, cy - car.height // 2), car)
    except (OSError, ValueError) as exc:
      cloudlog.error(f"Failed to load parked car image from {asset_path}: {exc}")
    self._last_key = None
    self._last_image = None

  def _font(self, path, size):
    try:
      return ImageFont.truetype(path, max(1, round(size * self.scale)))
    except OSError:
      return ImageFont.load_default()

  def _point(self, x, y):
    return round(self.left + x * self.scale), round(self.top + y * self.scale)

  def _text(self, draw, x, y, value, size, color=TEXT, anchor="lt"):
    draw.text(self._point(x, y), value, font=self.fonts[size], fill=color, anchor=anchor)

  def _text_width(self, draw, value, size):
    return draw.textlength(value, font=self.fonts[size]) / self.scale

  def _draw_fixed_labels(self, draw):
    border = (43, 63, 94)
    draw.rounded_rectangle((self._point(1400, 48), self._point(1842, 416)), radius=max(1, round(22 * self.scale)),
                           fill=(15, 26, 47), outline=border, width=max(1, round(2 * self.scale)))
    for y in (136, 225, 310):
      draw.line((self._point(1434, y), self._point(1808, y)), fill=border, width=max(1, round(self.scale)))
    for name, label, y, color in (
      ("trip", "주행 정보", self.TRIP_TITLE_Y, self.TEXT),
      ("distance", "거리", self.TRIP_DISTANCE_Y, self.MUTED),
      ("time", "시간", self.TRIP_DURATION_Y, self.MUTED),
    ):
      self._draw_trip_icon(draw, name, self._point(self.TRIP_ICON_X, y), self.ACCENT, border)
      self._text(draw, self.TRIP_LABEL_X, y, label, self.TRIP_LABEL_SIZE, color, "lm")

  def _draw_trip_icon(self, draw, name, center, color, border):
    x, y = center
    icon_scale = 1.5 * self.scale

    def point(dx, dy):
      return x + round(dx * icon_scale), y + round(dy * icon_scale)

    stroke = math.ceil(2 * icon_scale)

    def line(*points):
      draw.line([point(dx, dy) for dx, dy in points], fill=color, width=stroke, joint="curve")

    draw.rounded_rectangle((point(-16, -16), point(16, 16)), radius=max(1, round(8 * icon_scale)),
                           fill=(15, 26, 47), outline=border, width=max(1, round(self.scale)))
    if name == "trip":
      line((-9, -1), (-6, -7), (6, -7), (9, -1))
      draw.rounded_rectangle((point(-10, -1), point(10, 7)), radius=max(1, round(3 * icon_scale)),
                             outline=color, width=stroke)
      line((-8, 7), (-8, 10))
      line((8, 7), (8, 10))
      line((-6, 3), (-4, 3))
      line((4, 3), (6, 3))
    elif name == "distance":
      line((-9, 9), (-5, -9))
      line((9, 9), (5, -9))
      line((0, -8), (0, -5))
      line((0, -2), (0, 1))
      line((0, 5), (0, 9))
    elif name == "time":
      draw.ellipse((point(-9, -9), point(9, 9)), outline=color, width=stroke)
      line((0, -5), (0, 0), (4, 3))
    elif name == "upload":
      line((0, 4), (0, -8))
      line((-5, -3), (0, -8), (5, -3))
      line((-9, 3), (-9, 8), (9, 8), (9, 3))

  def _draw_upload_button(self, draw, enabled, state):
    fill, border, text = ((24, 56, 94), (76, 145, 210), self.TEXT) if enabled else ((30, 44, 64), (53, 72, 99), self.MUTED)
    draw.rounded_rectangle(self.upload_bounds, radius=max(1, round(11 * self.scale)),
                           fill=fill, outline=border, width=max(1, round(2 * self.scale)))
    _, top, _, bottom = self.upload_bounds
    center_y = (top + bottom) // 2
    icon_x = self._point(self.TRIP_ICON_X, 0)[0]
    label_x = self._point(self.TRIP_LABEL_X, 0)[0]
    self._draw_trip_icon(draw, "upload", (icon_x, center_y), text, border)
    label = self.UPLOAD_LABELS.get(state, "로그 전송")
    draw.text((label_x, center_y), label, font=self.fonts[self.TRIP_LABEL_SIZE], fill=text, anchor="lm")

  def _draw_weather_icon(self, draw, kind, left, top):
    def point(x, y):
      return self._point(left + x, top + y)

    def circle(x, y, r, fill):
      draw.ellipse((point(x - r, y - r), point(x + r, y + r)), fill=fill)

    if kind in ("clear", "partly"):
      x, y = (29, 39) if kind == "clear" else (24, 27)
      gold = (255, 208, 95)
      circle(x, y, 14, gold)
      for index in range(8):
        angle = index * math.pi / 4
        dx, dy = math.cos(angle), math.sin(angle)
        draw.line((point(x + dx * 19, y + dy * 19), point(x + dx * 24, y + dy * 24)),
                  fill=gold, width=max(1, round(3 * self.scale)))
    if kind != "clear":
      cloud = (210, 226, 246)
      circle(16, 48, 13, cloud)
      circle(33, 41, 17, cloud)
      circle(47, 50, 11, cloud)
      draw.rectangle((point(15, 46), point(47, 61)), fill=cloud)
    if kind in ("rain", "snow"):
      for x in (18, 32, 46):
        if kind == "snow":
          circle(x, 69, 2.5, self.TEXT)
        else:
          draw.line((point(x + 2, 65), point(x - 2, 72)), fill=self.ACCENT, width=max(1, round(3 * self.scale)))

  def _draw_weather(self, draw, center, kind, current, description):
    if kind is None and current == "--":
      self._text(draw, center, 299, description, 30, self.MUTED, "mt")
      return

    icon_space = 58 + 16 if kind is not None else 0
    temperature_space = self._text_width(draw, current, 54) + 24
    width = icon_space + temperature_space + self._text_width(draw, description, 30)
    left = center - width / 2
    if kind is not None:
      self._draw_weather_icon(draw, kind, left, 264)
    self._text(draw, left + icon_space, 283, current, 54)
    self._text(draw, left + icon_space + temperature_space, 299, description, 30)

  @staticmethod
  def _temperature(value):
    return "--" if value is None else f"{value:.0f}°"

  @staticmethod
  def _duration(seconds):
    if seconds is None:
      return "--"
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours}시간 {minutes:02d}분" if hours else f"{minutes}분"

  def render(self, data, *, metric):
    offset = data.get("park_utc_offset_s")
    now = time.localtime() if offset is None else time.gmtime(time.time() + offset)  # noqa: TID251
    clock = time.strftime("%H:%M", now)
    weekday = "월화수목금토일"[now.tm_wday]
    date = f"{now.tm_mon}월 {now.tm_mday}일 {weekday}요일"
    weather = data.get("park_weather") or {}
    kind, description = _park_weather_kind(weather.get("code"))
    current = self._temperature(weather.get("temperature_c"))
    high = self._temperature(weather.get("max_c"))
    low = self._temperature(weather.get("min_c"))
    dust = weather.get("dust_grade")
    km = data.get("trip_distance_km")
    distance = "--"
    if km is not None:
      distance = f"{km:.1f} km" if metric else f"{km * hud_render_support.KM_TO_MILE:.1f} mi"
    duration = self._duration(data.get("trip_duration_s"))
    upload_enabled = data.get("park_log_upload_enabled") is True
    upload_state = data.get("park_log_upload_state")
    key = (clock, date, kind, description, current, high, low, dust, distance, duration, upload_enabled, upload_state)
    if key == self._last_key:
      return self._last_image

    image = self.base.copy()
    draw = ImageDraw.Draw(image)
    self._draw_upload_button(draw, upload_enabled, upload_state)
    temperatures = f"최고 {high}  최저 {low}"
    grade, grade_color = self.DUST_GRADES[dust] if type(dust) is int and 0 <= dust < len(self.DUST_GRADES) else ("--", self.MUTED)
    dust_label = f"미세먼지 {grade}"
    dot_offset = self._text_width(draw, temperatures, 26) + 32
    details_width = dot_offset + 21 + self._text_width(draw, dust_label, 24)
    details_left = min(86, 510 - details_width)
    center = details_left + details_width / 2
    self._text(draw, center, 92, date, 30, self.MUTED, "mt")
    self._text(draw, center, 141, clock, 124, anchor="mt")
    self._draw_weather(draw, center, kind, current, description)
    self._text(draw, details_left, 363, temperatures, 26, self.MUTED)
    dot_left = details_left + dot_offset
    draw.ellipse((self._point(dot_left, 369), self._point(dot_left + 11, 380)), fill=grade_color)
    self._text(draw, dot_left + 21, 363, dust_label, 24, self.MUTED)
    self._text(draw, 1790, self.TRIP_DISTANCE_Y, distance, self.TRIP_LABEL_SIZE, anchor="rm")
    self._text(draw, 1790, self.TRIP_DURATION_Y, duration, self.TRIP_LABEL_SIZE, anchor="rm")
    self._last_key, self._last_image = key, image
    return image


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
    self._long_indicator = hud_render_support.LongIndicatorState(1 / config.fps, FirstOrderFilter, settle_first=True)
    self._long_indicator_alphas = ()
    indicator_icons = hud_render_support.LONG_INDICATOR_ICONS
    top = min(offset - size[1] / 2 for _, size, offset in indicator_icons)
    bottom = max(offset + size[1] / 2 for _, size, offset in indicator_icons)
    max_width = max(size[0] for _, size, _ in indicator_icons)
    scale = min(1.0, self.row_h / (bottom - top), self.panel_w / max_width)
    center = (top + bottom) / 2
    self._long_indicator_layout = tuple((name, tuple(max(1, round(value * scale)) for value in size), (offset - center) * scale)
                                        for name, size, offset in indicator_icons)
    self._source_to_panel = np.eye(3, dtype=np.float32)
    self._reset_leads()
    self._last_render_at = None

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
      message = f"Failed to load cluster fonts: bold={self.config.font_bold}, regular={self.config.font_regular}: {e}"
      if getattr(config, "require_assets", False):
        raise RuntimeError(message + "; reinstall the receiver assets and check service-account directory permissions") from e
      cloudlog.error(message)
      self.font_speed = ImageFont.load_default()
      self.font_current_speed = ImageFont.load_default()
      self.font_value = ImageFont.load_default()
      self.font_unit = ImageFont.load_default()
      self.font_current_unit = ImageFont.load_default()
      self.font_small = ImageFont.load_default()
      self.font_label = ImageFont.load_default()
      self.font_warning = ImageFont.load_default()

    self.base_canvas = self._create_base_canvas()
    # Only the camera region changes before PIL takes its own RGB snapshot.
    # Keep the NumPy background instead of allocating/copying a full frame at 20 Hz.
    self._frame = self.base_canvas.copy()
    self._color_blender = ColorBlendWorkspace(self.camera_w)
    self.camera_height = 1.22
    self.focal_length = 910.0
    self.icons = {}
    self._icon_cache = OrderedDict()
    self._rotated_icon_cache = OrderedDict()
    self._text_cache = OrderedDict()
    self._text_cache_bytes = 0
    self.last_frame_timings = {}
    self._blink_started_at = None
    asset_dir = getattr(self.config, "asset_dir", os.path.join(str(self.config.BASEDIR), "selfdrive", "assets"))
    icon_dir = os.path.join(str(asset_dir), "icons")
    self._park_image_path = os.path.join(icon_dir, PARK_CAR_IMAGE)
    self._park_screen = None
    self._park_active = False
    icon_errors = []
    for name, relative_path in REQUIRED_ICON_PATHS.items():
      try:
        with Image.open(os.path.join(asset_dir, relative_path)) as icon:
          self.icons[name] = icon.convert("RGBA")
      except Exception as e:
        icon_errors.append(f"{relative_path}: {e}")
    if icon_errors:
      message = f"Failed to load cluster icons from {asset_dir}: {'; '.join(icon_errors)}"
      if getattr(config, "require_assets", False):
        raise RuntimeError(message + "; reinstall the receiver assets and check service-account directory permissions")
      cloudlog.error(message)

  def _create_base_canvas(self):
    """Build the invariant panel background once instead of every frame."""
    image = Image.new("RGB", (self.target_w, self.target_h), Colors.BLACK)
    draw = ImageDraw.Draw(image)
    content_right = self.content_x + self.content_w - 1
    content_bottom = self.content_y + self.content_h - 1

    draw.rectangle([self.content_x, self.content_y, self.camera_x - 1, content_bottom], fill=Colors.PANEL)
    draw.rectangle([self.right_aux_x, self.content_y, content_right, content_bottom], fill=Colors.PANEL)
    for x in (self.left_aux_x, self.camera_x, self.right_aux_x, self.right_panel_x):
      draw.line((x, self.content_y, x, content_bottom), fill=Colors.DIVIDER, width=2)
    for row in (1, 2):
      y = int(round(self.content_y + row * self.row_h))
      draw.line((self.content_x, y, self.camera_x - 1, y), fill=Colors.DIVIDER, width=1)
      draw.line((self.right_aux_x, y, content_right, y), fill=Colors.DIVIDER, width=1)

    return np.asarray(image, dtype=np.uint8).copy()

  def render(self, camera, models):
    started = time.monotonic()
    self._update_filter_intervals(started)
    model_valid, hud_data, path_data = models.get_render_data()
    snapshot_at = time.monotonic()
    if hud_data.get("parked") is True:
      image = self.render_park(hud_data)
      self.last_frame_timings = {"snapshot": snapshot_at - started, "hud": time.monotonic() - snapshot_at}
      return image
    self._park_active = False
    self._update_long_indicator(hud_data, started)

    # Fetch once so a stale-stream reset cannot clear the frame between a
    # separate has_frame() check and get_frame() call.
    camera_frame = camera.get_frame()
    has_camera = camera_frame is not None
    frame = self._frame
    camera_region = frame[self.camera_y:self.camera_y + self.camera_h,
                          self.camera_x:self.camera_x + self.camera_w]
    if has_camera:
      if camera_frame.shape[:2] == (self.camera_h, self.camera_w) and \
          hasattr(camera, "get_source_to_panel_transform"):
        camera_region[:] = camera_frame
        self._source_to_panel = camera.get_source_to_panel_transform(self.camera_x, self.camera_y)
      else:
        self._crop_and_resize(camera_frame, canvas=frame)
    else:
      camera_region[:] = self.base_canvas[self.camera_y:self.camera_y + self.camera_h,
                                         self.camera_x:self.camera_x + self.camera_w]

    camera_copied_at = time.monotonic()
    path_cpu_started = thread_time()
    if has_camera and model_valid:
      frame = self._draw_model_path(frame, path_data, hud_data)
    else:
      self._reset_leads()
    path_cpu_elapsed = thread_time() - path_cpu_started

    path_drawn_at = time.monotonic()
    # RGB fromarray owns its pixels, so the encoder can retain this image while
    # the next render reuses the NumPy buffer. HUD drawing stays on that snapshot.
    pil_img = Image.fromarray(frame)
    self._draw_hud(pil_img, hud_data, has_camera)
    self.last_frame_timings = {
      "camera_copy": camera_copied_at - snapshot_at,
      "snapshot": snapshot_at - started,
      "path": path_drawn_at - camera_copied_at,
      "path_cpu": path_cpu_elapsed,
      "hud": time.monotonic() - path_drawn_at,
    }
    # Keep the composed frame as PIL through the USB worker. Converting the
    # complete 1920x462 image back to NumPy here only for it to be converted to
    # NV12 in the encoder thread was a full-frame copy on every update.
    return pil_img

  def render_park(self, hud_data):
    if not self._park_active:
      self._reset_leads()
      self._long_indicator.reset()
      self._long_indicator_alphas = ()
      self._blink_started_at = None
      self._park_active = True
    if self._park_screen is None:
      self._park_screen = ParkScreen((self.target_w, self.target_h), self.config.font_bold, self._park_image_path)
    return self._park_screen.render(hud_data, metric=self.config.speed_unit == "km/h")

  def _update_long_indicator(self, hud_data, now):
    if hud_data.get("_state_stale"):
      self._long_indicator.reset()
      self._long_indicator_alphas = ()
      return

    distance_level = min(max(int(hud_data.get("distance_level", 1) or 1), 1), 4)
    alphas = self._long_indicator.update(
      now, distance_level=distance_level, has_lead=bool(hud_data.get("lead_present")), e2e_lead=bool(hud_data.get("lead_e2e")),
      disengaged=float(self._get_border_color(hud_data) == Colors.Border.DISENGAGED),
    )
    self._long_indicator_alphas = tuple(round(min(max(alpha, 0.0), 1.0) * 255) for alpha in alphas)

  def _update_filter_intervals(self, now):
    # Transport backpressure can reduce actual rendering far below the target.
    # Keep fades and lead smoothing in real time rather than slowing them down
    # by using the configured frame interval for every delayed frame.
    previous = self._last_render_at
    self._last_render_at = now
    dt = 1.0 / self.config.fps if previous is None else max(now - previous, 0.001)
    for lead in self._lead_vehicles:
      for filter_, rc in ((lead.d_filter, 0.2), (lead.y_filter, 0.2), (lead.fade_filter, 0.1)):
        filter_.dt = dt
        filter_.update_alpha(rc)
    self._stop_point.update_dt(dt)

  def _crop_and_resize(self, frame, canvas=None):
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
    if canvas is None:
      canvas = self.base_canvas.copy()
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

  def _map_line_to_polygon(self, xs, ys, zs, width, z_offset, calib_transform,
                           max_distance=model_render_support.MAX_DRAW_DISTANCE, allow_invert=True, *, return_indices=False):
    count = min(len(xs), len(ys), len(zs))
    if count < 2:
      return None
    raw = np.column_stack((xs[:count], ys[:count], zs[:count])).astype(np.float32)
    in_range = (raw[:, 0] >= 0.0) & (raw[:, 0] <= max_distance)
    raw = raw[in_range]
    if len(raw) < 2:
      return None
    left = raw + np.array([0.0, -width, z_offset], dtype=np.float32)
    right = raw + np.array([0.0, width, z_offset], dtype=np.float32)
    left_px, left_valid = self._project_points(left, calib_transform, model_render_support.CLIP_MARGIN)
    right_px, right_valid = self._project_points(right, calib_transform, model_render_support.CLIP_MARGIN)
    valid = left_valid & right_valid
    if np.count_nonzero(valid) < 2:
      return None

    left_px = left_px[valid]
    right_px = right_px[valid]

    # Prevent the path polygon from folding back over itself on hills.
    keep = slice(None)
    if not allow_invert and len(left_px) > 1:
      keep = left_px[:, 1] == np.minimum.accumulate(left_px[:, 1])
      left_px = left_px[keep]
      right_px = right_px[keep]
      if len(left_px) < 2:
        return None

    polygon = np.vstack((left_px, right_px[::-1])).astype(np.int32)
    if return_indices:
      return polygon, np.flatnonzero(in_range)[valid][keep]
    return polygon

  @staticmethod
  def _fill_polygon_alpha(image, polygon, color, alpha, blender=None):
    alpha = float(np.clip(alpha, 0.0, 1.0))
    if len(color) == 4:
      alpha *= color[3] / 255.0
      color = color[:3]
    if alpha <= 0.0:
      return

    # Rasterize once in the original clipped ROI, then crop each strip to its
    # occupied pixels. A thin diagonal lane can have a nearly full-panel bbox;
    # looking up that entire rectangle wastes most of the memory traffic.
    x, y, width, height = cv2.boundingRect(polygon)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + width, image.shape[1]), min(y + height, image.shape[0])
    if x0 >= x1 or y0 >= y1:
      return

    roi = image[y0:y1, x0:x1]
    local_polygon = polygon - np.array([x0, y0], dtype=np.int32)
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [local_polygon], 255)
    table = _alpha_blend_lut(tuple(color), alpha) if blender is None else None
    for strip_y in range(0, roi.shape[0], ALPHA_BLEND_STRIP_HEIGHT):
      strip_mask = mask[strip_y:strip_y + ALPHA_BLEND_STRIP_HEIGHT]
      strip_x, mask_y, strip_w, strip_h = cv2.boundingRect(strip_mask)
      if strip_w == 0 or strip_h == 0:
        continue
      strip = roi[strip_y + mask_y:strip_y + mask_y + strip_h, strip_x:strip_x + strip_w]
      strip_mask = strip_mask[mask_y:mask_y + strip_h, strip_x:strip_x + strip_w]
      blended = blender.blend(strip, color, alpha) if blender is not None else cv2.LUT(strip, table)
      cv2.copyTo(blended, strip_mask, strip)

  @staticmethod
  def _fill_polygon_gradient(image, polygon, colors, stops, blender=None):
    x, y, width, height = cv2.boundingRect(polygon)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + width, image.shape[1]), min(y + height, image.shape[0])
    if x0 >= x1 or y0 >= y1:
      return

    roi = image[y0:y1, x0:x1]
    local_polygon = polygon - np.array([x0, y0], dtype=np.int32)
    denominator = max(image.shape[0] - 1, 1)
    color_array = np.asarray(colors, dtype=np.float32)
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [local_polygon], 255)
    # The former full-resolution float alpha buffer allocated several large
    # arrays for every frame and dominated render time on-device. Eight bands
    # retain the low-alpha visual gradient while keeping blending in OpenCV and
    # bounding temporary memory to one small band. Build the polygon mask once;
    # recreating and rasterizing it for every band dominated the engaged render
    # path on-device.
    band_height = max(1, (roi.shape[0] + GRADIENT_BANDS - 1) // GRADIENT_BANDS)
    for band_y0 in range(0, roi.shape[0], band_height):
      band_y1 = min(band_y0 + band_height, roi.shape[0])
      band_mask = mask[band_y0:band_y1]
      band_x, mask_y, band_w, band_h = cv2.boundingRect(band_mask)
      if band_w == 0 or band_h == 0:
        continue

      # Keep the original band midpoint even when its occupied rows are cropped;
      # moving it would change the gradient color at the polygon's edges.
      midpoint_y = y0 + (band_y0 + band_y1 - 1) * 0.5
      gradient_position = 1.0 - midpoint_y / denominator
      color = tuple(int(np.interp(gradient_position, stops, color_array[:, channel])) for channel in range(4))
      alpha = color[3] / 255.0
      if alpha <= 0.0:
        continue
      band = roi[band_y0 + mask_y:band_y0 + mask_y + band_h, band_x:band_x + band_w]
      band_mask = band_mask[mask_y:mask_y + band_h, band_x:band_x + band_w]
      blended = blender.blend(band, color[:3], alpha) if blender is not None else cv2.LUT(band, _alpha_blend_lut(color[:3], alpha))
      cv2.copyTo(blended, band_mask, band)

  def _draw_model_path(self, frame, path_data, hud_data):
    calib_transform = path_data.get("calib_transform")
    if not path_data["path_x"] or calib_transform is None:
      self._reset_leads()
      return frame

    camera_region = frame[self.camera_y:self.camera_y + self.camera_h,
                          self.camera_x:self.camera_x + self.camera_w]
    camera_height = float(path_data.get("camera_height", self.camera_height) or self.camera_height)

    disengaged = self._get_border_color(hud_data) == Colors.Border.DISENGAGED
    if not disengaged:
      self._draw_lane_lines(camera_region, path_data, calib_transform)
    self._draw_path(camera_region, path_data, hud_data, calib_transform, camera_height, disengaged=disengaged)

    self._update_leads(path_data, hud_data, calib_transform, camera_height)
    self._draw_lead_indicator(camera_region, hud_data)
    self._draw_stop_point(camera_region)

    return frame

  def _draw_path(self, camera_region, path_data, hud_data, calib_transform, camera_height, *, disengaged):
    path_z = path_data.get("path_z") or [0.0] * len(path_data["path_x"])
    enabled = hud_data.get("enabled", False)
    steering_pressed = hud_data.get("steering_pressed", False)
    acceleration_x = path_data.get("acceleration_x")
    use_acceleration = (enabled and not (disengaged or steering_pressed)
                        and acceleration_x is not None and len(acceleration_x) >= 2)
    projected = self._map_line_to_polygon(
      path_data["path_x"], path_data["path_y"], path_z,
      0.9, camera_height, calib_transform, allow_invert=False, return_indices=use_acceleration,
    )
    if projected is None:
      return

    model_colors, model_stops = [], []
    if use_acceleration:
      path_poly, indices = projected
      track_y = path_poly[:len(indices), 1] - self.camera_y
      model_colors, model_stops = _acceleration_path_gradient(track_y, indices, acceleration_x, camera_region.shape[0])
    else:
      path_poly = projected

    style = model_render_support.get_path_style(disengaged=disengaged, steering_pressed=steering_pressed, enabled=enabled,
                                                has_acceleration_gradient=len(model_colors) > 1)
    local_path_poly = path_poly - np.array([self.camera_x, self.camera_y], dtype=np.int32)
    blender = getattr(self, "_color_blender", None)
    if style is model_render_support.PathStyle.DISENGAGED:
      self._fill_polygon_alpha(camera_region, local_path_poly, Colors.WHITE, model_render_support.DISENGAGED_PATH_ALPHA / 255.0, blender=blender)
      return
    if style is model_render_support.PathStyle.FALLBACK:
      self._fill_polygon_alpha(camera_region, local_path_poly, Colors.WHITE, model_render_support.FALLBACK_PATH_ALPHA / 255.0, blender=blender)
      return

    if style is model_render_support.PathStyle.ACCELERATION:
      colors, stops = model_colors, model_stops
    else:
      colors = Colors.STEERING_PRESSED if style is model_render_support.PathStyle.STEERING else STOCK_PATH_GRADIENT
      stops = [0.0, 0.5, 1.0]
    self._fill_polygon_gradient(camera_region, local_path_poly, colors, stops, blender=blender)

  def _draw_lane_lines(self, camera_region, path_data, calib_transform):
    lane_lines = path_data.get("lane_lines") or []
    lane_probs = path_data.get("lane_line_probs") or []
    for index, line in enumerate(lane_lines):
      if len(line) != 3:
        continue
      probability = float(lane_probs[index]) if index < len(lane_probs) else 0.0
      # A fully transparent line never changes camera pixels. Avoid creating
      # and projecting both 3D boundaries before the alpha fill rejects it.
      if probability <= 0.0:
        continue
      width = model_render_support.LANE_LINE_WIDTH * probability
      polygon = self._map_line_to_polygon(*line, width, 0.0, calib_transform)
      if polygon is not None:
        local_polygon = polygon - np.array([self.camera_x, self.camera_y], dtype=np.int32)
        self._fill_polygon_alpha(camera_region, local_polygon, Colors.WHITE, np.clip(probability, 0.0, 0.7),
                                 blender=getattr(self, "_color_blender", None))

    road_edges = path_data.get("road_edges") or []
    road_stds = path_data.get("road_edge_stds") or []
    for index, edge in enumerate(road_edges):
      if len(edge) != 3:
        continue
      confidence = 1.0 - float(road_stds[index]) if index < len(road_stds) else 0.0
      if confidence <= 0.0:
        continue
      polygon = self._map_line_to_polygon(*edge, model_render_support.ROAD_EDGE_WIDTH, 0.0, calib_transform)
      if polygon is not None:
        local_polygon = polygon - np.array([self.camera_x, self.camera_y], dtype=np.int32)
        self._fill_polygon_alpha(camera_region, local_polygon, colors_alpha(Colors.RED, 100), np.clip(confidence, 0.0, 1.0),
                                 blender=getattr(self, "_color_blender", None))

  def _reset_leads(self):
    self._lead_vehicles = [LeadVehicle(self.config.fps), LeadVehicle(self.config.fps)]
    self._lead_info = [{}, {}]
    self._stop_point = StopPoint(self.config.fps)

  def _update_leads(self, path_data, hud_data, calib_transform, camera_height):
    lead_info = list(path_data.get("leads") or [])[:2]
    lead_info += [{} for _ in range(2 - len(lead_info))]
    leads = [(lead.get("present", False), float(lead.get("d_camera", lead.get("d_rel", 0.0))),
              float(lead.get("y_rel", 0.0))) for lead in lead_info]

    # both leads can be the same vehicle
    if leads[0][0] and abs(leads[1][1] - leads[0][1]) < 3.0:
      leads[1] = (False, 0.0, 0.0)

    lane_lines = path_data.get("lane_lines") or []
    lane = np.empty((0, 3), dtype=np.float32)
    if len(lane_lines) > 2:
      left, right = np.asarray(lane_lines[1], dtype=np.float32).T, np.asarray(lane_lines[2], dtype=np.float32).T
      if left.shape == right.shape and left.ndim == 2 and left.shape[1] == 3:
        lane = (left + right) / 2
    path_x = path_data.get("path_x") or []
    path_z = path_data.get("path_z") or []
    # braking disengages without making openpilot unavailable
    available = hud_data.get("enabled") or hud_data.get("engageable") or hud_data.get("brake_pressed")
    opacity = 0.4 if self._get_border_color(hud_data) == Colors.Border.DISENGAGED else 0.8
    for i, (lead, (present, d_rel, y_rel)) in enumerate(zip(self._lead_vehicles, leads, strict=True)):
      visible = (available and present and 0.0 < d_rel < model_render_support.MAX_DRAW_DISTANCE
                 and len(lane) > 0 and len(path_x) == len(path_z) > 0)
      visible = visible and np.isfinite(y_rel)
      # snap to a new vehicle instead of sliding over
      if not visible or abs(y_rel - lead.y_filter.x) > 3.0:
        lead.d_filter.initialized = lead.y_filter.initialized = False
      lead.fade_filter.update(opacity if visible else 0.0)
      if visible:
        lead.bar = self._get_lead_bar(lane, lead.d_filter.update(d_rel), lead.y_filter.update(y_rel),
                                      path_data, calib_transform, camera_height)
        self._lead_info[i] = lead_info[i]

    self._update_stop_point(path_data, hud_data, leads, available, opacity, calib_transform, camera_height)

  def _update_stop_point(self, path_data, hud_data, leads, available, opacity, calib_transform, camera_height):
    stop = self._stop_point
    path_x = path_data["path_x"]
    velocity_x = path_data.get("velocity_x") or []
    model_frame_id = path_data.get("model_frame_id")
    if model_frame_id is None and velocity_x:
      model_frame_id = (tuple(path_x), tuple(velocity_x))
    model_updated = model_frame_id != stop.model_frame_id
    stop.model_frame_id = model_frame_id
    lead_distances = [d_rel for present, d_rel, _ in leads if present]
    planning_stop = stop.update_from_model(path_x, velocity_x, model_updated, float(hud_data.get("v_ego", 0.0) or 0.0), lead_distances)
    d_stop = stop.d + stop.radar_to_camera
    path_y, path_z = path_data.get("path_y") or [], path_data.get("path_z") or []
    visible = (hud_data.get("longitudinal_control") and available and planning_stop
               and 0.0 < d_stop < model_render_support.MAX_DRAW_DISTANCE and len(path_x) == len(path_y) == len(path_z) > 0)
    stop.fade_filter.update(opacity if visible else 0.0)
    if visible:
      path = np.column_stack((path_x, path_y, path_z))
      stop.bar = self._get_lead_bar(path, d_stop, 0.0, path_data, calib_transform, camera_height)

  def _draw_stop_point(self, camera_region):
    stop = self._stop_point
    opacity = stop.fade_filter.x
    if stop.bar.size and int(255 * opacity) > 0:
      offset = np.array([self.camera_x, self.camera_y], dtype=np.float32)
      polygon = (stop.bar - offset).astype(np.int32)
      self._fill_polygon_alpha(camera_region, polygon, Colors.WHITE, opacity, blender=self._color_blender)

  def _get_lead_bar(self, lane, d_rel, y_rel, path_data, calib_transform, camera_height):
    # bar on the road behind the lead, following the lane
    x = np.array([d_rel, d_rel - min(6.0, 0.25 * d_rel)])
    y = np.interp(x, lane[:, 0], lane[:, 1]) - np.interp(d_rel, lane[:, 0], lane[:, 1]) - y_rel
    z = np.interp(x, path_data["path_x"], path_data["path_z"]) + camera_height
    half_width = model_render_support.LEAD_BAR_WIDTH / 2
    corners = np.vstack((np.column_stack((x, y + half_width, z)), np.column_stack((x, y - half_width, z))[::-1]))
    pts = self._source_to_panel @ np.asarray(calib_transform, dtype=np.float32) @ corners.T
    if not np.all(np.isfinite(pts)) or np.any(pts[2] <= 1e-6):
      return np.empty((0, 2), dtype=np.float32)
    bar = (pts[:2] / pts[2]).T

    far, near = bar[[0, 3]], bar[[1, 2]]
    length = np.linalg.norm(near.mean(axis=0) - far.mean(axis=0))
    if length <= 1e-6:
      return np.empty((0, 2), dtype=np.float32)
    bar[[1, 2]] = far + (near - far) * np.clip(length, model_render_support.MIN_LEAD_BAR_LENGTH, model_render_support.LEAD_BAR_LENGTH) / length
    return bar.astype(np.float32)

  def _draw_lead_indicator(self, camera_region, hud_data):
    offset = np.array([self.camera_x, self.camera_y], dtype=np.float32)
    for lead, lead_info in zip(self._lead_vehicles, self._lead_info, strict=True):
      opacity = lead.fade_filter.x
      if lead.bar.size == 0 or int(255 * opacity) == 0:
        continue

      polygon = (lead.bar - offset).astype(np.int32)
      self._fill_polygon_alpha(camera_region, polygon, Colors.RED, opacity)
      x, y = map(int, lead.bar[[0, 3]].mean(axis=0) - offset)
      distance = float(lead_info.get("d_rel", 0.0))

      conversion = 3.6 if getattr(self.config, "is_metric", True) else 2.236936
      unit = "km/h" if getattr(self.config, "is_metric", True) else "mph"
      lead_speed = max(0.0, (float(hud_data.get("v_ego", 0.0) or 0.0) +
                             float(lead_info.get("v_rel", 0.0) or 0.0)) * conversion)
      labels = (f"{distance:.0f} m", f"{lead_speed:.0f} {unit}")
      for label, baseline_y in zip(labels, (y - 13, y + 27), strict=True):
        glyph = self._text_glyph(label, self.font_unit, 2, anchor="ms")
        left, top, right, bottom = glyph.bbox
        x0, y0 = max(0, x + left), max(0, baseline_y + top)
        x1 = min(camera_region.shape[1], x + right)
        y1 = min(camera_region.shape[0], baseline_y + bottom)
        if x0 >= x1 or y0 >= y1:
          continue
        roi = camera_region[y0:y1, x0:x1]
        overlay = Image.fromarray(roi)
        # Keep the original two-pass PIL stroke/fill rounding. Reusing masks
        # avoids rasterizing the same distance/speed labels for every frame.
        position = x - x0 - glyph.anchor_x, baseline_y - y0 - glyph.anchor_y
        overlay.paste(Colors.BLACK[:3], position, glyph.stroke_mask)
        overlay.paste(Colors.WHITE[:3], position, glyph.fill_mask)
        cv2.addWeighted(np.asarray(overlay), opacity, roi, 1.0 - opacity, 0, dst=roi)

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
      if len(self._icon_cache) > ICON_CACHE_MAX_ENTRIES:
        self._icon_cache.popitem(last=False)
    else:
      self._icon_cache.move_to_end(key)
    return image

  def _icon(self, image, name, cx, cy, size, active=True, rotation=0.0, opacity=255):
    icon = self._base_icon(name, size, active, opacity)
    if icon is None:
      return
    if rotation:
      # Steering angle and bearing usually change by fractions of a degree.
      # Re-running PIL's bicubic transform for both icons on every camera frame
      # is expensive on-device and has no visible benefit at these icon sizes.
      rotation_key = self._icon_rotation_key(rotation)
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
    position = (int(cx - icon.width / 2), int(cy - icon.height / 2))
    if image.mode == "RGBA" and getattr(self, "_straight_alpha_hud", False):
      image.alpha_composite(icon, position)
    else:
      image.paste(icon, position, icon)

  @staticmethod
  def _icon_rotation_key(rotation):
    return int(round(float(rotation) / 2.0) * 2) % 360

  def _icon_size_for_height(self, name, height):
    source = self.icons.get(name)
    if source is None or source.height <= 0:
      return int(height), int(height)
    return max(1, int(round(height * source.width / source.height))), int(height)

  def _text_glyph(self, text, font, stroke_width, anchor="mm"):
    key = (id(font), text, stroke_width) if anchor == "mm" else (id(font), text, stroke_width, anchor)
    cached = self._text_cache.get(key)
    if cached is None:
      cached = TextGlyph(text, font, stroke_width, anchor)
      if cached.byte_size <= TEXT_CACHE_MAX_BYTES:
        self._text_cache[key] = cached
        self._text_cache_bytes += cached.byte_size
        while len(self._text_cache) > TEXT_CACHE_MAX_ENTRIES or self._text_cache_bytes > TEXT_CACHE_MAX_BYTES:
          _, evicted = self._text_cache.popitem(last=False)
          self._text_cache_bytes -= evicted.byte_size
    else:
      self._text_cache.move_to_end(key)
    return cached

  def _centered(self, draw, cx, cy, value, font, color, stroke_width=0, stroke_fill=None):
    text = str(value)
    fill = tuple(color) if isinstance(color, (tuple, list)) else color
    # ImageDraw text on an RGB target with an RGBA drawing context ignores the
    # fill alpha (unlike rectangles). Mirror that existing behavior so cached
    # glyphs are pixel-identical to the former direct draw.text() calls.
    if getattr(draw, "mode", None) == "RGBA" and isinstance(fill, tuple) and len(fill) == 4:
      fill = (*fill[:3], 255)
    stroke = tuple(stroke_fill) if isinstance(stroke_fill, (tuple, list)) else stroke_fill
    cached = self._text_glyph(text, font, stroke_width)

    sprite = cached.colored(fill, stroke)
    target = draw._image
    position = (int(cx) - cached.anchor_x, int(cy) - cached.anchor_y)
    if target.mode == "RGBA" and getattr(self, "_straight_alpha_hud", False):
      target.alpha_composite(sprite, position)
    else:
      target.paste(sprite, position, sprite)

  def _value(self, value, disabled="--"):
    try:
      speed = float(value)
      return str(round(speed)) if hud_render_support.is_cruise_speed_set(speed) else disabled
    except (TypeError, ValueError):
      return disabled

  @staticmethod
  def _active_speed_limit(data):
    """Select one coherent speed-limit source for all cluster widgets."""
    if data.get("nda_state"):
      if data.get("section_limit_speed", 0) > 0 and data.get("section_left_dist", 0) > 0:
        return float(data.get("section_limit_speed")), float(data.get("section_left_dist"))
      camera_active = data.get("cam_limit_active", data.get("cam_limit_speed", 0) > 0 and
                               data.get("cam_limit_speed_left_dist", 0) > 0)
      if camera_active:
        return float(data.get("cam_limit_speed")), float(data.get("cam_limit_speed_left_dist"))
      return float(data.get("nav_limit_speed", 0.0) or 0.0), 0.0
    if data.get("stock_limit_speed", 0) > 0:
      return (
        float(data.get("stock_limit_speed")),
        float(data.get("stock_limit_speed_left_dist", 0.0) or 0.0),
      )
    return float(data.get("stock_road_limit_speed", 0.0) or 0.0), 0.0

  def _draw_hud(self, image, data, has_camera):
    draw = ImageDraw.Draw(image)
    # The camera copy overwrites its left divider from the cached base canvas.
    draw.line((self.camera_x, self.content_y, self.camera_x, self.content_y + self.content_h - 1),
              fill=Colors.DIVIDER, width=2)

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
    status = hud_render_support.get_hud_status(
      enabled=bool(data.get("enabled")), lat_active=bool(data.get("lat_active")),
      pre_enabled_or_overriding=bool(data.get("pre_enabled_or_overriding")), steering_pressed=bool(data.get("steering_pressed")),
      brake_pressed=bool(data.get("brake_pressed")), blinker=bool(data.get("left_blinker") or data.get("right_blinker")),
      reverse=bool(data.get("reverse")), cruise_available=bool(data.get("cruise_available")), v_ego=float(data.get("v_ego", 0.0) or 0.0),
    )
    return getattr(Colors.Border, status.name)

  def _draw_ignore_limit_timer(self, image, data):
    try:
      timer = float(data.get("ignore_limit_timer", 0.0) or 0.0)
    except (TypeError, ValueError):
      return
    bar_width = int(round(self.target_w * hud_render_support.ignore_limit_ratio(timer)))
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
      speed_color = Colors.WHITE
      max_color = Colors.MAX_ACTIVE if data.get("enabled") else Colors.Border.OVERRIDE

      limit_speed, _ = self._active_speed_limit(data)

      cruise_speed = float(data.get("cruise_speed", 0.0) or 0.0)
      if limit_speed > 0 and data.get("enabled"):
        warning = hud_render_support.speed_limit_warning_level(cruise_speed, limit_speed)
        speed_color = (Colors.WHITE, Colors.CAUTION, Colors.ORANGE, Colors.RED)[warning]

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
      dist_text = hud_render_support.format_speed_limit_distance(left_dist)
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

    autohold_state = data.get("autohold_state") or 0
    if autohold_state >= 1:
      pedal_icon = "autohold_warning" if autohold_state > 1 else "autohold_active"
    elif data.get("brake_pressed"):
      pedal_icon = "brake_pressed"
    elif data.get("gas_pressed"):
      pedal_icon = "accel_pressed"
    else:
      pedal_icon = None
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
    traffic_name = TRAFFIC_ICONS[hud_render_support.traffic_light_index(data.get("traffic_state"))]
    self._icon(image, traffic_name, cx, self.row_centers[0], (68, 136), True)

    # Longitudinal indicator occupies the same middle cell as the old gap icon.
    for (name, size, offset), opacity in zip(self._long_indicator_layout, self._long_indicator_alphas, strict=False):
      if opacity:
        self._icon(image, name, cx, self.row_centers[1] + offset, size, opacity=opacity)

    # tpms icon
    tpms = self._base_icon("tpms", (108, 140), True)
    if tpms is not None:
      tpms_x = int(cx - tpms.width / 2)
      tpms_y = int(self.content_y + self.row_h * 2.0 + 7)
      if image.mode == "RGBA" and getattr(self, "_straight_alpha_hud", False):
        image.alpha_composite(tpms, (tpms_x, tpms_y))
      else:
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
      value, low = hud_render_support.tpms_reading(pressure)
      color = Colors.ORANGE if value == "--" else red if low else Colors.SIGN_TEXT
      self._centered(draw, px, py, value, self.font_small, color)

  @staticmethod
  def _current_speed_color(data):
    return hud_render_support.current_speed_color(float(data.get("accel", 0.0) or 0.0))

  def _current_speed_text(self, data):
    conversion = 3.6 if getattr(self.config, "is_metric", True) else 2.236936
    return "--" if data.get("_state_stale") else round(max(0.0, float(data.get("v_ego", 0.0) or 0.0) * conversion))

  def _draw_camera_overlays(self, draw, data, white):
    speed_color = self._current_speed_color(data)
    speed = self._current_speed_text(data)
    center_x = self.camera_x + self.camera_w / 2
    # Align the current-speed value with the center of b's speed-limit cell.
    self._centered(draw, center_x, self.row_centers[0],
                   speed, self.font_current_speed, speed_color,
                   stroke_width=2, stroke_fill=Colors.BLACK)
    self._centered(draw, center_x, self.content_y + self.row_h * 0.86, self.config.speed_unit,
                   self.font_current_unit, white, stroke_width=1, stroke_fill=Colors.BLACK)
