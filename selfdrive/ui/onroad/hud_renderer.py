import pyray as rl
import math
from dataclasses import dataclass
from datetime import datetime
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.ui.onroad.exp_button import ExpButton
from openpilot.selfdrive.ui.onroad.icon_button import IconButton, RotatableIconButton, ToggleIconButton, IconGroup
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = "--"
BLINKER_DRAW_COUNT = 8
BLINK_PERIOD_MS = 900.0


@dataclass(frozen=True)
class UIConfig:
  header_height: int = 300
  border_size: int = 30
  button_size: int = 192 * 0.8
  #set_speed_width_metric: int = 200
  #set_speed_width_imperial: int = 172
  #set_speed_height: int = 204
  icon_size: int = 144 * 0.8


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 176 + 24
  speed_unit: int = 66
  middle: int = 40
  big: int = 70
  info_text: int = 30


def colors_alpha(color, alpha):
  if isinstance(color, tuple):
    return rl.Color(color[0], color[1], color[2], alpha)
  else:
    return rl.Color(color.r, color.g, color.b, alpha)


@dataclass(frozen=True)
class Colors:
  WHITE = rl.Color(255, 255, 255, 255) # rl.WHITE
  GREY = rl.Color(166, 166, 166, 255)
  BLACK = rl.Color(0, 0, 0, 255) # rl.BLACK
  RED = rl.Color(201, 34, 49, 255)
  ORANGE = rl.Color(255, 149, 0, 255)
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = DISENGAGED
  ENGAGED = rl.Color(128, 216, 166, 255)
  DISENGAGED_BG = colors_alpha(BLACK, 153)
  OVERRIDE_BG = colors_alpha(OVERRIDE, 204)
  ENGAGED_BG = colors_alpha(ENGAGED, 204)
  BLACK_TRANSLUCENT = colors_alpha(BLACK, 166)
  WHITE_TRANSLUCENT = colors_alpha(WHITE, 200)
  BORDER_TRANSLUCENT = colors_alpha(WHITE, 75)
  HEADER_GRADIENT_START = colors_alpha(BLACK, 114)
  HEADER_GRADIENT_END = rl.Color(0, 0, 0, 0) # rl.BLANK
  DARK_GREY = rl.Color(114, 114, 114, 255)
  DARK_RED = rl.Color(139, 0, 0, 255)
  LIME = rl.Color(120, 255, 120, 255)
  LIGHT_ORANGE = rl.Color(255, 228, 191, 255)
  AMBER = rl.Color(255, 200, 100, 255)


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
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
    self.left_blind_spot: bool = False
    self.right_blind_spot: bool = False
    self.wifi_state: int = 0
    self.gps_bearing: float = 0.0
    self.gps_vertical_accuracy: float = 0.0
    self.gps_altitude: float = 0.0
    self.gps_horizontal_accuracy: float = 0.0
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

    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)
    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_extra_bold: rl.Font = gui_app.font(FontWeight.EXTRA_BOLD)

    self._exp_button: ExpButton = ExpButton(UIConfig.button_size, UIConfig.icon_size)

    # Initialize icon buttons
    self._init_icon_buttons()

    # Load static icons (non-interactive)
    self._load_static_icons()

  def _init_icon_buttons(self) -> None:
    icon_size = UIConfig.icon_size
    button_size = UIConfig.button_size
    bg_color = Colors.BLACK_TRANSLUCENT

    # Upper right icon group
    self.upper_icons = IconGroup()

    # Direction button (rotatable)
    self.direction_btn = RotatableIconButton("icons/direction.png", button_size, icon_size, bg_color)
    self.upper_icons.add_button(self.direction_btn)

    # GPS button
    self.gps_btn = IconButton("icons/gps.png", button_size, icon_size, bg_color)
    self.upper_icons.add_button(self.gps_btn)

    # WiFi button (will change texture based on state)
    self.wifi_btn = IconButton("icons/wifi_strength_full.png", button_size, icon_size, bg_color)
    self.upper_icons.add_button(self.wifi_btn)

    # Bottom icon group
    self.bottom_icons = IconGroup()

    # Steering wheel button (rotatable)
    self.steer_btn = RotatableIconButton("icons/steer.png", button_size, icon_size, bg_color)
    self.bottom_icons.add_button(self.steer_btn)

    # LKA toggle button
    self.lka_btn = ToggleIconButton("icons/lka_on.png", "icons/lka_off.png", button_size, icon_size, bg_color)
    self.bottom_icons.add_button(self.lka_btn)

    # Gas press button
    self.gas_btn = IconButton("icons/disengage_on_accelerator.png", button_size, icon_size, bg_color)
    self.bottom_icons.add_button(self.gas_btn)

    # Brake/Autohold button
    self.brake_btn = IconButton("icons/brake_disc.png", button_size, icon_size, bg_color)
    self.autohold_warning_btn = IconButton("icons/autohold_warning.png", button_size, icon_size, bg_color)
    self.autohold_active_btn = IconButton("icons/autohold_active.png", button_size, icon_size, bg_color)

  def _load_static_icons(self) -> None:
    icon_size = UIConfig.icon_size

    # WiFi textures for state switching
    self.wifi_l_img = gui_app.texture("icons/wifi_strength_low.png", icon_size, icon_size)
    self.wifi_m_img = gui_app.texture("icons/wifi_strength_medium.png", icon_size, icon_size)
    self.wifi_h_img = gui_app.texture("icons/wifi_strength_high.png", icon_size, icon_size)
    self.wifi_f_img = gui_app.texture("icons/wifi_strength_full.png", icon_size, icon_size)

    # Turn signals
    self.turnsignal_l_img = gui_app.texture("icons/turnsignal_l.png", icon_size, icon_size)
    self.turnsignal_r_img = gui_app.texture("icons/turnsignal_r.png", icon_size, icon_size)

    # Traffic lights
    self.traffic_off_img = gui_app.texture("icons/traffic_off.png", 77, 154)
    self.traffic_green_img = gui_app.texture("icons/traffic_green.png", 77, 154)
    self.traffic_red_img = gui_app.texture("icons/traffic_red.png", 77, 154)

    # Distance settings
    self.dist1_img = gui_app.texture("icons/dist1.png", 100, 250)
    self.dist2_img = gui_app.texture("icons/dist2.png", 100, 250)
    self.dist3_img = gui_app.texture("icons/dist3.png", 100, 250)
    self.dist4_img = gui_app.texture("icons/dist4.png", 100, 250)

    # Road signs
    self.speed_bump_img = gui_app.texture("icons/speed_bump.png", 150, 150)
    self.school_zone_img = gui_app.texture("icons/school_zone.png", 150, 150)
    self.speed_camera_img = gui_app.texture("icons/speed_camera.png", 150, 150)

    # TPMS
    self.tpms_img = gui_app.texture("icons/tpms.png", 160, 208)

    # blind spot detect
    self.blind_spot_left_img = gui_app.texture("icons/blind_spot_left.png", 184, 200)
    self.blind_spot_right_img = gui_app.texture("icons/blind_spot_right.png", 184, 200)

  def _update_state(self) -> None:
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
    selfdrive_state = sm['selfdriveState']

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
    self.left_blind_spot = car_state.leftBlindspot
    self.right_blind_spot = car_state.rightBlindspot

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
      self.gps_horizontal_accuracy = gps_location.horizontalAccuracy
      self.gps_satellite_count = gps_location.satelliteCount

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

    if selfdrive_state:
      alert_size = selfdrive_state.alertSize if hasattr(selfdrive_state, 'alertSize') else 0
      self.hide_bottom_icons = alert_size != 0

    # Update icon button states
    self._update_icon_button_states()

  def _update_icon_button_states(self) -> None:
    # Upper icons
    self.direction_btn.set_rotation(self.gps_bearing)
    self.direction_btn.set_opacity(0.8 if self.gps_satellite_count > 0 else 0.2)

    self.gps_btn.set_opacity(0.8 if self.gps_satellite_count > 0 else 0.2)

    # WiFi button - update texture based on state
    self.wifi_btn._texture = self._get_wifi_texture()
    self.wifi_btn.set_opacity(0.8 if self.wifi_state > 0 else 0.2)

    # Bottom icons
    self.steer_btn.set_rotation(-self.steer_angle)
    self.steer_btn.set_opacity(0.8)

    self.lka_btn.set_active(self.lat_active)
    self.lka_btn.set_opacity(0.8 if self.lka_state else 0.2)

    self.gas_btn.set_opacity(0.8 if self.gas_press else 0.2)

    self.brake_btn.set_opacity(0.8 if self.brake_press else 0.2)
    self.autohold_warning_btn.set_opacity(0.8)
    self.autohold_active_btn.set_opacity(0.8)

  def _get_wifi_texture(self) -> rl.Texture:
    if self.wifi_state == 1:
      return self.wifi_l_img
    elif self.wifi_state == 2:
      return self.wifi_m_img
    elif self.wifi_state == 3:
      return self.wifi_h_img
    return self.wifi_f_img

  def _render(self, rect: rl.Rectangle) -> None:
    # Draw the header background
    rl.draw_rectangle_gradient_v(
      int(rect.x),
      int(rect.y),
      int(rect.width),
      UIConfig.header_height,
      Colors.HEADER_GRADIENT_START,
      Colors.HEADER_GRADIENT_END,
    )

    if self.is_cruise_available:
      self._draw_set_speed(rect)

    self._draw_current_speed(rect)
    self._draw_upper_info(rect)
    self._draw_upper_icons(rect)

    if not self.hide_bottom_icons:
      self._draw_bottom_info(rect)
      self._draw_bottom_icons(rect)
      self._draw_tpms_distance(rect)

    self._draw_blinkers(rect)
    self._draw_blind_spot_detect(rect)

    button_x = rect.x + rect.width - UIConfig.border_size - UIConfig.button_size + 10
    button_y = rect.y + UIConfig.border_size + 10
    self._exp_button.render(rl.Rectangle(button_x, button_y, UIConfig.button_size, UIConfig.button_size))

  def user_interacting(self) -> bool:
    return self._exp_button.is_pressed or self.upper_icons.is_any_pressed() or self.bottom_icons.is_any_pressed()

  def _draw_upper_icons(self, rect: rl.Rectangle) -> None:
    icon_size = UIConfig.icon_size
    start_x = rect.x + rect.width - (icon_size * 4.85)
    y = rect.y + (icon_size / 2) + (UIConfig.border_size * 2)

    self.upper_icons.render_horizontal(start_x, y, UIConfig.button_size - UIConfig.icon_size, from_right=False)

  def _draw_bottom_icons(self, rect: rl.Rectangle) -> None:
    icon_size = UIConfig.icon_size
    y = rect.y + rect.height - (UIConfig.border_size * 3) - icon_size / 2

    # Left side icons (steering, LKA)
    start_x = rect.x + (icon_size * 2.2)

    # Render steering button first
    self.steer_btn.set_rect(rl.Rectangle(
      start_x - icon_size / 2,
      y - icon_size / 2 + 20,
      icon_size,
      icon_size
    ))
    self.steer_btn.render(self.steer_btn._rect)

    # Draw steering gradient border on top
    self._draw_steer_gradient_border(start_x, y + 20, icon_size, self.steer_angle)

    # Draw steering angle text
    sa_color = self._get_color_for_angle(self.steer_angle)
    sat_color = self._get_color_for_angle(self.steer_angle_target)
    sa_str = f"R {abs(self.steer_angle):.1f} °"
    sat_str = f"T {abs(self.steer_angle_target):.1f} °"
    self._draw_text(start_x, y + icon_size / 2 + 50, sa_str, FontSizes.info_text, sa_color)
    self._draw_text(start_x, y + icon_size / 2 + 75, sat_str, FontSizes.info_text, sat_color)

    # LKA button
    lka_x = start_x + UIConfig.button_size
    self.lka_btn.set_rect(rl.Rectangle(
      lka_x - icon_size / 2,
      y - icon_size / 2 + 20,
      icon_size,
      icon_size
    ))
    self.lka_btn.render(self.lka_btn._rect)

    # Right side icons (gas, brake/autohold)
    gas_x = rect.x + rect.width - (icon_size * 3.7)

    self.gas_btn.set_rect(rl.Rectangle(
      gas_x - icon_size / 2,
      y - icon_size / 2,
      icon_size,
      icon_size
    ))
    self.gas_btn.render(self.gas_btn._rect)

    # Brake/Autohold
    brake_x = gas_x + UIConfig.button_size

    if self.autohold_state >= 1:
      autohold_btn = self.autohold_warning_btn if self.autohold_state > 1 else self.autohold_active_btn
      autohold_btn.set_rect(rl.Rectangle(
        brake_x - icon_size / 2,
        y - icon_size / 2,
        icon_size,
        icon_size
      ))
      autohold_btn.render(autohold_btn._rect)
    else:
      self.brake_btn.set_rect(rl.Rectangle(
        brake_x - icon_size / 2,
        y - icon_size / 2,
        icon_size,
        icon_size
      ))
      self.brake_btn.render(self.brake_btn._rect)

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    # Determine speed color
    limit_speed = self._get_current_limit_speed()
    max_color = Colors.WHITE_TRANSLUCENT
    speed_color = Colors.WHITE_TRANSLUCENT

    if self.is_cruise_set:
      speed_color = Colors.WHITE
      if ui_state.status == UIStatus.ENGAGED:
        max_color = Colors.ENGAGED
      elif ui_state.status == UIStatus.DISENGAGED:
        max_color = Colors.DISENGAGED
      elif ui_state.status == UIStatus.OVERRIDE:
        max_color = Colors.OVERRIDE

      # Color based on speed limit
      if limit_speed > 0 and ui_state.status != UIStatus.DISENGAGED and ui_state.status != UIStatus.OVERRIDE:
        if self.cruise_speed > limit_speed + 25:
          speed_color = Colors.RED
        elif self.cruise_speed > limit_speed + 15:
          speed_color = Colors.ORANGE
        elif self.cruise_speed > limit_speed + 5:
          speed_color = Colors.AMBER

    # Max speed box with background
    max_speed_box_bg = rl.Rectangle(rect.x + 30, rect.y + 45, 170, 170)
    rl.draw_rectangle_rounded(max_speed_box_bg, 0.186, 10, Colors.BLACK_TRANSLUCENT)

    max_speed_box = rl.Rectangle(max_speed_box_bg.x + 5, max_speed_box_bg.y + 5, 160, 160)
    rl.draw_rectangle_rounded_lines_ex(max_speed_box, 0.186, 10, 2, Colors.WHITE_TRANSLUCENT)

    # MAX text
    max_text = "MAX"
    self._draw_text(
      max_speed_box.x + max_speed_box.width / 2,
      max_speed_box.y + 30,
      max_text,
      FontSizes.middle,
      max_color
    )

    # MAX speed value
    max_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.cruise_speed))
    self._draw_text(
      max_speed_box.x + max_speed_box.width / 2,
      max_speed_box.y + 100,
      max_speed_text,
      FontSizes.big,
      speed_color
    )

    # SET speed box with background (only if NDA or stock limit is active)
    if self.nda_state > 0 or self.stock_limit_speed > 0:
      set_speed_box_bg = rl.Rectangle(rect.x + 30, rect.y + 45 + 170, 170, 170)
      rl.draw_rectangle_rounded(set_speed_box_bg, 0.186, 10, Colors.BLACK_TRANSLUCENT)

      set_speed_box = rl.Rectangle(set_speed_box_bg.x + 5, set_speed_box_bg.y + 5, 160, 160)
      rl.draw_rectangle_rounded_lines_ex(set_speed_box, 0.186, 10, 2, Colors.WHITE_TRANSLUCENT)

      # SET text
      set_text = "SET"
      self._draw_text(
        set_speed_box.x + set_speed_box.width / 2,
        set_speed_box.y + 30,
        set_text,
        FontSizes.middle,
        max_color
      )

      # SET speed value (apply_speed)
      set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(self.apply_speed))
      self._draw_text(
        set_speed_box.x + set_speed_box.width / 2,
        set_speed_box.y + 100,
        set_speed_text,
        FontSizes.big,
        speed_color,
      )

    # Traffic box with background
    traffic_box_bg = rl.Rectangle(rect.x + 200, rect.y + 45, 95, 170)
    rl.draw_rectangle_rounded(traffic_box_bg, 0.35, 10, Colors.BLACK_TRANSLUCENT)

    traffic_box = rl.Rectangle(traffic_box_bg.x + 5, traffic_box_bg.y + 5, 85, 160)
    rl.draw_rectangle_rounded_lines_ex(traffic_box, 0.35, 10, 2, Colors.WHITE_TRANSLUCENT)

    # Traffic light
    traffic_w = 77
    traffic_h = 154
    traffic_x = traffic_box.x + (traffic_box.width - traffic_w) / 2
    traffic_y = traffic_box.y + (traffic_box.height - traffic_h) / 2
    traffic_center_y = traffic_y + traffic_h / 2

    if self.traffic_state == 1:
      rl.draw_texture_pro(
        self.traffic_red_img,
        rl.Rectangle(0, 0, traffic_w, traffic_h),
        rl.Rectangle(traffic_x, traffic_y, traffic_w, traffic_h),
        rl.Vector2(0, 0), 0, Colors.WHITE
      )
    elif self.traffic_state == 2:
      rl.draw_texture_pro(
        self.traffic_green_img,
        rl.Rectangle(0, 0, traffic_w, traffic_h),
        rl.Rectangle(traffic_x, traffic_y, traffic_w, traffic_h),
        rl.Vector2(0, 0), 0, Colors.WHITE
      )
    else:
      rl.draw_texture_pro(
        self.traffic_off_img,
        rl.Rectangle(0, 0, traffic_w, traffic_h),
        rl.Rectangle(traffic_x, traffic_y, traffic_w, traffic_h),
        rl.Vector2(0, 0), 0, Colors.WHITE
      )

    # Draw speed limit sign
    if limit_speed > 0:
      radius = 60

      center_x = traffic_x + traffic_w + 20 + radius + 15
      center_y = traffic_y + traffic_h / 2

      # Draw circles for sign
      rl.draw_circle(int(center_x), int(center_y), radius + 15, Colors.WHITE)
      rl.draw_circle(int(center_x), int(center_y), radius + 14, Colors.RED)
      rl.draw_circle(int(center_x), int(center_y), radius, Colors.WHITE)

      # Draw speed number
      limit_speed_text = str(int(limit_speed))
      self._draw_text(
        center_x,
        center_y,
        limit_speed_text,
        FontSizes.big,
        Colors.BLACK
      )

      # Draw distance if available
      left_dist = 0
      if self.cam_limit_speed > 0 and self.cam_limit_speed_left_dist > 0:
        left_dist = self.cam_limit_speed_left_dist
      elif self.section_limit_speed > 0 and self.section_left_dist > 0:
        left_dist = self.section_left_dist

      if left_dist > 0:
        if left_dist >= 1000:
          dist_text = f"{left_dist / 1000:.1f} km"
        else:
          dist_text = f"{int(left_dist)} m"

        self._draw_text_with_background(
          x=center_x,
          y=center_y + radius + 10,
          text=dist_text,
          font_size=FontSizes.middle,
          text_color=Colors.WHITE_TRANSLUCENT,
        )

    # Road sign
    sign_w = 150
    sign_h = 150
    sign_x = traffic_x + 250
    sign_y = traffic_center_y - sign_h / 2

    if self.road_signs == 1:
      rl.draw_texture_pro(
        self.school_zone_img,
        rl.Rectangle(0, 0, sign_w, sign_h),
        rl.Rectangle(sign_x, sign_y, sign_w, sign_h),
        rl.Vector2(0, 0), 0, Colors.WHITE
      )
    elif self.cam_limit_speed > 0 and self.cam_limit_speed_left_dist > 0:
      rl.draw_texture_pro(
        self.speed_camera_img,
        rl.Rectangle(0, 0, sign_w, sign_h),
        rl.Rectangle(sign_x, sign_y, sign_w, sign_h),
        rl.Vector2(0, 0), 0, Colors.WHITE
      )

  def _get_current_limit_speed(self) -> float:
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

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
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

    y_center_speed = 130

    self._draw_text_with_outline(
      center_x,
      y_center_speed,
      speed_text,
      FontSizes.current_speed,
      speed_color,
    )

    y_bottom_speed = y_center_speed + FontSizes.current_speed / 2
    y_padding_gap = 10
    y_center_unit = y_bottom_speed + y_padding_gap

    unit_text = "km/h" if ui_state.is_metric else "mph"
    self._draw_text_with_outline(
      center_x,
      y_center_unit,
      unit_text,
      FontSizes.speed_unit,
      Colors.LIGHT_ORANGE,
    )

  def _draw_upper_info(self, rect: rl.Rectangle) -> None:
    x = rect.x + UIConfig.border_size * 2
    y = rect.y + 20

    # Upper left - Car name
    car_name = self.params.get("CarName") or ""

    self._draw_text_with_background(
      x=x,
      y=y,
      text=car_name,
      font_size=FontSizes.info_text,
      text_color=Colors.WHITE_TRANSLUCENT,
      alignment="L"
    )

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
      self._draw_text_with_background(
        x=x,
        y=y,
        text=badge,
        font_size=FontSizes.info_text,
        text_color=Colors.LIME,
        alignment="L"
      )

      badge_width = measure_text_cached(self._font_bold, badge, FontSizes.info_text).x
      x += badge_width + 30

    x = rect.x + rect.width - UIConfig.border_size * 2
    y = rect.y + 20

    # Upper right - GPS info
    if self.gps_satellite_count == 0:
      gps_text = "No GPS Signal"
    else:
      alt_str = "--" if self.gps_vertical_accuracy == 0 or self.gps_vertical_accuracy > 100 else f"{self.gps_altitude:.1f} m"
      acc_str = "--" if self.gps_horizontal_accuracy == 0 or self.gps_horizontal_accuracy > 100 else f"{self.gps_horizontal_accuracy:.1f} m"
      gps_text = f"Alt({alt_str}) Acc({acc_str}) Sat({self.gps_satellite_count})"

    self._draw_text_with_background(
      x=x,
      y=y,
      text=gps_text,
      font_size=FontSizes.info_text,
      text_color=Colors.WHITE_TRANSLUCENT,
      alignment="R"
    )

  def _draw_bottom_info(self, rect: rl.Rectangle) -> None:
    # Bottom left - date
    x = rect.x + UIConfig.border_size * 2
    y = rect.y + rect.height - 20
    date_str = datetime.now().strftime("%Y-%m-%d")
    self._draw_text_with_background(
      x=x,
      y=y,
      text=date_str,
      font_size=FontSizes.info_text,
      text_color=Colors.WHITE_TRANSLUCENT,
      alignment="L"
    )

    # Steering Angle info
    x = rect.x + 400
    steer_info = f"SteerRatio({self.steer_ratio:.1f}) Torque({abs(self.steer_torque):.1f}) Curvature({abs(self.curvature):.3f})"
    self._draw_text_with_background(
      x=x,
      y=y,
      text=steer_info,
      font_size=FontSizes.info_text,
      text_color=Colors.WHITE_TRANSLUCENT,
      alignment="L"
    )

    # Bottom right - version info
    x = rect.x + rect.width - UIConfig.border_size * 2
    version = self.params.get("UpdaterCurrentDescription") or ""
    self._draw_text_with_background(
      x=x,
      y=y,
      text=version,
      font_size=FontSizes.info_text,
      text_color=Colors.WHITE_TRANSLUCENT,
      alignment="R"
    )

  def _draw_tpms_distance(self, rect: rl.Rectangle) -> None:
    tpms_w = 160
    tpms_h = 208
    tpms_x = rect.x + rect.width - tpms_w - UIConfig.border_size
    tpms_y = rect.y + rect.height - tpms_h - UIConfig.border_size * 2

    # Draw TPMS background image
    rl.draw_texture_pro(
      self.tpms_img,
      rl.Rectangle(0, 0, tpms_w, tpms_h),
      rl.Rectangle(tpms_x, tpms_y, tpms_w, tpms_h),
      rl.Vector2(0, 0), 0, Colors.WHITE_TRANSLUCENT
    )

    def get_tpms_color(pressure):
      if pressure < 5 or pressure > 60:
        return Colors.ORANGE
      if pressure < 31:
        return Colors.RED
      return Colors.BLACK

    def get_tpms_text(pressure):
      if pressure < 5 or pressure > 60:
        return "--"
      return str(round(pressure))

    self._draw_text(
      tpms_x + 28,
      tpms_y + 43,
      get_tpms_text(self.fl),
      FontSizes.info_text,
      get_tpms_color(self.fl),
    )
    self._draw_text(
      tpms_x + 136,
      tpms_y + 43,
      get_tpms_text(self.fr),
      FontSizes.info_text,
      get_tpms_color(self.fr),
    )
    self._draw_text(
      tpms_x + 28,
      tpms_y + 158,
      get_tpms_text(self.rl),
      FontSizes.info_text,
      get_tpms_color(self.rl),
    )
    self._draw_text(
      tpms_x + 136,
      tpms_y + 158,
      get_tpms_text(self.rr),
      FontSizes.info_text,
      get_tpms_color(self.rr),
    )

    personality = self.params.get("LongitudinalPersonality") or "1"
    dist_imgs = [self.dist1_img, self.dist2_img, self.dist3_img, self.dist4_img]
    dist_idx = min(int(personality), 3)

    dist_w = 100
    dist_h = 250
    dist_x = tpms_x + (tpms_w / 2) - (dist_w / 2)
    dist_y = tpms_y - dist_h - 10

    rl.draw_texture_pro(
      dist_imgs[dist_idx],
      rl.Rectangle(0, 0, dist_w, dist_h),
      rl.Rectangle(dist_x, dist_y, dist_w, dist_h),
      rl.Vector2(0, 0), 0, Colors.WHITE_TRANSLUCENT
    )

  def _get_color_for_angle(self, angle: float) -> rl.Color:
    abs_angle = abs(angle)
    if abs_angle > 360:
      return Colors.DARK_RED
    elif abs_angle > 240:
      return Colors.RED
    elif abs_angle > 120:
      return Colors.ORANGE
    return Colors.LIME

  def _draw_blinkers(self, rect: rl.Rectangle) -> None:
    if self.blink_wait > 0:
      self.blink_wait -= 1
      self.blink_index = 0
      return

    if not (self.left_blinker or self.right_blinker):
      self.blink_index = 0
      return

    # Update blinker animation
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
    blinker_width = 200
    blinker_height = 200
    alpha_base = 0.8

    # Draw left blinker
    if self.left_blinker:
      x = center_x - 200
      direction = -1
      blinker_img = self.turnsignal_l_img

      for i in range(BLINKER_DRAW_COUNT):
        distance = abs(self.blink_index - i)
        alpha = alpha_base if distance == 0 else alpha_base / (distance * 2)

        if alpha > 0.05:
          x_pos = x + int(i * blinker_width * 0.6 * direction)
          color = colors_alpha(Colors.WHITE, int(alpha * 255))

          rl.draw_texture_pro(
            blinker_img,
            rl.Rectangle(0, 0, blinker_img.width, blinker_img.height),
            rl.Rectangle(x_pos, y, blinker_width, blinker_height),
            rl.Vector2(0, 0), 0, color
          )

    # Draw right blinker
    if self.right_blinker:
      x = center_x
      direction = 1
      blinker_img = self.turnsignal_r_img

      for i in range(BLINKER_DRAW_COUNT):
        distance = abs(self.blink_index - i)
        alpha = alpha_base if distance == 0 else alpha_base / (distance * 2)

        if alpha > 0.05:
          x_pos = x + int(i * blinker_width * 0.6 * direction)
          color = colors_alpha(Colors.WHITE, int(alpha * 255))

          rl.draw_texture_pro(
            blinker_img,
            rl.Rectangle(0, 0, blinker_img.width, blinker_img.height),
            rl.Rectangle(x_pos, y, blinker_width, blinker_height),
            rl.Vector2(0, 0), 0, color
          )

  def _draw_blind_spot_detect(self, rect: rl.Rectangle) -> None:
    blinder_w = 184
    blinder_h = 200

    center_y = rect.y + rect.height / 2
    y_pos = center_y - blinder_h / 2

    segment_width = rect.width / 5

    if self.left_blind_spot:
      x_pos = rect.x + segment_width * 1.5 - blinder_w / 2

      rl.draw_texture_pro(
        self.blind_spot_left_img,
        rl.Rectangle(0, 0, blinder_w, blinder_h),
        rl.Rectangle(x_pos, y_pos, blinder_w, blinder_h),
        rl.Vector2(0, 0), 0, Colors.WHITE
      )

    if self.right_blind_spot:
      x_pos = rect.x + segment_width * 3.5 - blinder_w / 2

      rl.draw_texture_pro(
        self.blind_spot_right_img,
        rl.Rectangle(0, 0, blinder_w, blinder_h),
        rl.Rectangle(x_pos, y_pos, blinder_w, blinder_h),
        rl.Vector2(0, 0), 0, Colors.WHITE
      )

  # ----------------- helper function -----------------
  def _draw_text(self, x: float, y: float, text: str, font_size: int, text_color: rl.Color, alignment: str = "C") -> None:
    text_size = measure_text_cached(self._font_bold, text, font_size)

    if alignment == "L":
      draw_x = x
    elif alignment == "R":
      draw_x = x - text_size.x
    else: # alignment == "C":
      draw_x = x - text_size.x / 2

    rl.draw_text_ex(
      self._font_bold,
      text,
      rl.Vector2(draw_x, y - text_size.y / 2),
      font_size,
      0,
      text_color
    )

  def _draw_text_with_background(self, x: float, y: float, text: str, font_size: int, text_color: rl.Color, alignment: str = "C") -> None:
    text_size = measure_text_cached(self._font_bold, text, font_size)
    text_width = text_size.x
    text_height = text_size.y

    padding_x: int = 10
    padding_y: int = 4

    bg_width = text_width + (padding_x * 2)
    bg_height = text_height + (padding_y * 2)

    if alignment == "L":
      bg_x = x - padding_x
    elif alignment == "R":
      bg_x = x - bg_width + padding_x
    else: # alignment == "C":
      bg_x = x - bg_width / 2

    bg_y = y - (bg_height / 2)

    corner_radius: float = 0.5
    segments: int = 10
    bg_color = Colors.BLACK_TRANSLUCENT
    rl.draw_rectangle_rounded(
      rl.Rectangle(bg_x, bg_y, bg_width, bg_height),
      corner_radius,
      segments,
      bg_color
    )

    self._draw_text(x, y, text, font_size, text_color, alignment)

  def _draw_text_with_outline(self, x: float, y: float, text: str, font_size: int, text_color: rl.Color, alignment: str = "C") -> None:
    text_size = measure_text_cached(self._font_bold, text, font_size)

    if alignment == "L":
      draw_x = x
    elif alignment == "R":
      draw_x = x - text_size.x
    else:  # alignment == "C":
      draw_x = x - text_size.x / 2

    draw_y = y - text_size.y / 2

    outline_thickness: int = 2
    offsets = [
      (-outline_thickness, -outline_thickness),
      (0, -outline_thickness),
      (outline_thickness, -outline_thickness),
      (-outline_thickness, 0),
      (outline_thickness, 0),
      (-outline_thickness, outline_thickness),
      (0, outline_thickness),
      (outline_thickness, outline_thickness),
    ]

    outline_color = Colors.BLACK_TRANSLUCENT
    for offset_x, offset_y in offsets:
      rl.draw_text_ex(
        self._font_bold,
        text,
        rl.Vector2(draw_x + offset_x, draw_y + offset_y),
        font_size,
        0,
        outline_color
      )

    rl.draw_text_ex(
      self._font_bold,
      text,
      rl.Vector2(draw_x, draw_y),
      font_size,
      0,
      text_color
    )

  def _draw_steer_gradient_border(self, center_x: float, center_y: float, icon_size: float, angle: float) -> None:
    if angle == 0:
      return

    border_thickness = 10
    radius = icon_size / 2
    adjusted_radius = radius + border_thickness / 2
    segments = 60
    abs_angle = abs(angle)
    angle_range = min(abs_angle, 360)
    start_angle_deg = 90

    for i in range(segments):
      segment_angle = (angle_range / segments) * i
      next_segment_angle = (angle_range / segments) * (i + 1)
      progress = segment_angle / 360.0

      if progress < 0.33:
        t = progress / 0.33
        color = rl.Color(int(120 + (255 - 120) * t), int(255 - (255 - 149) * t), int(120 - 120 * t), 200)
      elif progress < 0.67:
        t = (progress - 0.33) / 0.34
        color = rl.Color(255, int(149 - (149 - 34) * t), int(0 + (49 - 0) * t), 200)
      else:
        color = colors_alpha(Colors.RED, 200)

      if angle > 0:
        current_angle = start_angle_deg + segment_angle
        next_angle = start_angle_deg + next_segment_angle
      else:
        current_angle = start_angle_deg - segment_angle
        next_angle = start_angle_deg - next_segment_angle

      current_rad = math.radians(current_angle)
      next_rad = math.radians(next_angle)
      start_x = center_x + math.cos(current_rad) * adjusted_radius
      start_y = center_y - math.sin(current_rad) * adjusted_radius
      end_x = center_x + math.cos(next_rad) * adjusted_radius
      end_y = center_y - math.sin(next_rad) * adjusted_radius

      rl.draw_line_ex(rl.Vector2(start_x, start_y), rl.Vector2(end_x, end_y), border_thickness, color)

    if abs_angle > 360:
      extra_angle = abs_angle - 360
      extra_segments = int((extra_angle / 360) * segments)

      for i in range(extra_segments):
        segment_angle = 360 + (extra_angle / extra_segments) * i
        next_segment_angle = 360 + (extra_angle / extra_segments) * (i + 1)

        if angle > 0:
          current_angle = start_angle_deg + segment_angle
          next_angle = start_angle_deg + next_segment_angle
        else:
          current_angle = start_angle_deg - segment_angle
          next_angle = start_angle_deg - next_segment_angle

        current_rad = math.radians(current_angle)
        next_rad = math.radians(next_angle)
        start_x = center_x + math.cos(current_rad) * adjusted_radius
        start_y = center_y - math.sin(current_rad) * adjusted_radius
        end_x = center_x + math.cos(next_rad) * adjusted_radius
        end_y = center_y - math.sin(next_rad) * adjusted_radius

        rl.draw_line_ex(rl.Vector2(start_x, start_y), rl.Vector2(end_x, end_y), border_thickness, colors_alpha(Colors.DARK_RED, 200))
