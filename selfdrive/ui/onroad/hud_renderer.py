import pyray as rl
import math
from dataclasses import dataclass
from datetime import datetime
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.ui.onroad.exp_button import ExpButton
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '—'
BLINKER_DRAW_COUNT = 8
BLINK_PERIOD_MS = 900.0


@dataclass(frozen=True)
class UIConfig:
  header_height: int = 300
  border_size: int = 30
  button_size: int = 192
  set_speed_width_metric: int = 260
  set_speed_width_imperial: int = 172
  set_speed_height: int = 204
  wheel_icon_size: int = 144
  icon_size: int = 144
  small_icon_size: int = 77


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 176
  speed_unit: int = 66
  max_speed: int = 40
  set_speed: int = 90
  info_text: int = 30


@dataclass(frozen=True)
class Colors:
  white: rl.Color = rl.WHITE
  disengaged: rl.Color = rl.Color(145, 155, 149, 255)
  override: rl.Color = rl.Color(145, 155, 149, 255)
  engaged: rl.Color = rl.Color(128, 216, 166, 255)
  disengaged_bg: rl.Color = rl.Color(0, 0, 0, 153)
  override_bg: rl.Color = rl.Color(145, 155, 149, 204)
  engaged_bg: rl.Color = rl.Color(128, 216, 166, 204)
  grey: rl.Color = rl.Color(166, 166, 166, 255)
  dark_grey: rl.Color = rl.Color(114, 114, 114, 255)
  black_translucent: rl.Color = rl.Color(0, 0, 0, 166)
  white_translucent: rl.Color = rl.Color(255, 255, 255, 200)
  border_translucent: rl.Color = rl.Color(255, 255, 255, 75)
  header_gradient_start: rl.Color = rl.Color(0, 0, 0, 114)
  header_gradient_end: rl.Color = rl.BLANK
  red: rl.Color = rl.Color(201, 34, 49, 255)
  lime: rl.Color = rl.Color(120, 255, 120, 255)
  orange: rl.Color = rl.Color(255, 149, 0, 255)
  light_orange: rl.Color = rl.Color(255, 228, 191, 255)


UI_CONFIG = UIConfig()
FONT_SIZES = FontSizes()
COLORS = Colors()


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.params = Params()

    # Basic cruise state
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self.cruise_speed: float = 0.0
    self.apply_speed: float = 0.0
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False

    # Extended state variables
    self.accel: float = 0.0
    self.brake_press: bool = False
    self.gas_press: bool = False
    self.autohold_state: int = 0
    self.left_blinker: bool = False
    self.right_blinker: bool = False
    self.wifi_state: int = 0
    self.gps_bearing: float = 0.0
    self.gps_vertical_accuracy: float = 0.0
    self.gps_altitude: float = 0.0
    self.gps_accuracy: float = 0.0
    self.gps_satellite_count: int = 0
    self.steer_angle: float = 0.0
    self.steer_angle_target: float = 0.0
    self.lat_active: bool = False
    self.lka_state: bool = False
    self.long_control: bool = False

    # TPMS
    self.fl: float = 0.0
    self.fr: float = 0.0
    self.rl: float = 0.0
    self.rr: float = 0.0

    # Speed limits and navigation
    self.nav_limit_speed: float = 0.0
    self.stock_limit_speed: float = 0.0
    self.road_limit_speed: float = 0.0
    self.cam_limit_speed: float = 0.0
    self.cam_limit_speed_left_dist: float = 0.0
    self.section_limit_speed: float = 0.0
    self.section_left_dist: float = 0.0
    self.road_signs: int = 0
    self.nda_state: int = 0
    self.traffic_state: int = 0

    # Steering info
    self.steer_torque: float = 0.0
    self.curvature: float = 0.0
    self.steer_ratio: float = 0.0

    # Blinker animation
    self.blink_index: int = 0
    self.blink_wait: int = 0
    self.prev_blink_time: float = 0.0

    self.hide_bottom_icons: bool = False

    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)

    self._exp_button: ExpButton = ExpButton(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)

    # Load icons
    self._load_icons()

  def _load_icons(self) -> None:
    """Load all icon textures."""
    icon_size = UI_CONFIG.icon_size
    small_icon_size = UI_CONFIG.small_icon_size

    # Main icons
    self.steer_img = gui_app.texture("icons/steer.png", icon_size, icon_size)
    self.gaspress_img = gui_app.texture("icons/disengage_on_accelerator.png", icon_size, icon_size)
    self.brake_img = gui_app.texture("icons/brake_disc.png", icon_size, icon_size)

    # GPS and connectivity
    self.gps_img = gui_app.texture("icons/gps.png", icon_size, icon_size)
    self.direction_img = gui_app.texture("icons/direction.png", icon_size, icon_size)
    self.wifi_l_img = gui_app.texture("icons/wifi_strength_low.png", icon_size, icon_size)
    self.wifi_m_img = gui_app.texture("icons/wifi_strength_medium.png", icon_size, icon_size)
    self.wifi_h_img = gui_app.texture("icons/wifi_strength_high.png", icon_size, icon_size)
    self.wifi_f_img = gui_app.texture("icons/wifi_strength_full.png", icon_size, icon_size)

    # Turn signals
    self.turnsignal_l_img = gui_app.texture("icons/turnsignal_l.png", icon_size, icon_size)
    self.turnsignal_r_img = gui_app.texture("icons/turnsignal_r.png", icon_size, icon_size)

    # Traffic lights
    self.traffic_off_img = gui_app.texture("icons/traffic_off.png", small_icon_size, small_icon_size * 2)
    self.traffic_green_img = gui_app.texture("icons/traffic_green.png", small_icon_size, small_icon_size * 2)
    self.traffic_red_img = gui_app.texture("icons/traffic_red.png", small_icon_size, small_icon_size * 2)

    # LKA
    self.lka_on_img = gui_app.texture("icons/lka_on.png", icon_size, icon_size)
    self.lka_off_img = gui_app.texture("icons/lka_off.png", icon_size, icon_size)

    # Distance settings
    self.dist1_img = gui_app.texture("icons/dist1.png", 100, 250)
    self.dist2_img = gui_app.texture("icons/dist2.png", 100, 250)
    self.dist3_img = gui_app.texture("icons/dist3.png", 100, 250)
    self.dist4_img = gui_app.texture("icons/dist4.png", 100, 250)

    # Autohold
    self.autohold_warning_img = gui_app.texture("icons/autohold_warning.png", icon_size, icon_size)
    self.autohold_active_img = gui_app.texture("icons/autohold_active.png", icon_size, icon_size)

    # Road signs
    self.speed_bump_img = gui_app.texture("icons/speed_bump.png", 150, 150)
    self.school_zone_img = gui_app.texture("icons/school_zone.png", 150, 150)
    self.speed_camera_img = gui_app.texture("icons/speed_camera.png", 150, 150)

    # TPMS
    self.tpms_img = gui_app.texture("icons/tpms.png", 160, 208)

  def _update_state(self) -> None:
    """Update HUD state based on car state and controls state."""
    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      self.is_cruise_set = False
      self.set_speed = SET_SPEED_NA
      self.speed = 0.0
      return

    controls_state = sm['controlsState']
    car_state = sm['carState']
    car_control = sm['carControl']
    car_params = sm['carParams']
    device_state = sm['deviceState']
    gps_location = sm['gpsLocationExternal']
    navi_data = sm['naviData']
    longitudinal_plan = sm['longitudinalPlan']
    live_params = sm['liveParameters']
    selfdriveState = sm['selfdriveState']

    # Cruise speed
    v_cruise_cluster = car_state.vCruiseCluster
    self.cruise_speed = v_cruise_cluster if v_cruise_cluster > 0 else controls_state.vCruiseDEPRECATED
    self.apply_speed = car_state.vCruise
    self.set_speed = self.cruise_speed
    self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
    self.is_cruise_available = self.set_speed != -1

    if self.is_cruise_set and not ui_state.is_metric:
      self.set_speed *= KM_TO_MILE
      self.cruise_speed *= KM_TO_MILE
      self.apply_speed *= KM_TO_MILE

    # Current speed
    v_ego_cluster = car_state.vEgoCluster
    self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed = max(0.0, v_ego * speed_conversion)

    # Extended state
    self.accel = car_state.aEgo
    self.brake_press = car_state.brakeLights
    self.gas_press = car_state.gasPressed
    self.left_blinker = car_state.leftBlinker
    self.right_blinker = car_state.rightBlinker
    self.steer_angle = car_state.steeringAngleDeg

    # Get ex_state if available
    if hasattr(car_state, 'exState'):
      ex_state = car_state.exState
      self.autohold_state = ex_state.autoHold if hasattr(ex_state, 'autoHold') else 0
      if hasattr(ex_state, 'tpms'):
        tpms = ex_state.tpms
        self.fl = tpms.fl
        self.fr = tpms.fr
        self.rl = tpms.rl
        self.rr = tpms.rr
      self.nav_limit_speed = ex_state.navLimitSpeed if hasattr(ex_state, 'navLimitSpeed') else 0
      self.road_signs = ex_state.roadSigns if hasattr(ex_state, 'roadSigns') else 0

    self.stock_limit_speed = car_state.speedLimit if hasattr(car_state, 'speedLimit') else 0

    if device_state:
      self.wifi_state = device_state.networkStrength

    if gps_location:
      self.gps_bearing = gps_location.bearingDeg
      self.gps_vertical_accuracy = gps_location.verticalAccuracy
      self.gps_altitude = gps_location.altitude
      self.gps_accuracy = gps_location.horizontalAccuracy
      self.gps_satellite_count = ui_state.satelliteCount

    if car_control:
      self.lat_active = car_control.latActive
      if hasattr(car_control, 'actuators'):
        self.steer_angle_target = car_control.actuators.steeringAngleDeg
        self.curvature = car_control.actuators.curvature if hasattr(car_control.actuators, 'curvature') else 0

    if car_params:
      self.long_control = car_params.openpilotLongitudinalControl

    if hasattr(car_state, 'cruiseState'):
      self.lka_state = car_state.cruiseState.available

    self.steer_torque = car_state.steeringTorque if hasattr(car_state, 'steeringTorque') else 0

    if navi_data:
      self.nda_state = navi_data.active if hasattr(navi_data, 'active') else 0
      self.road_limit_speed = navi_data.roadLimitSpeed if hasattr(navi_data, 'roadLimitSpeed') else 0
      self.cam_limit_speed = navi_data.camLimitSpeed if hasattr(navi_data, 'camLimitSpeed') else 0
      self.cam_limit_speed_left_dist = navi_data.camLimitSpeedLeftDist if hasattr(navi_data, 'camLimitSpeedLeftDist') else 0
      self.section_limit_speed = navi_data.sectionLimitSpeed if hasattr(navi_data, 'sectionLimitSpeed') else 0
      self.section_left_dist = navi_data.sectionLeftDist if hasattr(navi_data, 'sectionLeftDist') else 0

    if longitudinal_plan:
      self.traffic_state = longitudinal_plan.trafficState if hasattr(longitudinal_plan, 'trafficState') else 0

    if live_params:
      self.steer_ratio = live_params.steerRatio if hasattr(live_params, 'steerRatio') else 0

    if selfdriveState:
      alert_size = selfdriveState.alertSize if hasattr(selfdriveState, 'alertSize') else 0
      self.hide_bottom_icons = alert_size != 0

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""
    # Draw the header background
    rl.draw_rectangle_gradient_v(
      int(rect.x),
      int(rect.y),
      int(rect.width),
      UI_CONFIG.header_height,
      COLORS.header_gradient_start,
      COLORS.header_gradient_end,
    )

    if self.is_cruise_available:
      self._draw_set_speed(rect)

    self._draw_current_speed(rect)
    self._draw_upper_left_info(rect)
    self._draw_upper_right_info(rect)
    self._draw_upper_icons(rect)
    self._draw_road_signs(rect)

    if not self.hide_bottom_icons:
      self._draw_bottom_info(rect)
      self._draw_bottom_icons(rect)

    self._draw_blinkers(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))

  def user_interacting(self) -> bool:
    return self._exp_button.is_pressed

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX speed indicator box."""
    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45

    set_speed_rect = rl.Rectangle(x, y, set_speed_width, UI_CONFIG.set_speed_height)
    rl.draw_rectangle_rounded(set_speed_rect, 0.35, 10, COLORS.black_translucent)
    rl.draw_rectangle_rounded_lines_ex(set_speed_rect, 0.35, 10, 6, COLORS.border_translucent)

    # Determine speed color
    limit_speed = self._get_current_limit_speed()
    max_color = COLORS.grey
    set_speed_color = COLORS.dark_grey
    if self.is_cruise_set:
      set_speed_color = COLORS.white
      if ui_state.status == UIStatus.ENGAGED:
        max_color = COLORS.engaged
      elif ui_state.status == UIStatus.DISENGAGED:
        max_color = COLORS.disengaged
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = COLORS.override

      # Color based on speed limit
      if limit_speed > 0 and ui_state.status != UIStatus.DISENGAGED:
        if self.cruise_speed > limit_speed + 25:
          set_speed_color = COLORS.red
        elif self.cruise_speed > limit_speed + 15:
          set_speed_color = COLORS.orange
        elif self.cruise_speed > limit_speed + 5:
          set_speed_color = rl.Color(255, 200, 100, 255)

    # MAX text
    max_text = tr("MAX")
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, FONT_SIZES.max_speed).x
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + 27),
      FONT_SIZES.max_speed,
      0,
      max_color,
    )

    # Speed value
    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.cruise_speed))
    speed_text_width = measure_text_cached(self._font_bold, set_speed_text, FONT_SIZES.set_speed).x
    rl.draw_text_ex(
      self._font_bold,
      set_speed_text,
      rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y + 77),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

    # Draw speed limit sign
    if limit_speed > 0:
      self._draw_speed_limit_sign(rect, limit_speed)

  def _get_current_limit_speed(self) -> float:
    """Get current applicable speed limit."""
    if self.nda_state > 0:
      if self.cam_limit_speed > 0 and self.cam_limit_speed_left_dist > 0:
        return self.cam_limit_speed
      elif self.section_limit_speed > 0 and self.section_left_dist > 0:
        return self.section_limit_speed
      else:
        return self.road_limit_speed
    elif self.stock_limit_speed > 0:
      return self.stock_limit_speed
    elif self.nav_limit_speed > 0:
      return self.nav_limit_speed
    return 0

  def _draw_speed_limit_sign(self, rect: rl.Rectangle, limit_speed: float) -> None:
    """Draw speed limit sign with distance."""
    center_x = rect.x + 220
    center_y = rect.y + 125

    # Draw circles for sign
    rl.draw_circle(int(center_x), int(center_y), 36, COLORS.white)
    rl.draw_circle(int(center_x), int(center_y), 35, COLORS.red)
    rl.draw_circle(int(center_x), int(center_y), 27, COLORS.white)

    # Draw speed number
    speed_text = str(int(limit_speed))
    speed_text_width = measure_text_cached(self._font_bold, speed_text, 50).x
    rl.draw_text_ex(
      self._font_bold,
      speed_text,
      rl.Vector2(center_x - speed_text_width / 2, center_y - 20),
      50,
      0,
      rl.BLACK,
    )

    # Draw distance if available
    left_dist = 0
    if self.cam_limit_speed > 0 and self.cam_limit_speed_left_dist > 0:
      left_dist = self.cam_limit_speed_left_dist
    elif self.section_limit_speed > 0 and self.section_left_dist > 0:
      left_dist = self.section_left_dist

    if left_dist > 0:
      if left_dist >= 1000:
        dist_text = f"{left_dist/1000:.1f} km"
      else:
        dist_text = f"{int(left_dist)} m"

      dist_width = measure_text_cached(self._font_medium, dist_text, 30).x
      rl.draw_text_ex(
        self._font_medium,
        dist_text,
        rl.Vector2(center_x - dist_width / 2, center_y + 45),
        30,
        0,
        COLORS.white_translucent,
      )

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Draw the current vehicle speed and unit with color based on acceleration."""
    # Color based on accel/decel
    if self.accel > 0:
      # Accelerating - green
      alpha = int(255 - (180 * min(self.accel / 3.0, 1.0)))
      alpha = max(80, min(255, alpha))
      speed_color = rl.Color(alpha, 255, alpha, 200)
    else:
      # Decelerating - red
      alpha = int(255 - (255 * min(abs(self.accel) / 4.0, 1.0)))
      alpha = max(60, min(255, alpha))
      speed_color = rl.Color(255, alpha, alpha, 200)

    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(rect.x + rect.width / 2 - speed_text_size.x / 2, 180 - speed_text_size.y / 2)
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, speed_color)

    unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, 290 - unit_text_size.y / 2)
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.light_orange)

  def _draw_upper_left_info(self, rect: rl.Rectangle) -> None:
    """Draw upper left information (car name, settings)."""
    x = rect.x + 20
    y = rect.y + UI_CONFIG.border_size + 20

    # Car name
    car_name = self.params.get("CarName") or "Unknown"
    self._draw_text(x, y, car_name, FONT_SIZES.info_text, COLORS.white_translucent)

    # Settings badges
    x = rect.x + 400
    badges = []

    if self.nda_state > 0:
      badges.append("NDA")
    elif self.stock_limit_speed > 0:
      badges.append("Stock")

    if self.params.get_bool("CameraSccEnable"):
      badges.append("CamScc")

    if self.params.get_bool("IsHda2"):
      badges.append("HDA2")

    if self.params.get_bool("AlphaLongitudinalEnabled"):
      badges.append("Long")

    if self.params.get_bool("RadarTrackEnable"):
      badges.append("Radar")

    for badge in badges:
      badge_width = measure_text_cached(self._font_medium, badge, FONT_SIZES.info_text).x
      # Draw badge background
      rl.draw_rectangle_rounded(
        rl.Rectangle(x - 10, y - 10, badge_width + 20, 40),
        0.5, 10, COLORS.black_translucent
      )
      self._draw_text(x, y + 10, badge, FONT_SIZES.info_text, COLORS.lime)
      x += badge_width + 30

  def _draw_upper_right_info(self, rect: rl.Rectangle) -> None:
    """Draw upper right GPS information."""
    x = rect.x + rect.width - 20
    y = rect.y + UI_CONFIG.border_size + 20

    # GPS info
    if self.gps_satellite_count == 0:
      gps_text = "🛰️ No GPS Signal"
    else:
      alt_str = "--" if self.gps_vertical_accuracy == 0 or self.gps_vertical_accuracy > 100 else f"{self.gps_altitude:.1f} m"
      acc_str = "--" if self.gps_accuracy == 0 or self.gps_accuracy > 100 else f"{self.gps_accuracy:.1f} m"
      gps_text = f"🛰️ Alt({alt_str}) Acc({acc_str}) Sat({self.gps_satellite_count})"

    text_width = measure_text_cached(self._font_medium, gps_text, FONT_SIZES.info_text).x
    self._draw_text(x - text_width, y + 10, gps_text, FONT_SIZES.info_text, COLORS.white_translucent)

  def _draw_upper_icons(self, rect: rl.Rectangle) -> None:
    """Draw upper right icons (direction, GPS, WiFi)."""
    icon_size = UI_CONFIG.icon_size
    icon_bg = COLORS.black_translucent

    # Direction icon
    x = rect.x + rect.width - (icon_size / 2) - (UI_CONFIG.border_size * 2) - (icon_size * 3)
    y = rect.y + (icon_size / 2) + (UI_CONFIG.border_size * 2)
    opacity = 0.8 if self.gps_satellite_count > 0 else 0.2
    self._draw_icon_rotated(x, y, self.direction_img, icon_bg, opacity, self.gps_bearing)

    # GPS icon
    x = rect.x + rect.width - (icon_size / 2) - (UI_CONFIG.border_size * 2) - (icon_size * 2)
    self._draw_icon(x, y, self.gps_img, icon_bg, opacity)

    # WiFi icon
    wifi_img = self.wifi_f_img
    if self.wifi_state == 1:
      wifi_img = self.wifi_l_img
    elif self.wifi_state == 2:
      wifi_img = self.wifi_m_img
    elif self.wifi_state == 3:
      wifi_img = self.wifi_h_img

    x = rect.x + rect.width - (icon_size / 2) - (UI_CONFIG.border_size * 2) - (icon_size * 1)
    wifi_opacity = 0.8 if self.wifi_state > 0 else 0.2
    self._draw_icon(x, y, wifi_img, icon_bg, wifi_opacity)

  def _draw_road_signs(self, rect: rl.Rectangle) -> None:
    """Draw road signs (traffic light, school zone, speed camera)."""
    # Traffic light
    x = rect.x + 205
    y = rect.y + (UI_CONFIG.border_size * 2.6)

    if self.traffic_state == 1:
      rl.draw_texture_pro(
        self.traffic_red_img,
        rl.Rectangle(0, 0, 77, 154),
        rl.Rectangle(x, y, 77, 154),
        rl.Vector2(0, 0), 0, rl.WHITE
      )
    elif self.traffic_state == 2:
      rl.draw_texture_pro(
        self.traffic_green_img,
        rl.Rectangle(0, 0, 77, 154),
        rl.Rectangle(x, y, 77, 154),
        rl.Vector2(0, 0), 0, rl.WHITE
      )
    else:
      rl.draw_texture_pro(
        self.traffic_off_img,
        rl.Rectangle(0, 0, 77, 154),
        rl.Rectangle(x, y, 77, 154),
        rl.Vector2(0, 0), 0, rl.WHITE
      )

    # Road signs (school zone, speed camera)
    x = rect.x + 440
    y = rect.y + (UI_CONFIG.border_size * 3.5)

    if self.road_signs == 1:
      rl.draw_texture_pro(
        self.school_zone_img,
        rl.Rectangle(0, 0, 150, 150),
        rl.Rectangle(x, y, 150, 150),
        rl.Vector2(0, 0), 0, rl.WHITE
      )
    elif self.cam_limit_speed > 0 and self.cam_limit_speed_left_dist > 0:
      rl.draw_texture_pro(
        self.speed_camera_img,
        rl.Rectangle(0, 0, 150, 150),
        rl.Rectangle(x, y, 150, 150),
        rl.Vector2(0, 0), 0, rl.WHITE
      )

  def _draw_bottom_info(self, rect: rl.Rectangle) -> None:
    """Draw bottom information (steering, TPMS, etc)."""
    # Bottom left - date
    x = rect.x + 20
    y = rect.y + rect.height - 20
    date_str = datetime.now().strftime("%Y-%m-%d")
    self._draw_text(x, y, date_str, FONT_SIZES.info_text, COLORS.white_translucent)

    # Bottom left - steering info
    x = rect.x + 400
    steer_info = f"SteerRatio({self.steer_ratio:.1f}) Torque({abs(self.steer_torque):.1f}) Curvature({abs(self.curvature):.3f})"
    self._draw_text(x, y, steer_info, FONT_SIZES.info_text, COLORS.white_translucent)

    # Bottom right - version info
    x = rect.x + rect.width - 20
    version = self.params.get("UpdaterCurrentDescription") or ""
    text_width = measure_text_cached(self._font_medium, version, FONT_SIZES.info_text).x
    self._draw_text(x - text_width, y, version, FONT_SIZES.info_text, COLORS.white_translucent)

    # Draw TPMS if available
    if self.fl > 0 or self.fr > 0 or self.rl > 0 or self.rr > 0:
      self._draw_tpms(rect)

  def _draw_bottom_icons(self, rect: rl.Rectangle) -> None:
    """Draw bottom icons (steering, LKA, gas, brake, distance, TPMS)."""
    icon_size = UI_CONFIG.icon_size
    icon_bg = COLORS.black_translucent
    y = rect.y + rect.height - (UI_CONFIG.border_size * 3) - icon_size / 2

    # Steering angle icon with gradient
    x = rect.x + (icon_size / 2) + (UI_CONFIG.border_size * 1.5) + icon_size
    steer_opacity = 0.8
    self._draw_icon_rotated(x, y, self.steer_img, icon_bg, steer_opacity, self.steer_angle)

    # Steering angle text
    sa_color = self._get_color_for_angle(self.steer_angle)
    sat_color = self._get_color_for_angle(self.steer_angle_target)
    sa_str = f"R {abs(self.steer_angle):.1f} °"
    sat_str = f"T {abs(self.steer_angle_target):.1f} °"

    self._draw_text(x, y + icon_size / 2 + 20, sa_str, FONT_SIZES.info_text, sa_color, "C")
    self._draw_text(x, y + icon_size / 2 + 50, sat_str, FONT_SIZES.info_text, sat_color, "C")

    # LKA icon
    x = rect.x + (icon_size / 2) + (UI_CONFIG.border_size * 1.5) + (icon_size * 2)
    lka_img = self.lka_on_img if self.lat_active else self.lka_off_img
    lka_opacity = 0.8 if self.lka_state else 0.2
    self._draw_icon(x, y, lka_img, icon_bg, lka_opacity)

    # Gas press icon
    x = rect.x + rect.width - (icon_size / 2) - (UI_CONFIG.border_size * 2) - (icon_size * 2)
    gas_opacity = 0.8 if self.gas_press else 0.2
    self._draw_icon(x, y, self.gaspress_img, icon_bg, gas_opacity)

    # Brake/Autohold icon
    x = rect.x + rect.width - (icon_size / 2) - (UI_CONFIG.border_size * 2) - (icon_size * 1)
    if self.autohold_state >= 1:
      autohold_img = self.autohold_warning_img if self.autohold_state > 1 else self.autohold_active_img
      self._draw_icon(x, y, autohold_img, icon_bg, 0.8)
    else:
      brake_opacity = 0.8 if self.brake_press else 0.2
      self._draw_icon(x, y, self.brake_img, icon_bg, brake_opacity)

    # Distance setting
    personality = self.params.get("LongitudinalPersonality") or "1"
    dist_imgs = [self.dist1_img, self.dist2_img, self.dist3_img, self.dist4_img]
    dist_idx = min(int(personality), 3)

    x = rect.x + rect.width - (UI_CONFIG.border_size * 2) - 100 * 1.3
    y = rect.y + rect.height - (UI_CONFIG.border_size * 2) - 250 * 1.8

    rl.draw_texture_pro(
      dist_imgs[dist_idx],
      rl.Rectangle(0, 0, 100, 250),
      rl.Rectangle(x, y, 100, 250),
      rl.Vector2(0, 0), 0, rl.Color(255, 255, 255, 204)
    )

  def _draw_tpms(self, rect: rl.Rectangle) -> None:
    """Draw TPMS pressure display."""
    x = rect.x + rect.width - 180
    y = rect.y + rect.height - 230

    # Draw TPMS background image
    rl.draw_texture_pro(
      self.tpms_img,
      rl.Rectangle(0, 0, 160, 208),
      rl.Rectangle(x, y, 160, 208),
      rl.Vector2(0, 0), 0, rl.WHITE
    )

    # Draw pressure values
    def get_tpms_color(pressure):
      if pressure < 5 or pressure > 60:
        return rl.BLACK
      if pressure < 31:
        return COLORS.red
      return COLORS.white

    def get_tpms_text(pressure):
      if pressure < 5 or pressure > 60:
        return "—"
      return str(round(pressure))

    # FL
    self._draw_text(x + 25, y + 56, get_tpms_text(self.fl), FONT_SIZES.info_text, get_tpms_color(self.fl), "C")
    # FR
    self._draw_text(x + 133, y + 56, get_tpms_text(self.fr), FONT_SIZES.info_text, get_tpms_color(self.fr), "C")
    # RL
    self._draw_text(x + 25, y + 171, get_tpms_text(self.rl), FONT_SIZES.info_text, get_tpms_color(self.rl), "C")
    # RR
    self._draw_text(x + 133, y + 171, get_tpms_text(self.rr), FONT_SIZES.info_text, get_tpms_color(self.rr), "C")

  def _draw_icon(self, x: float, y: float, texture: rl.Texture, bg_color: rl.Color,
                 opacity: float = 1.0) -> None:
    """Draw an icon with background circle."""
    icon_size = UI_CONFIG.icon_size

    # Draw background circle
    rl.draw_circle(int(x), int(y), icon_size / 2, bg_color)

    # Draw icon with opacity
    color = rl.Color(255, 255, 255, int(opacity * 255))
    rl.draw_texture_pro(
      texture,
      rl.Rectangle(0, 0, texture.width, texture.height),
      rl.Rectangle(x - icon_size / 2, y - icon_size / 2, icon_size, icon_size),
      rl.Vector2(0, 0), 0, color
    )

  def _draw_icon_rotated(self, x: float, y: float, texture: rl.Texture,
                         bg_color: rl.Color, opacity: float, rotation: float) -> None:
    """Draw a rotated icon with background circle."""
    icon_size = UI_CONFIG.icon_size

    # Draw background circle
    rl.draw_circle(int(x), int(y), icon_size / 2, bg_color)

    # Draw rotated icon with opacity
    color = rl.Color(255, 255, 255, int(opacity * 255))
    rl.draw_texture_pro(
      texture,
      rl.Rectangle(0, 0, texture.width, texture.height),
      rl.Rectangle(x, y, icon_size, icon_size),
      rl.Vector2(icon_size / 2, icon_size / 2), rotation, color
    )

  def _get_color_for_angle(self, angle: float) -> rl.Color:
    """Get color based on steering angle magnitude."""
    abs_angle = abs(angle)
    if abs_angle > 360:
      return rl.Color(139, 0, 0, 200)  # Dark red
    elif abs_angle > 240:
      return COLORS.red
    elif abs_angle > 120:
      return COLORS.orange
    return COLORS.lime

  def _draw_blinkers(self, rect: rl.Rectangle) -> None:
    """Draw turn signal animation."""
    if self.blink_wait > 0:
      self.blink_wait -= 1
      self.blink_index = 0
      return

    if not (self.left_blinker or self.right_blinker):
      self.blink_index = 0
      return

    # Update blink animation
    current_time = rl.get_time() * 1000  # Convert to ms
    if current_time - self.prev_blink_time > BLINK_PERIOD_MS / 60:
      self.prev_blink_time = current_time
      self.blink_index += 1

    if self.blink_index >= BLINKER_DRAW_COUNT:
      self.blink_index = BLINKER_DRAW_COUNT - 1
      self.blink_wait = 15

    # Draw blinker images
    center_x = rect.width / 2
    y = (rect.height - 200) / 2
    direction = -1 if self.left_blinker else 1
    x = center_x - 200 if self.left_blinker else center_x

    blinker_width = 200
    blinker_height = 200
    alpha_base = 0.8

    blinker_img = self.turnsignal_l_img if self.left_blinker else self.turnsignal_r_img

    for i in range(BLINKER_DRAW_COUNT):
      distance = abs(self.blink_index - i)
      alpha = alpha_base if distance == 0 else alpha_base / (distance * 2)

      if alpha > 0.05:
        x_pos = x + int(i * blinker_width * 0.6 * direction)
        color = rl.Color(255, 255, 255, int(alpha * 255))

        rl.draw_texture_pro(
          blinker_img,
          rl.Rectangle(0, 0, blinker_img.width, blinker_img.height),
          rl.Rectangle(x_pos, y, blinker_width, blinker_height),
          rl.Vector2(0, 0), 0, color
        ) #Draw arrow triangle
        arrow_color = rl.Color(255, 200, 0, int(alpha * 255))
        self._draw_arrow(x + int(i * blinker_width * 0.6 * direction),
                        int(y + blinker_height / 2),
                        direction > 0, arrow_color)

  def _draw_arrow(self, x: int, y: int, pointing_right: bool, color: rl.Color) -> None:
    """Draw a directional arrow for blinker (deprecated - using images instead)."""
    pass

  def _draw_text(self, x: float, y: float, text: str, font_size: int,
                 color: rl.Color, alignment: str = "L") -> None:
    """Helper to draw text with alignment."""
    text_size = measure_text_cached(self._font_medium, text, font_size)

    if alignment == "R":
      x = x - text_size.x
    elif alignment == "C":
      x = x - text_size.x / 2

    rl.draw_text_ex(
      self._font_medium,
      text,
      rl.Vector2(x, y - text_size.y / 2),
      font_size,
      0,
      color
    )
