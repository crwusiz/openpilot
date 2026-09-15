import pyray as rl
from dataclasses import dataclass
from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.onroad.torque_bar import TorqueBar
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.common.filter_simple import FirstOrderFilter
from cereal import log

EventName = log.OnroadEvent.EventName

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'

SET_SPEED_PERSISTENCE = 2.5  # seconds


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 72
  speed_unit: int = 24
  max_speed: int = 36
  set_speed: int = 112
  middle: int = 16
  big: int = 30


def colors_alpha(color, alpha):
  if isinstance(color, tuple):
    return rl.Color(color[0], color[1], color[2], alpha)
  return rl.Color(color.r, color.g, color.b, alpha)


@dataclass(frozen=True)
class Colors:
  BLACK = rl.BLACK
  WHITE = rl.WHITE
  WHITE_TRANSLUCENT = colors_alpha(WHITE, 200)
  BLACK_TRANSLUCENT = colors_alpha(BLACK, 166)
  BOX_BG = colors_alpha(BLACK, 100)
  RED = rl.Color(201, 34, 49, 255)
  ORANGE = rl.Color(255, 149, 0, 255)
  GREEN = rl.Color(128, 216, 166, 255)
  STEERING = rl.Color(0, 191, 255, 255)
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = DISENGAGED


class TurnIntent(Widget):
  FADE_IN_ANGLE = 30  # degrees

  def __init__(self):
    super().__init__()
    self._pre = False
    self._turn_intent_direction: int = 0

    self._turn_intent_alpha_filter = FirstOrderFilter(0, 0.05, 1 / gui_app.target_fps)
    self._turn_intent_rotation_filter = FirstOrderFilter(0, 0.1, 1 / gui_app.target_fps)

    self._txt_turn_intent_left: rl.Texture = gui_app.texture('icons_mici/turn_intent_left.png', 50, 20)
    self._txt_turn_intent_right: rl.Texture = gui_app.texture('icons_mici/turn_intent_left.png', 50, 20, flip_x=True)

  def _render(self, _):
    if self._turn_intent_alpha_filter.x > 1e-2:
      turn_intent_texture = self._txt_turn_intent_right if self._turn_intent_direction == 1 else self._txt_turn_intent_left
      src_rect = rl.Rectangle(0, 0, turn_intent_texture.width, turn_intent_texture.height)
      dest_rect = rl.Rectangle(self._rect.x + self._rect.width / 2, self._rect.y + self._rect.height / 2,
                               turn_intent_texture.width, turn_intent_texture.height)

      origin = (turn_intent_texture.width / 2, self._rect.height / 2)
      color = rl.Color(255, 255, 255, int(255 * self._turn_intent_alpha_filter.x))
      rl.draw_texture_pro(turn_intent_texture, src_rect, dest_rect, origin, self._turn_intent_rotation_filter.x, color)

  def _update_state(self) -> None:
    sm = ui_state.sm

    left = any(e.name == EventName.preLaneChangeLeft for e in sm['onroadEvents'])
    right = any(e.name == EventName.preLaneChangeRight for e in sm['onroadEvents'])
    if left or right:
      # pre lane change
      if not self._pre:
        self._turn_intent_rotation_filter.x = self.FADE_IN_ANGLE if left else -self.FADE_IN_ANGLE

      self._pre = True
      self._turn_intent_direction = -1 if left else 1
      self._turn_intent_alpha_filter.update(1)
      self._turn_intent_rotation_filter.update(0)
    elif any(e.name == EventName.laneChange for e in sm['onroadEvents']):
      # fade out and rotate away
      self._pre = False
      self._turn_intent_alpha_filter.update(0)

      if self._turn_intent_direction == 0:
        # unknown. missed pre frame?
        self._turn_intent_rotation_filter.update(0)
      else:
        self._turn_intent_rotation_filter.update(self._turn_intent_direction * self.FADE_IN_ANGLE)
    else:
      # didn't complete lane change, just hide
      self._pre = False
      self._turn_intent_direction = 0
      self._turn_intent_alpha_filter.update(0)
      self._turn_intent_rotation_filter.update(0)


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self.cruise_speed: float = 0.0
    self.apply_speed: float = 0.0
    self.nda_state: int = 0
    self.stock_limit_speed: float = 0.0
    self.accel: float = 0.0
    self.traffic_state: int = 0
    self._set_speed_changed_time: float = 0
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False
    self._engaged: bool = False

    self._can_draw_top_icons = True
    self._show_wheel_critical = False

    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)
    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_display: rl.Font = gui_app.font(FontWeight.DISPLAY)

    self._turn_intent = TurnIntent()
    #self._torque_bar = TorqueBar()

    self._txt_wheel: rl.Texture = gui_app.texture('icons_mici/wheel.png', 50, 50)
    self._txt_wheel_green: rl.Texture = gui_app.texture('icons_mici/wheel_green.png', 50, 50)
    self._txt_wheel_blue: rl.Texture = gui_app.texture('icons_mici/wheel_blue.png', 50, 50)
    self._txt_wheel_critical: rl.Texture = gui_app.texture('icons_mici/wheel_critical.png', 50, 50)
    self._txt_exclamation_point: rl.Texture = gui_app.texture('icons_mici/exclamation_point.png', 44, 44)

    self._txt_traffic_off = gui_app.texture("icons/traffic_off.png", 77, 154)
    self._txt_traffic_green = gui_app.texture("icons/traffic_green.png", 77, 154)
    self._txt_traffic_red = gui_app.texture("icons/traffic_red.png", 77, 154)

    self._wheel_alpha_filter = FirstOrderFilter(0, 0.05, 1 / gui_app.target_fps)
    self._wheel_y_filter = FirstOrderFilter(0, 0.1, 1 / gui_app.target_fps)

    self._set_speed_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

  def set_wheel_critical_icon(self, critical: bool):
    """Set the wheel icon to critical or normal state."""
    self._show_wheel_critical = critical

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
      return

    controls_state = sm['controlsState']
    car_state = sm['carState']
    navi_data = sm['naviData']
    longitudinal_plan = sm['longitudinalPlan']

    v_cruise_cluster = car_state.vCruiseCluster
    self.cruise_speed = v_cruise_cluster if v_cruise_cluster > 0 else controls_state.deprecated.vCruise
    self.apply_speed = car_state.vCruise
    set_speed = self.cruise_speed

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

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""
    #self._torque_bar.render(rect)
    self._draw_current_speed(rect)
    self._draw_set_speed(rect)
    self._draw_steering_wheel(rect)
    self._draw_borders(rect)
    self._draw_traffic_light(rect)

  def _draw_steering_wheel(self, rect: rl.Rectangle) -> None:
    wheel_txt = self._get_wheel_texture()
    is_critical = self._show_wheel_critical or ui_state.status == UIStatus.BLINKER

    if is_critical:
      self._wheel_alpha_filter.update(255)
      self._wheel_y_filter.update(0)
    else:
      if ui_state.status == UIStatus.DISENGAGED:
        self._wheel_alpha_filter.update(255 * 0.5)
        self._wheel_y_filter.update(0)
      else:
        self._wheel_alpha_filter.update(255 * 0.9)
        self._wheel_y_filter.update(0)

    # pos
    pos_x = int(rect.x + 21 + wheel_txt.width / 2)
    pos_y = int(rect.y + rect.height - 14 - wheel_txt.height / 2 + self._wheel_y_filter.x)
    rotation = -ui_state.sm['carState'].steeringAngleDeg

    turn_intent_margin = 25
    self._turn_intent.render(rl.Rectangle(
      pos_x - wheel_txt.width / 2 - turn_intent_margin,
      pos_y - wheel_txt.height / 2 - turn_intent_margin,
      wheel_txt.width + turn_intent_margin * 2,
      wheel_txt.height + turn_intent_margin * 2,
    ))

    src_rect = rl.Rectangle(0, 0, wheel_txt.width, wheel_txt.height)
    dest_rect = rl.Rectangle(pos_x, pos_y, wheel_txt.width, wheel_txt.height)
    origin = (wheel_txt.width / 2, wheel_txt.height / 2)

    # color and draw
    color = rl.Color(255, 255, 255, int(self._wheel_alpha_filter.x))
    rl.draw_texture_pro(wheel_txt, src_rect, dest_rect, origin, rotation, color)

    if is_critical:
      # Draw exclamation point icon
      EXCLAMATION_POINT_SPACING = 10
      exclamation_pos_x = pos_x - self._txt_exclamation_point.width / 2 + wheel_txt.width / 2 + EXCLAMATION_POINT_SPACING
      exclamation_pos_y = pos_y - self._txt_exclamation_point.height / 2
      rl.draw_texture_ex(self._txt_exclamation_point, rl.Vector2(exclamation_pos_x, exclamation_pos_y), 0.0, 1.0, rl.WHITE)

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
      left_color = colors_alpha(Colors.GREEN, alpha)
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
      right_color = colors_alpha(Colors.GREEN, alpha)
      right_draw = True

    if right_draw:
      rl.draw_rectangle(int(rect.x + rect.width - border_size), int(rect.y), border_size, int(rect.height), right_color)

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX and SET speed indicator boxes on the left side."""
    if not self._can_draw_top_icons or ui_state.status == UIStatus.DISENGAGED:
      return

    max_color = Colors.WHITE_TRANSLUCENT
    speed_color = Colors.WHITE_TRANSLUCENT

    if self.is_cruise_set:
      speed_color = Colors.WHITE
      if ui_state.status in (UIStatus.ENGAGED, UIStatus.ACTIVE):
        max_color = Colors.GREEN
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = Colors.OVERRIDE

    box_size = 64
    box_x = rect.x + 10
    max_y = rect.y + 10

    # Max speed box
    max_speed_box_bg = rl.Rectangle(box_x, max_y, box_size, box_size)
    rl.draw_rectangle_rounded(max_speed_box_bg, 0.2, 10, Colors.BOX_BG)

    max_speed_box = rl.Rectangle(max_speed_box_bg.x + 2, max_speed_box_bg.y + 2, box_size - 4, box_size - 4)
    rl.draw_rectangle_rounded_lines_ex(max_speed_box, 0.2, 10, 1, Colors.WHITE_TRANSLUCENT)

    # MAX text
    self._draw_text(
      max_speed_box.x + max_speed_box.width / 2,
      max_speed_box.y + 18,
      tr("MAX"),
      FontSizes.middle,
      max_color
    )

    # MAX speed value
    max_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.cruise_speed))
    self._draw_text(
      max_speed_box.x + max_speed_box.width / 2,
      max_speed_box.y + 42,
      max_speed_text,
      FontSizes.big,
      speed_color
    )

    # SET speed box with background (only if NDA or stock limit is active)
    if self.nda_state > 0 or self.stock_limit_speed > 0:
      set_y = max_y + box_size + 8
      set_speed_box_bg = rl.Rectangle(box_x, set_y, box_size, box_size)
      rl.draw_rectangle_rounded(set_speed_box_bg, 0.2, 10, Colors.BOX_BG)

      set_speed_box = rl.Rectangle(set_speed_box_bg.x + 2, set_speed_box_bg.y + 2, box_size - 4, box_size - 4)
      rl.draw_rectangle_rounded_lines_ex(set_speed_box, 0.2, 10, 1, Colors.WHITE_TRANSLUCENT)

      # SET text
      self._draw_text(
        set_speed_box.x + set_speed_box.width / 2,
        set_speed_box.y + 18,
        tr("SET"),
        FontSizes.middle,
        max_color
      )

      # SET speed value
      set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.apply_speed))
      self._draw_text(
        set_speed_box.x + set_speed_box.width / 2,
        set_speed_box.y + 42,
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
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FontSizes.speed_unit, 0, Colors.WHITE_TRANSLUCENT)

  def _draw_traffic_light(self, rect: rl.Rectangle) -> None:
    if not self._can_draw_top_icons or ui_state.status == UIStatus.DISENGAGED:
      return

    img_w = 48
    img_h = 96

    img_x = rect.x + rect.width - img_w - 10
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
      rl.Vector2(0, 0), 0, Colors.WHITE
    )
