import math
import logging
import time
import numpy as np

from opendbc.car import structs
from openpilot.common.params import Params
from openpilot.common.constants import UnitConverter
from openpilot.selfdrive.car.cruise import (V_CRUISE_MIN, V_CRUISE_MAX, V_CRUISE_UNSET, V_CRUISE_INITIAL,
                                            V_CRUISE_INITIAL_EXPERIMENTAL_MODE,
                                            CRUISE_LONG_PRESS, IMPERIAL_INCREMENT)
from opendbc.car.hyundai.values import Buttons
from openpilot.selfdrive.addon.navi_controller import SpeedLimiter

"""
MPH_TO_KPH = 1.609344
KPH_TO_MPH = 1. / MPH_TO_KPH
MS_TO_KPH = 3.6
KPH_TO_MS = 1. / MS_TO_KPH
MS_TO_MPH = MS_TO_KPH * KPH_TO_MPH
MPH_TO_MS = MPH_TO_KPH * KPH_TO_MS

V_CRUISE_MIN = 10
V_CRUISE_MAX = 145
V_CRUISE_UNSET = 255
V_CRUISE_INITIAL = 30
V_CRUISE_INITIAL_EXPERIMENTAL_MODE = 105
CRUISE_LONG_PRESS = 50
"""

ButtonType = structs.CarState.ButtonEvent.Type
GearShifter = structs.CarState.GearShifter

NO_ACTIVE_LIMIT = 255.
SCHOOL_ZONE_SPEED = 30.0
SCHOOL_ZONE_MAX_SPEED = 50.0

IGNORE_LIMIT_TIMEOUT_TICKS = 3000  # 100Hz 기준 30초 (무시 타이머)
LIMIT_CHANGE_TIMEOUT_TICKS = 300   # 100Hz 기준 3초 (제한속도 변경 대기 시간)
AVAILABLE_TIMEOUT_TICKS = 300      # 100Hz 기준 3초 (크루즈 활성화 대기 시간)
GAS_PRESSED_OVERRIDE_TICKS = 100   # 100Hz 기준 1초 (가속 페달 오버라이드 대기 시간)

CURVE_MIN_SPEED_CLU = 30.0
CURVE_STRONG_REDUCTION_RATIO = 1.3
CURVE_STRONG_REDUCTION_FACTOR = 0.9

STEER_DECEL_START_ANGLE_DEG = 45.0
STEER_DECEL_ACTIVATION_ANGLE_DEG = 60.0
STEER_DECEL_END_ANGLE_DEG = 120.0
STEER_DECEL_ACTIVATION_DELTA_DEG = 5.0
STEER_DECEL_MIN_SPEED_CLU = 20.0

CRUISE_DEBUG_LOG = "/data/cruise_debug.log"
CRUISE_DEBUG_INTERVAL = 0.5

BUTTON_SPAM_TICKS = 20


def _setup_debug_logger():
  logger = logging.getLogger("cruise_controller")
  logger.setLevel(logging.DEBUG)
  logger.propagate = False

  if logger.handlers:
    return logger

  try:
    handler = logging.FileHandler(CRUISE_DEBUG_LOG, mode="w")
    handler.setFormatter(logging.Formatter(
      "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)
    logger.info("=== Cruise Controller Session Started ===")
  except OSError:
    pass

  return logger


cruise_log = _setup_debug_logger()


def _get_button_limit(speed_limiter, CS):
  nda_active = bool(speed_limiter.get_active())
  if nda_active:
    navi_data = speed_limiter.naviData
    if navi_data is None:
      return 0., False

    section_limit = float(getattr(navi_data, 'sectionLimitSpeed', 0.) or 0.)
    section_left_dist = float(getattr(navi_data, 'sectionLeftDist', 0.) or 0.)
    if section_limit > 0 and section_left_dist > 0:
      return section_limit, True

    camera_limit = float(getattr(navi_data, 'camLimitSpeed', 0.) or 0.)
    camera_left_dist = float(getattr(navi_data, 'camLimitSpeedLeftDist', 0.) or 0.)
    if camera_limit > 0 and camera_left_dist > 0:
      return camera_limit, True

    road_limit = float(speed_limiter.get_road_limit_speed() or 0.)
    return (road_limit, False) if road_limit > 0 else (0., False)

  stock_road_limit = float(CS.exState.navLimitSpeed or 0.)
  return (stock_road_limit, False) if stock_road_limit > 0 else (0., False)


class CruiseButtonHandler:
  def __init__(self):
    self.btn_count = 0
    self.prev_btn = ButtonType.unknown
    self.btn_long_pressed = False
    self.last_btn = ButtonType.unknown
    self.double_pressed_timer = 0

  def update(self, btn_events):
    btn = ButtonType.unknown
    double_pressed = False

    if self.btn_count > 0:
      self.btn_count += 1

    if self.double_pressed_timer > 0:
      self.double_pressed_timer -= 1

    for b in btn_events:
      if b.pressed and self.btn_count == 0 and b.type in [
        ButtonType.accelCruise,
        ButtonType.decelCruise,
        ButtonType.gapAdjustCruise,
        ButtonType.cancel,
        # ButtonType.lfaButton
      ]:
        self.btn_count = 1
        self.prev_btn = b.type
      elif not b.pressed and self.btn_count > 0:
        if not self.btn_long_pressed:
          btn = b.type

          if self.last_btn == btn and self.double_pressed_timer > 0:
            double_pressed = True
            self.last_btn = ButtonType.unknown
            self.double_pressed_timer = 0
          else:
            self.last_btn = btn
            self.double_pressed_timer = 40

        self.btn_long_pressed = False
        self.btn_count = 0

    if self.btn_count > CRUISE_LONG_PRESS:
      self.btn_long_pressed = True
      btn = self.prev_btn
      self.btn_count %= CRUISE_LONG_PRESS
      self.last_btn = ButtonType.unknown

    return btn, self.btn_long_pressed, double_pressed


class CruiseController:
  def __init__(self, CP, CI):
    self.CP = CP
    self.CI = CI

    self.params = Params()
    self.experimental_mode = self.params.get_bool("ExperimentalMode")

    self.conv = UnitConverter()
    self.btn_handler = CruiseButtonHandler()
    self.min_set_speed_clu = self.conv.to_current_unit(
      V_CRUISE_MIN) if CruiseStateManager.instance().cruise_state_control else self.conv.to_current_unit(
      V_CRUISE_INITIAL)
    self.max_set_speed_clu = self.conv.to_current_unit(V_CRUISE_MAX)

    self.btn = Buttons.NONE
    self.override_speed_clu = 0.
    self.apply_limit_speed_clu = 0.
    self.curve_speed_clu = 0.
    self.applied_speed_clu = 0.
    self.requested_speed_clu = V_CRUISE_UNSET
    self.requested_speed_clu_last = 0.
    self.road_limit_speed_clu = 0.
    self.camera_limit_speed_clu = 0.
    self.steer_limit_speed_clu = 0.
    self.lead_limit_speed_clu = 0.
    self.prev_steering_angle = 0.
    self.prev_cruise_enabled = False
    self.cruise_just_enabled = False
    self.gas_override_active = False
    self.limit_speed_updated = False
    self.steer_decel_active = False
    self.steer_decel_entry_speed_ms: float | None = None
    self.v_cruise_kph = V_CRUISE_UNSET
    self.v_cruise_cluster_kph = V_CRUISE_UNSET

    self.gas_pressed_count = 0
    self.ignore_road_limit_temporarily = False
    self.ignore_limit_timer = 0

    self.prev_road_limit_speed = 0.
    self.pending_road_limit_speed = 0.
    self.limit_change_timer = 0
    self.prev_section_active = False
    self.prev_section_limit_speed = 0.
    self.pending_road_restore = False

    self.prev_model_mono_time = 0
    self.cached_curve_speed_ms: float | None = None

    self.button_spam_wait_timer = 0
    self.button_spam_count = 0
    self.button_spam_start_speed_clu: int | None = None

    self._debug_last_time = 0.
    self._debug_last_state = None
    cruise_log.info(
      "INIT long=%d pcm=%d state_control=%d metric=%d min_set=%.1f max_set=%.1f",
      self.CP.openpilotLongitudinalControl, self.CP.pcmCruise,
      CruiseStateManager.instance().cruise_state_control, self.conv.is_metric,
      self.min_set_speed_clu, self.max_set_speed_clu,
    )

  def reset(self):
    self.btn = Buttons.NONE
    self.button_spam_wait_timer = 0
    self.button_spam_count = 0
    self.button_spam_start_speed_clu = None
    self.override_speed_clu = 0.
    self.apply_limit_speed_clu = 0.
    self.curve_speed_clu = 0.
    self.gas_pressed_count = 0
    self.ignore_road_limit_temporarily = False
    self.ignore_limit_timer = 0
    self.prev_model_mono_time = 0
    self.cached_curve_speed_ms = None
    self.steer_decel_active = False
    self.steer_decel_entry_speed_ms = None
    self.prev_steering_angle = 0.
    self.pending_road_limit_speed = 0.
    self.limit_change_timer = 0
    self.pending_road_restore = False
    self.cruise_just_enabled = False
    self.gas_override_active = False
    self.limit_speed_updated = False

  def _finish_button_spam(self):
    self.btn = Buttons.NONE
    self.button_spam_count = 0
    self.button_spam_wait_timer = BUTTON_SPAM_TICKS
    self.button_spam_start_speed_clu = None

  def _reset_section_state(self):
    self.prev_section_active = False
    self.prev_section_limit_speed = 0.
    self.pending_road_restore = False

  def _road_limit_target(self, road_limit_speed: float) -> float:
    ratio = np.interp(road_limit_speed,
                      [self.conv.to_current_unit(10.0), self.conv.to_current_unit(100.0)],
                      [1.30, 1.10])
    return road_limit_speed * ratio

  def _set_limit_speed(self, target_speed: float):
    self.requested_speed_clu = target_speed
    self.limit_speed_updated = True
    if CruiseStateManager.instance().cruise_state_control:
      CruiseStateManager.instance().speed_ms = self.conv.to_ms(target_speed)

  @staticmethod
  def _debug_speed(value) -> str:
    if value is None or value == NO_ACTIVE_LIMIT:
      return "-"
    return f"{float(value):.1f}"

  def _debug_limit_state(self, *, CS, cluster_speed_clu, requested_speed, nda_active,
                         section_active, section_limit_speed, section_left_dist,
                         nda_camera_active, is_school_zone, is_limit_zone,
                         road_limit_speed_nda, road_limit_speed_stock, road_limit_speed,
                         road_limit_applies, road_limit_target_clu, lead,
                         speed_candidates, calculated_max_speed_clu, immediate_reasons):
    candidate_names = ("ROAD", "CAMERA", "LEAD", "CURVE", "STEER")
    valid_candidates = [
      (name, speed) for name, speed in zip(candidate_names, speed_candidates, strict=True)
      if speed >= self.min_set_speed_clu and speed != NO_ACTIVE_LIMIT
    ]
    if valid_candidates:
      limit_source, limit_speed = min(valid_candidates, key=lambda item: item[1])
      active_source = "REQUESTED" if requested_speed < limit_speed else limit_source
    else:
      active_source = "NONE"

    event_state = (
      bool(nda_active), bool(section_active), round(float(section_limit_speed), 1),
      bool(nda_camera_active), bool(is_school_zone), bool(is_limit_zone),
      round(float(road_limit_speed or 0.), 1), round(float(self.prev_road_limit_speed), 1),
      round(float(self.pending_road_limit_speed), 1), bool(road_limit_applies),
      bool(self.pending_road_restore), bool(self.ignore_road_limit_temporarily),
      active_source, tuple(immediate_reasons),
      bool(self.steer_decel_active),
    )
    now = time.monotonic()
    if event_state == self._debug_last_state and now - self._debug_last_time < CRUISE_DEBUG_INTERVAL:
      return

    self._debug_last_state = event_state
    self._debug_last_time = now
    log_format = " ".join((
      "LIMIT source=%s ego=%.1f requested=%.1f calculated=%.1f applied=%.1f prev_output=%.1f",
      "candidates[road=%s camera=%s lead=%s curve=%s steer=%s]",
      "nav[nda=%d road_nda=%.1f road_stock=%.1f observed=%s accepted=%.1f target=%s pending=%.1f timer=%d/%d applies=%d restore=%d",
      "section=%d limit=%.1f left=%.0f camera=%d zone=%d school=%d]",
      "lead[present=%d dRel=%.1f vRel=%.1f] ignore=%d timer=%d/%d steer_angle=%.1f immediate=%s",
    ))
    cruise_log.debug(
      log_format,
      active_source, cluster_speed_clu, requested_speed, calculated_max_speed_clu,
      self.apply_limit_speed_clu, self.applied_speed_clu,
      *(self._debug_speed(speed) for speed in speed_candidates),
      nda_active, road_limit_speed_nda, road_limit_speed_stock, self._debug_speed(road_limit_speed),
      self.prev_road_limit_speed, self._debug_speed(road_limit_target_clu),
      self.pending_road_limit_speed, self.limit_change_timer,
      LIMIT_CHANGE_TIMEOUT_TICKS, road_limit_applies, self.pending_road_restore,
      section_active, section_limit_speed, section_left_dist, nda_camera_active,
      is_limit_zone, is_school_zone, lead.present, lead.dRel, lead.vRel,
      self.ignore_road_limit_temporarily, self.ignore_limit_timer, IGNORE_LIMIT_TIMEOUT_TICKS,
      CS.steeringAngleDeg, ",".join(immediate_reasons) or "smooth",
    )

  def _cal_limit_speed(self, CS, sm, current_speed_ms: float, cluster_speed_clu: float, requested_speed_clu: float,
                       double_pressed: bool = False):
    speed_limiter = SpeedLimiter.instance()
    speed_limiter.recv()
    nda_active = speed_limiter.get_active()
    section_limit_speed, section_left_dist = speed_limiter.get_section_limit_speed()
    section_active = bool(nda_active and section_limit_speed > 0 and section_left_dist > 0)
    section_started_or_changed = section_active and (
      not self.prev_section_active or section_limit_speed != self.prev_section_limit_speed
    )
    section_ended = self.prev_section_active and not section_active
    nda_camera_active = bool(nda_active and speed_limiter.get_camera_limit_active())

    road_limit_speed_nda = speed_limiter.get_road_limit_speed()
    road_limit_speed_stock = CS.exState.navLimitSpeed
    road_signs = CS.exState.roadSigns
    is_limit_zone = False
    lead = sm['radarState'].leadOne

    # 1. Camera limit speed. NDA and stock camera sources are mutually exclusive.
    camera_limit_speed_clu = NO_ACTIVE_LIMIT
    if nda_active:
      camera_limit_speed, is_limit_zone = speed_limiter.get_max_speed(cluster_speed_clu)
      is_school_zone = speed_limiter.get_in_school_zone()
      camera_limit_speed_clu = section_limit_speed if section_active else camera_limit_speed
    else:
      is_school_zone = road_signs == 1
      if CS.speedLimit > 0 and CS.speedLimitDistance > 0:
        camera_limit_speed_clu, is_limit_zone = speed_limiter.get_camera_limit_speed_stock(CS, cluster_speed_clu)

    if is_school_zone and not nda_camera_active:
      school_zone_max_limit = self.conv.to_current_unit(SCHOOL_ZONE_MAX_SPEED)
      if 0 < camera_limit_speed_clu < NO_ACTIVE_LIMIT:
        camera_limit_speed_clu = min(camera_limit_speed_clu, school_zone_max_limit)
      elif road_limit_speed_nda > 0 and nda_active:
        camera_limit_speed_clu = min(road_limit_speed_nda, school_zone_max_limit)
      elif road_limit_speed_stock > 0 and not nda_active:
        camera_limit_speed_clu = min(road_limit_speed_stock, school_zone_max_limit)
      else:
        camera_limit_speed_clu = self.conv.to_current_unit(SCHOOL_ZONE_SPEED)

    self.camera_limit_speed_clu = camera_limit_speed_clu

    # 2. Track the observed road limit even while camera/section/protection-zone
    # targets own the applied speed. This prevents a stale road limit when an
    # enforcement zone ends.
    road_limit_speed = None
    road_limit_applies = False
    if nda_active and road_limit_speed_nda > 0:
      road_limit_speed = road_limit_speed_nda
      road_limit_applies = not nda_camera_active and not is_school_zone
    elif not nda_active and road_limit_speed_stock > 0:
      road_limit_speed = road_limit_speed_stock
      road_limit_applies = not is_school_zone

    road_limit_changed = False
    if road_limit_speed is not None:
      if self.prev_road_limit_speed <= 0:
        self.prev_road_limit_speed = road_limit_speed
        self.pending_road_limit_speed = road_limit_speed
        self.limit_change_timer = 0
      elif road_limit_speed == self.prev_road_limit_speed:
        self.pending_road_limit_speed = road_limit_speed
        self.limit_change_timer = 0
      elif road_limit_speed != self.pending_road_limit_speed:
        self.pending_road_limit_speed = road_limit_speed
        self.limit_change_timer = 0
      else:
        self.limit_change_timer += 1
        if self.limit_change_timer > LIMIT_CHANGE_TIMEOUT_TICKS:
          self.prev_road_limit_speed = road_limit_speed
          self.pending_road_limit_speed = road_limit_speed
          self.limit_change_timer = 0
          road_limit_changed = True
    else:
      self.pending_road_limit_speed = 0.
      self.limit_change_timer = 0

    road_limit_ready = road_limit_speed is not None and road_limit_speed == self.prev_road_limit_speed
    road_limit_target_clu = self._road_limit_target(self.prev_road_limit_speed) \
      if road_limit_ready and self.prev_road_limit_speed > 0 else NO_ACTIVE_LIMIT

    if section_started_or_changed:
      self._set_limit_speed(section_limit_speed)
      requested_speed_clu = section_limit_speed
      self.pending_road_restore = False
    elif section_ended:
      self.pending_road_restore = True

    self.prev_section_active = section_active
    self.prev_section_limit_speed = section_limit_speed if section_active else 0.

    # A confirmed road limit can be hidden temporarily by an active camera,
    # section, or school-zone target. Remember it so the road target is
    # restored once that restriction releases ownership of the requested SET.
    camera_target_active = 0 < camera_limit_speed_clu < NO_ACTIVE_LIMIT
    road_target_suspended = section_active or is_school_zone or camera_target_active
    if road_limit_ready and not road_limit_applies and road_target_suspended:
      self.pending_road_restore = True

    if road_limit_changed and road_limit_applies and not self.ignore_road_limit_temporarily:
      self._set_limit_speed(road_limit_target_clu)
      self.pending_road_restore = False
    elif road_limit_changed:
      self.pending_road_restore = True
    elif self.cruise_just_enabled and road_limit_applies and \
         road_limit_target_clu != NO_ACTIVE_LIMIT and not self.ignore_road_limit_temporarily:
      # A confirmed NDA road limit may have been cached while cruise was off.
      # Apply it on the enable edge instead of waiting for another road-limit
      # change (and its 3 second debounce) before synchronizing SET.
      cruise_log.info(
        "NDA_ENABLE_SYNC road=%.1f target=%.1f ego=%.1f",
        road_limit_speed, road_limit_target_clu, cluster_speed_clu,
      )
      self._set_limit_speed(road_limit_target_clu)
      requested_speed_clu = road_limit_target_clu
      self.pending_road_restore = False

    road_limit_speed_clu = road_limit_target_clu if road_limit_applies else NO_ACTIVE_LIMIT
    restore_limit_speed_clu = section_limit_speed if section_active else road_limit_speed_clu

    if self.ignore_road_limit_temporarily:
      self.ignore_limit_timer += 1

      if is_school_zone or double_pressed:
        self.ignore_road_limit_temporarily = False
        self.ignore_limit_timer = 0
      elif self.ignore_limit_timer > IGNORE_LIMIT_TIMEOUT_TICKS:
        self.ignore_road_limit_temporarily = False
        self.ignore_limit_timer = 0

        if restore_limit_speed_clu != NO_ACTIVE_LIMIT and requested_speed_clu != restore_limit_speed_clu:
          self._set_limit_speed(restore_limit_speed_clu)
          self.pending_road_restore = False
        elif road_limit_ready and restore_limit_speed_clu == NO_ACTIVE_LIMIT:
          self.pending_road_restore = True
      else:
        road_limit_speed_clu = NO_ACTIVE_LIMIT

    if self.pending_road_restore and road_limit_applies and \
       road_limit_target_clu != NO_ACTIVE_LIMIT and not self.ignore_road_limit_temporarily:
      if requested_speed_clu != road_limit_target_clu:
        cruise_log.info(
          "ROAD_RESTORE road=%.1f target=%.1f requested=%.1f ego=%.1f",
          road_limit_speed, road_limit_target_clu, requested_speed_clu, cluster_speed_clu,
        )
        self._set_limit_speed(road_limit_target_clu)
      self.pending_road_restore = False

    self.road_limit_speed_clu = road_limit_speed_clu

    # 3. Lead limit speed
    lead_speed = self._cal_lead_speed(lead, cluster_speed_clu)
    lead_limit_speed_clu = lead_speed if self.CP.openpilotLongitudinalControl and lead.present else NO_ACTIVE_LIMIT
    self.lead_limit_speed_clu = lead_limit_speed_clu

    # 4. Curve limit speed
    curve_limit_speed_clu = self._cal_curve_speed_adaptive(sm, current_speed_ms, requested_speed_clu)
    self.curve_speed_clu = curve_limit_speed_clu

    # 5. Steering angle based limit speed
    steer_limit_speed_clu = self._cal_steer_based_speed(current_speed_ms, CS.steeringAngleDeg)
    self.steer_limit_speed_clu = steer_limit_speed_clu

    speed_candidates = [
      road_limit_speed_clu,
      camera_limit_speed_clu,
      lead_limit_speed_clu,
      curve_limit_speed_clu,
      steer_limit_speed_clu
    ]

    valid_limits = [s for s in speed_candidates if s >= self.min_set_speed_clu and s != NO_ACTIVE_LIMIT]

    if valid_limits:
      calculated_max_speed_clu = min(requested_speed_clu, min(valid_limits))
      is_curve_limit = (curve_limit_speed_clu != NO_ACTIVE_LIMIT and curve_limit_speed_clu == min(valid_limits))
    else:
      calculated_max_speed_clu = requested_speed_clu
      is_curve_limit = False

    immediate_conditions = (
      ("stock_long", not self.CP.openpilotLongitudinalControl),
      ("initial", self.apply_limit_speed_clu <= 0),
      ("limit_zone", is_limit_zone),
      ("section_change", section_started_or_changed),
      ("cruise_enable", self.cruise_just_enabled),
      ("curve", is_curve_limit),
      ("double_press", double_pressed),
    )
    immediate_reasons = [name for name, active in immediate_conditions if active]

    if immediate_reasons:
      self.apply_limit_speed_clu = calculated_max_speed_clu
    else:
      error = calculated_max_speed_clu - self.apply_limit_speed_clu
      kp = np.interp(abs(error), [0, 2, 5, 10], [0.01, 0.05, 0.10, 0.20])
      self.apply_limit_speed_clu += error * kp

    self._debug_limit_state(
      CS=CS, cluster_speed_clu=cluster_speed_clu, requested_speed=requested_speed_clu,
      nda_active=nda_active, section_active=section_active, section_limit_speed=section_limit_speed,
      section_left_dist=section_left_dist, nda_camera_active=nda_camera_active,
      is_school_zone=is_school_zone, is_limit_zone=is_limit_zone,
      road_limit_speed_nda=road_limit_speed_nda, road_limit_speed_stock=road_limit_speed_stock,
      road_limit_speed=road_limit_speed, road_limit_applies=road_limit_applies,
      road_limit_target_clu=road_limit_target_clu, lead=lead, speed_candidates=speed_candidates,
      calculated_max_speed_clu=calculated_max_speed_clu, immediate_reasons=immediate_reasons,
    )

  def _cal_lead_speed(self, lead, cluster_speed_clu: float):
    lead_distance_buffer = 5.
    distance = lead.dRel - lead_distance_buffer
    relative_speed = lead.vRel
    lead_decay_factor = 22.
    lead_accel_gain = 1.2
    min_relative_speed = -1.0

    is_valid_deceleration = (
      0 < distance < -relative_speed * lead_decay_factor and
      relative_speed < min_relative_speed
    )

    if not is_valid_deceleration:
      return NO_ACTIVE_LIMIT

    time = distance / relative_speed if abs(relative_speed) > 1e-3 else 0.1
    deceleration_ms = -relative_speed / time
    speed_delta_clu = self.conv.to_clu(deceleration_ms) * lead_accel_gain
    new_speed_clu = cluster_speed_clu + speed_delta_clu
    lead_limit_speed_clu = max(new_speed_clu, self.min_set_speed_clu)

    return lead_limit_speed_clu

  def _calculate_curvature(self, x_positions, y_positions):
    dy = np.gradient(y_positions, x_positions)
    d2y = np.gradient(dy, x_positions)
    return d2y / (1 + dy ** 2) ** 1.5

  def _get_model_based_speed(self, model, current_speed_ms: float, min_curve_speed_ms: float):
    x_positions = np.array(model.position.x)
    y_positions = np.array(model.position.y)

    if len(x_positions) < 10:
      return NO_ACTIVE_LIMIT, 0.0

    curvatures = np.abs(self._calculate_curvature(x_positions, y_positions))
    curv_segment = curvatures[-10:]
    curv_variance = np.var(curv_segment)
    trajectory_length = np.sum(np.sqrt(np.diff(x_positions) ** 2 + np.diff(y_positions) ** 2))
    confidence = min(1.0, trajectory_length / 100.0) * (1.0 / (1.0 + curv_variance * 1000))

    a_y_max = 2.975 - current_speed_ms * 0.0375
    current_curve_speed_ms = float(np.mean(np.sqrt(a_y_max / np.clip(curv_segment, 1e-4, None)))) * 0.85

    current_model_speed = float(max(current_curve_speed_ms, min_curve_speed_ms)) \
      if not math.isnan(current_curve_speed_ms) and current_curve_speed_ms < current_speed_ms else NO_ACTIVE_LIMIT

    lookahead_distance = current_speed_ms * 5.0
    lookahead_indices = (x_positions <= lookahead_distance) & (x_positions > current_speed_ms * 0.5)

    predictive_speed = NO_ACTIVE_LIMIT

    if np.any(lookahead_indices) and np.sum(lookahead_indices) > 5:
      x_ahead = x_positions[lookahead_indices]
      curv_ahead = curvatures[lookahead_indices]

      max_future_curv = np.max(curv_ahead)
      max_curv_idx = np.argmax(curv_ahead)
      curve_distance = x_ahead[max_curv_idx]

      if max_future_curv > 0.005:
        safe_speed_ms = np.sqrt(a_y_max / max_future_curv) * 0.9

        if safe_speed_ms < current_speed_ms and curve_distance > 10:
          required_decel = (current_speed_ms ** 2 - safe_speed_ms ** 2) / (2 * curve_distance)
          max_comfortable_decel = 1.8

          if required_decel <= max_comfortable_decel:
            predictive_speed = safe_speed_ms
            confidence = min(1.0, confidence * 1.5)
          else:
            early_speed_ms = current_speed_ms - 3.0
            predictive_speed = max(early_speed_ms, safe_speed_ms)
            confidence = min(1.0, confidence * 1.2)

    if predictive_speed != NO_ACTIVE_LIMIT:
      if current_model_speed != NO_ACTIVE_LIMIT:
        model_based_speed = min(current_model_speed, predictive_speed)
      else:
        model_based_speed = predictive_speed
    else:
      model_based_speed = current_model_speed

    return model_based_speed, confidence

  def _get_acc_based_speed(self, model, current_speed_ms: float, min_curve_speed_ms: float):
    orientation_rate = np.array(model.orientationRate.z)
    velocity = np.array(model.velocity.x)

    if len(orientation_rate) == 0 or len(velocity) == 0:
      return NO_ACTIVE_LIMIT, 0.0

    predicted_lat_acc = float(np.max(np.abs(orientation_rate * velocity)))
    acc_based_curvature = predicted_lat_acc / max(current_speed_ms, 1.0) ** 2

    orientation_stability = 1.0 - min(1.0, np.std(orientation_rate) / (np.mean(np.abs(orientation_rate)) + 1e-6))
    velocity_stability = 1.0 - min(1.0, np.std(velocity) / (np.mean(velocity) + 1e-6))

    speed_factor = 1.0 if current_speed_ms < self.conv.to_ms(30.0) else 0.7
    confidence = (orientation_stability + velocity_stability) / 2.0 * speed_factor

    a_y_max = 2.975 - current_speed_ms * 0.0375
    acc_speed_ms = np.sqrt(a_y_max / np.clip(acc_based_curvature, 1e-4, None)) * 0.85

    acc_based_speed = float(max(acc_speed_ms, min_curve_speed_ms)) \
      if not math.isnan(acc_speed_ms) and acc_speed_ms < current_speed_ms else NO_ACTIVE_LIMIT

    return acc_based_speed, confidence

  def _cal_curve_speed_adaptive(self, sm, current_speed_ms: float, requested_speed_clu: float):
    if not sm.all_checks(['modelV2']):
      self.prev_model_mono_time = 0
      self.cached_curve_speed_ms = None
      return NO_ACTIVE_LIMIT

    model_mono_time = sm.logMonoTime['modelV2']

    if model_mono_time != self.prev_model_mono_time:
      model = sm['modelV2']
      min_curve_speed_ms = max(self.conv.to_ms(CURVE_MIN_SPEED_CLU), current_speed_ms * 0.5)
      model_speed, model_confidence = self._get_model_based_speed(model, current_speed_ms, min_curve_speed_ms)
      acc_speed, acc_confidence = self._get_acc_based_speed(model, current_speed_ms, min_curve_speed_ms)

      base_model_weight = float(np.interp(
        current_speed_ms,
        [self.conv.to_ms(30.0), self.conv.to_ms(60.0), self.conv.to_ms(100.0)],
        [0.3, 0.5, 0.7],
      ))
      estimates = []
      for speed, base_weight, confidence in (
        (model_speed, base_model_weight, model_confidence),
        (acc_speed, 1.0 - base_model_weight, acc_confidence),
      ):
        if speed == NO_ACTIVE_LIMIT or not np.isfinite(speed):
          continue
        confidence = float(np.clip(confidence, 0.0, 1.0)) if np.isfinite(confidence) else 0.0
        estimates.append((float(speed), base_weight * confidence))

      if estimates:
        total_weight = sum(weight for _, weight in estimates)
        calculated_curve_speed_ms = (
          sum(speed * weight for speed, weight in estimates) / total_weight
          if total_weight > 1e-6 else min(speed for speed, _ in estimates)
        )

        speed_reduction_ratio = current_speed_ms / calculated_curve_speed_ms
        if speed_reduction_ratio > CURVE_STRONG_REDUCTION_RATIO:
          calculated_curve_speed_ms *= CURVE_STRONG_REDUCTION_FACTOR
        self.cached_curve_speed_ms = calculated_curve_speed_ms
      else:
        self.cached_curve_speed_ms = None

      self.prev_model_mono_time = model_mono_time

    if self.cached_curve_speed_ms is None:
      return NO_ACTIVE_LIMIT

    curve_speed_ms = min(self.cached_curve_speed_ms, self.conv.to_ms(requested_speed_clu))
    return self.conv.to_clu(curve_speed_ms)

  def _cal_steer_based_speed(self, current_speed_ms: float, steering_angle_deg: float):
    abs_steer_angle = abs(steering_angle_deg)

    if abs_steer_angle < STEER_DECEL_START_ANGLE_DEG:
      self.steer_decel_active = False
      self.steer_decel_entry_speed_ms = None
      self.prev_steering_angle = abs_steer_angle
      return NO_ACTIVE_LIMIT

    angle_delta_deg = abs(abs_steer_angle - self.prev_steering_angle)
    self.prev_steering_angle = abs_steer_angle

    if not self.steer_decel_active:
      should_activate = (
        angle_delta_deg > STEER_DECEL_ACTIVATION_DELTA_DEG or
        abs_steer_angle > STEER_DECEL_ACTIVATION_ANGLE_DEG
      )
      if not should_activate:
        return NO_ACTIVE_LIMIT

      self.steer_decel_active = True
      self.steer_decel_entry_speed_ms = current_speed_ms

    entry_speed_ms = self.steer_decel_entry_speed_ms or current_speed_ms
    speed_multiplier = float(np.interp(
      abs_steer_angle,
      [STEER_DECEL_START_ANGLE_DEG, STEER_DECEL_END_ANGLE_DEG],
      [0.95, 0.75],
    ))
    target_speed_ms = entry_speed_ms * speed_multiplier
    min_allowed_speed_clu = max(STEER_DECEL_MIN_SPEED_CLU, self.min_set_speed_clu)
    steer_based_speed_ms = max(target_speed_ms, self.conv.to_ms(min_allowed_speed_clu))
    return self.conv.to_clu(steer_based_speed_ms)

  def _override_speed(self, CS, cluster_speed_clu: float, requested_speed_clu: float, cruise_btn_pressed: bool):
    syncing = CS.gasPressed and not cruise_btn_pressed
    sync_margin = 3.
    self.gas_override_active = False

    if not self.CP.openpilotLongitudinalControl:
      initial_speed_clu = self.conv.to_current_unit(V_CRUISE_INITIAL)
      if syncing and cluster_speed_clu + sync_margin > requested_speed_clu:
        set_speed = np.clip(cluster_speed_clu + sync_margin, initial_speed_clu, self.max_set_speed_clu)
        requested_speed_clu = int(round(set_speed))

      self.override_speed_clu = requested_speed_clu
      if self.apply_limit_speed_clu > initial_speed_clu:
        self.override_speed_clu = np.clip(self.override_speed_clu, initial_speed_clu, self.apply_limit_speed_clu)

    elif CS.cruiseState.enabled:
      if syncing:
        self.gas_pressed_count += 1
        if self.gas_pressed_count > GAS_PRESSED_OVERRIDE_TICKS:
          previous_set_speed = self.requested_speed_clu
          self.ignore_road_limit_temporarily = True
          self.ignore_limit_timer = 0
          set_speed = np.clip(cluster_speed_clu + sync_margin, self.min_set_speed_clu, self.max_set_speed_clu)
          requested_speed_clu = float(set_speed)
          self.override_speed_clu = float(set_speed)
          # The accelerator override establishes a new requested SET speed.
          # Keep the displayed cruise/set values and the next non-limited
          # control cycle in sync with that target.
          self.apply_limit_speed_clu = float(set_speed)
          self.gas_override_active = True
          if self.gas_pressed_count == GAS_PRESSED_OVERRIDE_TICKS + 1:
            cruise_log.info(
              "GAS_OVERRIDE start ego=%.1f previous_set=%.1f new_set=%.1f ignore_timeout=%d",
              cluster_speed_clu, previous_set_speed, requested_speed_clu, IGNORE_LIMIT_TIMEOUT_TICKS,
            )

          if CruiseStateManager.instance().cruise_state_control:
            CruiseStateManager.instance().speed_ms = self.conv.to_ms(set_speed)
      else:
        if self.gas_pressed_count > GAS_PRESSED_OVERRIDE_TICKS:
          cruise_log.info(
            "GAS_OVERRIDE end ego=%.1f set=%.1f ignore=%d timer=%d/%d",
            cluster_speed_clu, self.requested_speed_clu,
            self.ignore_road_limit_temporarily, self.ignore_limit_timer, IGNORE_LIMIT_TIMEOUT_TICKS,
          )
        self.gas_pressed_count = 0

    return requested_speed_clu

  def _get_button_to_adjust_speed(self, current_set_speed: float) -> Buttons:
    if self.override_speed_clu < self.conv.to_current_unit(V_CRUISE_INITIAL):
      return Buttons.NONE
    error = self.override_speed_clu - current_set_speed
    if abs(error) < 0.9:
      return Buttons.NONE

    return Buttons.RES_ACCEL if error > 0 else Buttons.SET_DECEL

  def _initialize_v_cruise(self, CS):
    initial_kph = V_CRUISE_INITIAL_EXPERIMENTAL_MODE if self.experimental_mode else V_CRUISE_INITIAL
    initial_speed_clu = self.conv.to_current_unit(initial_kph)

    if any(b.type in (ButtonType.accelCruise, ButtonType.resumeCruise) for b in
           CS.buttonEvents) and self.requested_speed_clu != V_CRUISE_UNSET:
      self.requested_speed_clu = self.requested_speed_clu_last
    else:
      self.requested_speed_clu = int(round(np.clip(
        self.conv.to_clu(CS.vEgo), initial_speed_clu, self.max_set_speed_clu,
      )))

    return self.requested_speed_clu

  def update_v_cruise(self, CS, sm, enabled: bool):
    self.requested_speed_clu_last = self.requested_speed_clu
    requested_speed_clu = self.requested_speed_clu
    current_speed_ms = CS.vEgo
    cluster_speed_clu = self.conv.to_clu(CS.vEgoCluster)

    btn, long_pressed, double_pressed = self.btn_handler.update(CS.buttonEvents)

    if CS.cruiseState.enabled:
      if not self.CP.openpilotLongitudinalControl or not self.CP.pcmCruise:
        requested_speed_clu = self._update_cruise_button(
          CS, requested_speed_clu, btn, long_pressed, double_pressed, enabled,
        )
      else:
        requested_speed_clu = self.conv.to_clu(CS.cruiseState.speed)
        if CS.cruiseState.speed == 0:
          requested_speed_clu = V_CRUISE_UNSET
        elif CS.cruiseState.speed == -1:
          requested_speed_clu = -1
    else:
      requested_speed_clu = V_CRUISE_UNSET

    if self.prev_cruise_enabled != CS.cruiseState.enabled:
      self.prev_cruise_enabled = CS.cruiseState.enabled
      self.cruise_just_enabled = CS.cruiseState.enabled
      cruise_log.info(
        "CRUISE enabled=%d available=%d stock_set=%.1f ego=%.1f gas=%d brake=%d",
        CS.cruiseState.enabled, CS.cruiseState.available,
        self.conv.to_clu(CS.cruiseState.speed), cluster_speed_clu, CS.gasPressed, CS.brakePressed,
      )
      if CS.cruiseState.enabled:
        if not self.CP.pcmCruise:
          requested_speed_clu = self._initialize_v_cruise(CS)
        else:
          requested_speed_clu = self.conv.to_clu(CS.cruiseState.speed)
          if CS.cruiseState.speed == 0:
            requested_speed_clu = V_CRUISE_UNSET
          elif CS.cruiseState.speed == -1:
            requested_speed_clu = -1

    self.requested_speed_clu = requested_speed_clu
    if CS.cruiseState.enabled and 1 < CS.cruiseState.speed < V_CRUISE_UNSET:

      self.limit_speed_updated = False
      self._cal_limit_speed(CS, sm, current_speed_ms, cluster_speed_clu, requested_speed_clu, double_pressed)

      if self.limit_speed_updated:
        requested_speed_clu = self.requested_speed_clu

      min_cruise_speed_clu = self.conv.to_current_unit(V_CRUISE_MIN)
      applied_max_speed_clu = max(self.apply_limit_speed_clu, min_cruise_speed_clu)
      self.applied_speed_clu = float(np.clip(requested_speed_clu, min_cruise_speed_clu, applied_max_speed_clu))
      requested_speed_clu_from_override = self._override_speed(
        CS, cluster_speed_clu, self.requested_speed_clu, self.CI.CS.cruise_buttons[-1] != Buttons.NONE,
      )

      if requested_speed_clu_from_override != self.requested_speed_clu:
        self.requested_speed_clu = requested_speed_clu_from_override
      requested_speed_clu = requested_speed_clu_from_override

      if self.gas_override_active:
        self.applied_speed_clu = self.requested_speed_clu
      elif CruiseStateManager.instance().cruise_state_control:
        self.applied_speed_clu = min(self.applied_speed_clu, max(self.requested_speed_clu, min_cruise_speed_clu))
    else:
      self.applied_speed_clu = cluster_speed_clu
      self.reset()
      self._reset_section_state()

    self.requested_speed_clu = requested_speed_clu
    self.v_cruise_kph = float(self.conv.to_kph(self.applied_speed_clu))
    self.v_cruise_cluster_kph = float(
      self.requested_speed_clu
      if self.requested_speed_clu in (-1, V_CRUISE_UNSET)
      else self.conv.to_kph(self.requested_speed_clu)
    )
    self.cruise_just_enabled = False
    self._update_message(CS)

  def _update_cruise_button(self, CS, requested_speed_clu, btn, long_pressed, double_pressed, enabled):
    previous_speed = requested_speed_clu
    speed_step = 1.
    long_speed_step = 10. if self.conv.is_metric else 5.
    speed_limiter = SpeedLimiter.instance()
    speed_limiter.recv()
    nda_active = bool(speed_limiter.get_active())
    button_limit, enforcement_limit = _get_button_limit(speed_limiter, CS)
    actual_limit = button_limit if enforcement_limit else (
      self.prev_road_limit_speed if button_limit > 0 and button_limit == self.prev_road_limit_speed else 0.
    )
    is_school_zone = speed_limiter.get_in_school_zone() if nda_active else CS.exState.roadSigns == 1

    if enabled:
      if btn != Buttons.NONE:
        if double_pressed and btn == ButtonType.accelCruise:
          if actual_limit > 0:
            if enforcement_limit or is_school_zone:
              requested_speed_clu = actual_limit
            else:
              ratio = np.interp(actual_limit,
                                [self.conv.to_current_unit(10.0), self.conv.to_current_unit(100.0)], [1.30, 1.10])
              requested_speed_clu = actual_limit * ratio
        elif double_pressed and btn == ButtonType.decelCruise:
          if actual_limit > 0:
            requested_speed_clu = actual_limit
        elif not long_pressed:
          if btn == ButtonType.accelCruise:
            requested_speed_clu += speed_step
          elif btn == ButtonType.decelCruise:
            requested_speed_clu -= speed_step
        else:
          if btn == ButtonType.accelCruise:
            requested_speed_clu += long_speed_step - requested_speed_clu % long_speed_step
          elif btn == ButtonType.decelCruise:
            requested_speed_clu -= long_speed_step - (-requested_speed_clu) % long_speed_step

      requested_speed_clu = np.clip(
        round(requested_speed_clu), self.conv.to_current_unit(V_CRUISE_MIN), self.max_set_speed_clu,
      )

    if btn != ButtonType.unknown:
      log_format = " ".join((
        "DRIVER_BUTTON type=%s long=%d double=%d enabled=%d set=%.1f->%.1f",
        "button_limit=%.1f enforcement=%d school=%d nda=%d",
      ))
      cruise_log.info(
        log_format,
        getattr(btn, 'name', str(btn)), long_pressed, double_pressed, enabled,
        previous_speed, requested_speed_clu, button_limit, enforcement_limit, is_school_zone, nda_active,
      )

    return requested_speed_clu

  def spam_message(self, CS, can_sends):
    ascc_enabled = CS.cruiseState.enabled and 1 < CS.cruiseState.speed < V_CRUISE_UNSET and not CS.brakePressed
    btn_pressed = self.CI.CS.cruise_buttons[-1] != Buttons.NONE

    if not self.CP.openpilotLongitudinalControl:
      if not ascc_enabled or btn_pressed:
        self.reset()
        self.button_spam_wait_timer = BUTTON_SPAM_TICKS * 2
        return

    if not ascc_enabled:
      self.reset()

    if self.button_spam_wait_timer > 0:
      self.button_spam_wait_timer -= 1
    elif ascc_enabled and CS.vEgo > 0.1:
      current_set_speed_clu = round(self.conv.to_clu(CS.cruiseState.speed))

      if self.button_spam_count > 0 and current_set_speed_clu != self.button_spam_start_speed_clu:
        cruise_log.debug(
          "BUTTON_SPAM_ACK type=%s speed=%d->%d sent=%d wait=%d",
          getattr(self.btn, 'name', str(self.btn)), self.button_spam_start_speed_clu,
          current_set_speed_clu, self.button_spam_count, BUTTON_SPAM_TICKS,
        )
        self._finish_button_spam()
        return

      if self.button_spam_count == 0:
        self.btn = self._get_button_to_adjust_speed(current_set_speed_clu)
        if self.btn != Buttons.NONE:
          self.button_spam_start_speed_clu = current_set_speed_clu
          cruise_log.debug(
            "BUTTON_SPAM type=%s current=%d target=%.1f max=%d wait=%d",
            getattr(self.btn, 'name', str(self.btn)), current_set_speed_clu,
            self.override_speed_clu, BUTTON_SPAM_TICKS, BUTTON_SPAM_TICKS,
          )

      if self.btn != Buttons.NONE:
        can = self.CI.create_buttons(self.btn)
        if can is not None:
          can_sends.append(can)

        self.button_spam_count += 1
        if self.button_spam_count >= BUTTON_SPAM_TICKS:
          self._finish_button_spam()
      elif self.CP.openpilotLongitudinalControl and self.override_speed_clu >= V_CRUISE_INITIAL:
        self.override_speed_clu = 0.
    elif self.CP.openpilotLongitudinalControl:
      self.override_speed_clu = 0.

  def _update_message(self, CS):
    exState = CS.exState
    exState.ignoreLimitTimer = float(self.ignore_limit_timer)


class CruiseStateManager:
  def __init__(self):
    self.params = Params()
    self.cruise_state_control = self.params.get_bool("CruiseStateControl")

    self.conv = UnitConverter()
    self.btn_handler = CruiseButtonHandler()

    self.available = False
    self.enabled = False
    self.speed_ms = self.conv.to_ms(V_CRUISE_INITIAL)
    self.speed_ms_last = self.conv.to_ms(V_CRUISE_INITIAL)
    self.prev_brake_pressed = False
    self.prev_main_button = False

    self.available_timer = 0

  @classmethod
  def instance(cls):
    if not hasattr(cls, "_instance"):
      cls._instance = cls()
    return cls._instance

  def _reset_available(self):
    self.available = False
    self.available_timer = AVAILABLE_TIMEOUT_TICKS

  def _reset_speed(self, CS):
    self.enabled = False
    self.speed_ms_last = self.speed_ms
    self.speed_ms = CS.vEgoCluster

  def update(self, CS, main_buttons):
    if self.available_timer > 0:
      self.available_timer -= 1
      if self.available_timer == 0:
        self.available = True

    btn, long_pressed, double_pressed = self.btn_handler.update(CS.buttonEvents)

    if btn != ButtonType.unknown:
      self._button_press(CS, btn, long_pressed, double_pressed)

    self._main_button_toggle(main_buttons[-1])

    if not self.available:
      self._reset_speed(CS)

    if not self.prev_brake_pressed and CS.brakePressed:
      self._reset_speed(CS)
    self.prev_brake_pressed = CS.brakePressed

    if CS.gearShifter == GearShifter.park:
      self._reset_speed(CS)

    CS.cruiseState.available = self.available
    CS.cruiseState.enabled = self.enabled
    CS.cruiseState.standstill = False
    CS.cruiseState.speed = float(self.speed_ms)

  def _main_button_toggle(self, current_main_button: bool) -> None:
    if current_main_button != self.prev_main_button and current_main_button:
      self.available = not self.available
    self.prev_main_button = current_main_button

  def _button_press(self, CS, btn, long_pressed, double_pressed):
    speed_limiter = SpeedLimiter.instance()
    speed_limiter.recv()
    nda_active = bool(speed_limiter.get_active())
    button_limit, enforcement_limit = _get_button_limit(speed_limiter, CS)
    is_school_zone = speed_limiter.get_in_school_zone() if nda_active else CS.exState.roadSigns == 1

    v_cruise_delta = 10 if self.conv.is_metric else IMPERIAL_INCREMENT * 5
    v_cruise_kph = int(round(self.conv.to_clu(self.speed_ms)))
    cluster_speed_clu = self.conv.to_clu(CS.vEgoCluster)

    if btn == ButtonType.accelCruise:
      if self.enabled:
        if double_pressed:
          if button_limit > 0:
            if enforcement_limit or is_school_zone:
              v_cruise_kph = button_limit
            else:
              ratio = np.interp(button_limit, [self.conv.to_current_unit(10.0), self.conv.to_current_unit(100.0)],
                                [1.30, 1.10])
              v_cruise_kph = button_limit * ratio
        elif not long_pressed:
          v_cruise_kph += (1 if self.conv.is_metric else IMPERIAL_INCREMENT)
        else:
          v_cruise_kph += (v_cruise_delta - v_cruise_kph % v_cruise_delta)
      elif not self.enabled and self.available and CS.gearShifter != GearShifter.park:
        self.enabled = True
        v_cruise_kph = max(np.clip(round(self.conv.to_clu(self.speed_ms_last)), V_CRUISE_INITIAL, V_CRUISE_MAX),
                           round(cluster_speed_clu))

    if btn == ButtonType.decelCruise:
      if self.enabled:
        if double_pressed:
          if button_limit > 0:
            v_cruise_kph = button_limit
        elif not long_pressed:
          v_cruise_kph -= (1 if self.conv.is_metric else IMPERIAL_INCREMENT)
        else:
          v_cruise_kph -= (v_cruise_delta - (-v_cruise_kph) % v_cruise_delta)
      elif not self.enabled and self.available and CS.gearShifter != GearShifter.park:
        self.enabled = True
        v_cruise_kph = max(np.clip(round(cluster_speed_clu), V_CRUISE_MIN, V_CRUISE_MAX),
                           V_CRUISE_INITIAL)

    if btn == ButtonType.gapAdjustCruise:
      if long_pressed:
        current_exp_mode = self.params.get_bool("ExperimentalMode")
        self.params.put_bool("ExperimentalMode", not current_exp_mode, block=False)

    if btn == ButtonType.cancel:
      if not long_pressed:
        self._reset_speed(CS)
      else:
        self._reset_available()
        self._reset_speed(CS)

    """
    if btn == ButtonType.lfaButton:
      if not long_pressed:
        if button_limit > 0:
          if self.enabled:
            v_cruise_kph = button_limit
          elif not self.enabled and self.available and CS.gearShifter != GearShifter.park:
            self.enabled = True
            v_cruise_kph = button_limit
      else:
        self._reset_available()
        self._reset_speed(CS)
    """

    v_cruise_kph = np.clip(round(v_cruise_kph), V_CRUISE_MIN, V_CRUISE_MAX)
    self.speed_ms = self.conv.to_ms(v_cruise_kph)
