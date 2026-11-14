import math
import numpy as np
import raylib_nb as rl
from dataclasses import dataclass
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.onroad import DIMMED_ICON_ALPHA
from openpilot.selfdrive.ui.mici.onroad.status import STATUS_FADE_RC, lerp_color, status_fade
from openpilot.selfdrive.ui.mici.onroad.torque_bar import TorqueBar
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus, ChestnutState
from openpilot.system.ui.lib.application import gui_app, FontWeight, ASSETS_DIR
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.common.filter_simple import FirstOrderFilter

from openpilot.selfdrive.ui import Colors, colors_alpha
from openpilot.selfdrive.ui.mici.onroad import RIGHT_ICON_CENTER_OFFSET, left_column_rect


# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'

SET_SPEED_PERSISTENCE = 2.5  # seconds
WHEEL_SIZE = 50
WHEEL_WHITE = rl.Color(255, 255, 255, 255)
WHEEL_GREEN = rl.Color(0, 255, 64, 255)
WHEEL_ORANGE = rl.Color(255, 115, 0, 255)


def _texture(rgba: np.ndarray, px: float) -> rl.Texture:
  texture = rl.load_texture_from_image(rl.Image(None, rgba.shape[1], rgba.shape[0], 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8))
  rl.update_texture(texture, np.ascontiguousarray(rgba))
  rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
  texture.width, texture.height = round(rgba.shape[1] / px), round(rgba.shape[0] / px)
  return texture


def _wheel_effect_textures(asset: str, size: int) -> tuple[rl.Texture, rl.Texture]:
  """White glow and center highlight for the wheel, matching the design's render. Tint the glow when drawing."""
  image = gui_app._load_image_from_path((ASSETS_DIR / asset).as_posix(), size, size)
  w, h, px = image.width, image.height, image.width / size
  alpha = np.frombuffer(image.to_bytes(w * h * 4), dtype=np.uint8).reshape(h, w, 4)[..., 3] / 255.0
  rl.unload_image(image)

  # glow: Figma's drop shadow (blur 24 at 128px) renders as a gaussian with sigma 10, its spread is ignored on vectors
  sigma = 10 * size / 128 * px
  pad = math.ceil(3 * sigma)
  glow = np.pad(alpha, pad)
  x = np.arange(-pad, pad + 1)
  kernel = np.exp(-x ** 2 / (2 * sigma ** 2))
  kernel /= kernel.sum()
  for axis in (0, 1):
    glow = np.apply_along_axis(np.convolve, axis, glow, kernel, mode='same')
  glow_rgba = np.full((*glow.shape, 4), 255, dtype=np.uint8)
  glow_rgba[..., 3] = np.clip(glow * 255, 0, 255)

  # highlight: the fill is a radial gradient from 35% lighter at the center to the base color at the rim
  yy, xx = np.mgrid[:h, :w]
  r = np.hypot(xx + 0.5 - w / 2, yy + 0.5 - h / 2) / (w / 2)
  highlight_rgba = np.full((h, w, 4), 255, dtype=np.uint8)
  highlight_rgba[..., 3] = np.clip(alpha * 89 * (1 - r), 0, 255)

  return _texture(glow_rgba, px), _texture(highlight_rgba, px)


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 72
  speed_unit: int = 24
  max_speed: int = 36
  set_speed: int = 112
  middle: int = 14
  big: int = 26


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self._set_speed_changed_time: float = 0
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False
    self._engaged: bool = False

    self.cruise_speed: float = 0.0
    self.apply_speed: float = 0.0
    self.nda_state: int = 0
    self.stock_limit_speed: float = 0.0
    self.accel: float = 0.0
    self.traffic_state: int = 0
    self.ignore_limit_timer: float = 0.0

    self._can_draw_top_icons = True
    self._show_wheel_critical = False

    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)
    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_display: rl.Font = gui_app.font(FontWeight.DISPLAY)

    self._torque_bar = TorqueBar()

    self._txt_wheel: rl.Texture = gui_app.texture('icons/steer.png', WHEEL_SIZE, WHEEL_SIZE)
    self._txt_wheel_glow, self._txt_wheel_highlight = _wheel_effect_textures('icons_mici/wheel.png', WHEEL_SIZE)

    self._txt_wheel_critical: rl.Texture = gui_app.texture('icons/steer_critical.png', WHEEL_SIZE, WHEEL_SIZE)
    self._txt_wheel_green: rl.Texture = gui_app.texture('icons/steer_green.png', WHEEL_SIZE, WHEEL_SIZE)
    self._txt_wheel_blue: rl.Texture = gui_app.texture('icons/steer_blue.png', WHEEL_SIZE, WHEEL_SIZE)

    self._txt_exclamation_point: rl.Texture = gui_app.texture('icons_mici/exclamation_point.png', 9, 44)
    self._txt_chestnut: rl.Texture = gui_app.texture('icons_mici/chestnut.png', 48, 35)
    self._txt_chestnut_green: rl.Texture = gui_app.texture('icons_mici/chestnut_green.png', 48, 35)
    self._txt_chestnut_orange: rl.Texture = gui_app.texture('icons_mici/chestnut_orange.png', 60, 35)

    self._txt_traffic_off = gui_app.texture("icons/traffic_off.png", 77, 154)
    self._txt_traffic_green = gui_app.texture("icons/traffic_green.png", 77, 154)
    self._txt_traffic_red = gui_app.texture("icons/traffic_red.png", 77, 154)

    self._wheel_alpha_filter = FirstOrderFilter(0, 0.05, 1 / gui_app.target_fps)
    self._wheel_critical_filter = FirstOrderFilter(0, STATUS_FADE_RC, 1 / gui_app.target_fps)

    self._wheel_y_filter = FirstOrderFilter(0, 0.1, 1 / gui_app.target_fps)

    self._set_speed_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._chestnut_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

  def set_wheel_critical_icon(self, critical: bool):
    """Set the wheel icon to critical or normal state."""
    self._show_wheel_critical = critical

  def set_torque_bar_hidden(self, hidden: bool):
    self._torque_bar.set_hidden(hidden)

  def set_can_draw_top_icons(self, can_draw_top_icons: bool):
    """Set whether to draw the top part of the HUD."""
    self._can_draw_top_icons = can_draw_top_icons

  def drawing_top_icons(self) -> bool:
    # whether we're drawing any top icons currently
    return bool(self._set_speed_alpha_filter.x > 1e-2)

  def _update_state(self) -> None:
    """Update HUD state based on car state and controls state."""
    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      self.is_cruise_set = False
      self.set_speed = SET_SPEED_NA
      self.speed = 0.0
      self.traffic_state = 0
      self.ignore_limit_timer = 0.0
      return

    car_state = sm['carState']
    navi_data = sm['naviData']
    longitudinal_plan = sm['longitudinalPlan']

    set_speed = car_state.vCruiseCluster
    self.apply_speed = car_state.vCruise

    engaged = sm['selfdriveState'].enabled
    if (set_speed != self.set_speed and engaged) or (engaged and not self._engaged):
      self._set_speed_changed_time = rl.get_time()
    self._engaged = engaged
    self.set_speed = set_speed
    self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
    self.is_cruise_available = self.set_speed != -1

    if self.is_cruise_set and not ui_state.is_metric:
      self.set_speed *= KM_TO_MILE
      self.cruise_speed *= KM_TO_MILE
      self.apply_speed *= KM_TO_MILE

    # Update Current Speed
    v_ego_cluster = car_state.vEgoCluster
    self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed = max(0.0, v_ego * speed_conversion)
    self.accel = car_state.aEgo

    # Extended State for SET Speed logic
    self.stock_limit_speed = car_state.speedLimit if hasattr(car_state, 'speedLimit') else 0
    if navi_data:
      self.nda_state = navi_data.active if hasattr(navi_data, 'active') else 0

    if longitudinal_plan:
      self.traffic_state = longitudinal_plan.trafficState if hasattr(longitudinal_plan, 'trafficState') else 0

    self.ignore_limit_timer = car_state.ignoreLimitTimer

  def _get_wheel_texture(self) -> rl.Texture:
    """Return the correct wheel texture based on current UI status."""
    if self._show_wheel_critical or ui_state.status == UIStatus.BLINKER:
      return self._txt_wheel_critical
    elif ui_state.status == UIStatus.STEERING:
      return self._txt_wheel_blue
    elif ui_state.status in (UIStatus.ENGAGED, UIStatus.ACTIVE):
      return self._txt_wheel_green
    return self._txt_wheel

  def _draw_text(self, x: float, y: float, text: str, font_size: int, text_color: rl.Color, alignment: str = "C") -> None:
    """Helper method for drawing aligned text."""
    text_size = measure_text_cached(self._font_bold, text, font_size)

    if alignment == "L":
      draw_x = x
    elif alignment == "R":
      draw_x = x - text_size.x
    else: # C
      draw_x = x - text_size.x / 2

    rl.draw_text_ex(
      self._font_bold,
      text,
      rl.Vector2(draw_x, y - text_size.y / 2),
      font_size,
      0,
      text_color
    )

  def _draw_ignore_limit_timer(self, rect: rl.Rectangle) -> None:
    max_ticks = 3000.0

    if self.ignore_limit_timer <= 0 or self.ignore_limit_timer >= max_ticks:
      return

    ratio = max(0.0, min(1.0, (max_ticks - self.ignore_limit_timer) / max_ticks))

    bar_height = 10
    bar_width = rect.width * ratio

    bar_x = rect.x + (rect.width - bar_width) / 2
    bar_y = rect.y

    bar_color = colors_alpha(Colors.ORANGE, 150)
    rl.draw_rectangle(int(bar_x), int(bar_y), int(bar_width), bar_height, bar_color)

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""

    #self._torque_bar.render(rect)

    self._draw_current_speed(rect)
    self._draw_borders(rect)

    if not ui_state.cluster_enabled:
      self._draw_set_speed(rect)
      self._draw_traffic_light(rect)
      self._draw_steering_wheel(rect)
    self._draw_ignore_limit_timer(rect)

    self._draw_model_source(rect)

  def render_steering_wheel(self, rect: rl.Rectangle) -> None:
    """Draw the steering wheel centered at the bottom of rect, after render() has updated state."""
    self._draw_steering_wheel(rect)

  def _draw_model_source(self, rect: rl.Rectangle) -> None:
    loading = ui_state.chestnut_state == ChestnutState.LOADING
    if loading:
      icon = self._txt_chestnut
      opacity = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(rl.get_time() * 6.0))
    elif ui_state.chestnut_state in (ChestnutState.UNCOMPILED, ChestnutState.FAILED):
      icon = self._txt_chestnut_orange
      opacity = 1.0
    elif ui_state.chestnut_state == ChestnutState.ACTIVE:
      icon = self._txt_chestnut_green
      opacity = 1.0
    else:
      return

    alpha = self._chestnut_alpha_filter.update(self._get_wheel_opacity())
    if alpha < 1e-2:
      return

    pos = rl.Vector2(rect.x + rect.width - RIGHT_ICON_CENTER_OFFSET - icon.width / 2,
                     rect.y + rect.height - 14 - (self._txt_wheel.height + icon.height) / 2 + (1 - alpha) * icon.height / 2)
    rl.draw_texture_ex(icon, pos, 0.0, 1.0, rl.Color(255, 255, 255, int(255 * opacity * alpha)))

  def _get_wheel_opacity(self) -> float:
    if self._show_wheel_critical or ui_state.status == UIStatus.BLINKER:
      return 1.0
    return 0.5 if ui_state.status == UIStatus.DISENGAGED else 0.9

  def _draw_steering_wheel(self, rect: rl.Rectangle) -> None:
    # The wheel is the source of truth for engagement: green if and only if openpilot is engaged.
    # When disengaged it's grayed out, except for steerRequired alerts which show it orange.
    critical = self._wheel_critical_filter.update(self._show_wheel_critical)
    idle_color = lerp_color(WHEEL_WHITE, WHEEL_ORANGE, critical)
    wheel_color = status_fade.color({UIStatus.ENGAGED: WHEEL_GREEN, UIStatus.OVERRIDE: idle_color, UIStatus.DISENGAGED: idle_color})
    engaged = status_fade.weight(UIStatus.ENGAGED)
    glow = engaged + (1 - engaged) * critical
    wheel_txt = self._txt_wheel

    if self._show_wheel_critical or ui_state.status != UIStatus.DISENGAGED:
      self._wheel_alpha_filter.update(255)
    else:
      self._wheel_alpha_filter.update(255 * DIMMED_ICON_ALPHA)

    # pos
    pos_x = int(rect.x + rect.width / 2)
    pos_y = int(rect.y + rect.height - 10 - wheel_txt.height / 2)
    rotation = -ui_state.sm['carState'].steeringAngleDeg

    alpha = self._wheel_alpha_filter.x
    origin = rl.Vector2(self._txt_wheel_glow.width / 2, self._txt_wheel_glow.height / 2)
    rl.draw_texture_pro(self._txt_wheel_glow, rl.Rectangle(0, 0, self._txt_wheel_glow.width, self._txt_wheel_glow.height),
                        rl.Rectangle(pos_x, pos_y, self._txt_wheel_glow.width, self._txt_wheel_glow.height), origin, rotation,
                        rl.Color(wheel_color.r, wheel_color.g, wheel_color.b, int(alpha * glow)))
    rl.draw_texture_pro(wheel_txt, rl.Rectangle(0, 0, wheel_txt.width, wheel_txt.height),
                        rl.Rectangle(pos_x, pos_y, wheel_txt.width, wheel_txt.height),
                        rl.Vector2(wheel_txt.width / 2, wheel_txt.height / 2), rotation, rl.Color(wheel_color.r, wheel_color.g, wheel_color.b, int(alpha)))
    rl.draw_texture_pro(self._txt_wheel_highlight, rl.Rectangle(0, 0, wheel_txt.width, wheel_txt.height),
                        rl.Rectangle(pos_x, pos_y, wheel_txt.width, wheel_txt.height),
                        rl.Vector2(wheel_txt.width / 2, wheel_txt.height / 2), rotation, rl.Color(255, 255, 255, int(alpha * glow)))


  def _draw_borders(self, rect: rl.Rectangle) -> None:
    """Draw borders for blinkers, blind spot, and system status."""
    sm = ui_state.sm
    car_state = sm['carState']

    blink_period = 0.9  # seconds
    blinking = (rl.get_time() % blink_period) < (blink_period / 2)
    border_size = 10

    left_blinker = car_state.leftBlinker
    right_blinker = car_state.rightBlinker
    left_blindspot = car_state.leftBlindspot
    right_blindspot = car_state.rightBlindspot

    is_braking = (ui_state.status == UIStatus.RED)
    is_override = (ui_state.status == UIStatus.OVERRIDE)
    is_steering = (ui_state.status == UIStatus.STEERING)
    is_standby = (ui_state.status == UIStatus.BLINKER)
    is_engaged = (ui_state.status in (UIStatus.ENGAGED, UIStatus.ACTIVE))

    alpha = 100

    # --- Left Border ---
    left_color = None
    left_draw = False

    if is_braking or left_blindspot:
      left_color = colors_alpha(Colors.RED, alpha)
      left_draw = True
    elif left_blinker:
      left_color = colors_alpha(Colors.ORANGE, alpha)
      left_draw = blinking
    elif is_steering:
      left_color = colors_alpha(Colors.STEERING, alpha)
      left_draw = True
    elif is_override:
      left_color = colors_alpha(Colors.OVERRIDE, alpha)
      left_draw = True
    elif is_standby:
      left_color = colors_alpha(Colors.ORANGE, alpha)
      left_draw = True
    elif is_engaged:
      left_color = colors_alpha(Colors.ENGAGED, alpha)
      left_draw = True

    if left_draw:
      rl.draw_rectangle(int(rect.x), int(rect.y), border_size, int(rect.height), left_color)

    # --- Right Border ---
    right_color = None
    right_draw = False

    if is_braking or right_blindspot:
      right_color = colors_alpha(Colors.RED, alpha)
      right_draw = True
    elif right_blinker:
      right_color = colors_alpha(Colors.ORANGE, alpha)
      right_draw = blinking
    elif is_steering:
      right_color = colors_alpha(Colors.STEERING, alpha)
      right_draw = True
    elif is_override:
      right_color = colors_alpha(Colors.OVERRIDE, alpha)
      right_draw = True
    elif is_standby:
      right_color = colors_alpha(Colors.ORANGE, alpha)
      right_draw = True
    elif is_engaged:
      right_color = colors_alpha(Colors.ENGAGED, alpha)
      right_draw = True

    if right_draw:
      rl.draw_rectangle(int(rect.x + rect.width - border_size), int(rect.y), border_size, int(rect.height), right_color)

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX and SET speed indicator boxes on the left side."""
    if not self._can_draw_top_icons or ui_state.status == UIStatus.DISENGAGED:
      return

    max_color = colors_alpha(rl.WHITE, 200)
    speed_color = colors_alpha(rl.WHITE, 200)

    if self.is_cruise_set:
      speed_color = rl.WHITE
      if ui_state.status in (UIStatus.ENGAGED, UIStatus.ACTIVE):
        max_color = Colors.ENGAGED
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = Colors.OVERRIDE

    # Max speed box
    max_speed_box_bg = left_column_rect(rect, 0)
    rl.draw_rectangle_rounded(max_speed_box_bg, 0.2, 10, colors_alpha(rl.BLACK, 100))

    max_speed_box = rl.Rectangle(max_speed_box_bg.x + 2, max_speed_box_bg.y + 2,
                                 max_speed_box_bg.width - 4, max_speed_box_bg.height - 4)
    rl.draw_rectangle_rounded_lines_ex(max_speed_box, 0.2, 10, 1, colors_alpha(rl.WHITE, 200))

    # MAX text
    self._draw_text(
      max_speed_box.x + max_speed_box.width / 2,
      max_speed_box.y + 14,
      tr("MAX"),
      FontSizes.middle,
      max_color
    )

    # MAX speed value
    max_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.cruise_speed))
    self._draw_text(
      max_speed_box.x + max_speed_box.width / 2,
      max_speed_box.y + 32,
      max_speed_text,
      FontSizes.big,
      speed_color
    )

    # SET speed box with background (only if NDA or stock limit is active)
    if self.nda_state > 0 or self.stock_limit_speed > 0:
      set_speed_box_bg = left_column_rect(rect, 1)
      rl.draw_rectangle_rounded(set_speed_box_bg, 0.2, 10, colors_alpha(rl.BLACK, 100))

      set_speed_box = rl.Rectangle(set_speed_box_bg.x + 2, set_speed_box_bg.y + 2,
                                   set_speed_box_bg.width - 4, set_speed_box_bg.height - 4)
      rl.draw_rectangle_rounded_lines_ex(set_speed_box, 0.2, 10, 1, colors_alpha(rl.WHITE, 200))

      # SET text
      self._draw_text(
        set_speed_box.x + set_speed_box.width / 2,
        set_speed_box.y + 14,
        tr("SET"),
        FontSizes.middle,
        max_color
      )

      # SET speed value
      set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.apply_speed))
      self._draw_text(
        set_speed_box.x + set_speed_box.width / 2,
        set_speed_box.y + 32,
        set_speed_text,
        FontSizes.big,
        speed_color,
      )

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Draw the current vehicle speed and unit at the top center."""

    if not self._can_draw_top_icons:
      return

    # Color based on accel/decel
    if self.accel > 0:
      # Accelerating - green
      alpha = int(255 - (180 * min(self.accel / 3.0, 1.0)))
      alpha = max(80, min(255, alpha))
      speed_color = rl.Color(alpha, 255, alpha, 255)
    else:
      # Decelerating - red
      alpha = int(255 - (255 * min(abs(self.accel) / 4.0, 1.0)))
      alpha = max(60, min(255, alpha))
      speed_color = rl.Color(255, alpha, alpha, 255)

    speed_text = str(round(self.speed))
    center_x = rect.x + rect.width / 2

    speed_text_size = measure_text_cached(self._font_bold, speed_text, FontSizes.current_speed)
    speed_pos = rl.Vector2(center_x - speed_text_size.x / 2, rect.y - 5)
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FontSizes.current_speed, 0, speed_color)

    unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FontSizes.speed_unit)
    unit_pos = rl.Vector2(center_x - unit_text_size.x / 2, rect.y + speed_text_size.y - 15)
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FontSizes.speed_unit, 0, colors_alpha(rl.WHITE, 200))

  def _draw_traffic_light(self, rect: rl.Rectangle) -> None:
    if not self._can_draw_top_icons or ui_state.status == UIStatus.DISENGAGED:
      return

    img_w = 32
    img_h = 64

    img_x = rect.x + rect.width - RIGHT_ICON_CENTER_OFFSET - img_w / 2
    img_y = rect.y + 10

    if self.traffic_state == 1:
      tex = self._txt_traffic_red
    elif self.traffic_state == 2:
      tex = self._txt_traffic_green
    else:
      tex = self._txt_traffic_off

    rl.draw_texture_pro(
      tex,
      rl.Rectangle(0, 0, tex.width, tex.height),
      rl.Rectangle(img_x, img_y, img_w, img_h),
      rl.Vector2(0, 0), 0, rl.WHITE
    )
