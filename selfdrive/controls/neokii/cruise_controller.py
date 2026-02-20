import math
import random
import threading
import numpy as np

from opendbc.car import structs
from openpilot.common.params import Params
from openpilot.common.constants import UnitConverter
from openpilot.selfdrive.car.cruise import (V_CRUISE_MIN, V_CRUISE_MAX, V_CRUISE_UNSET, V_CRUISE_INITIAL, V_CRUISE_INITIAL_EXPERIMENTAL_MODE,
                                            CRUISE_LONG_PRESS, IMPERIAL_INCREMENT)
from opendbc.car.hyundai.values import Buttons, HyundaiFlags
from openpilot.selfdrive.controls.neokii.navi_controller import SpeedLimiter

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

NO_LIMIT_SPEED = 255.
SCHOOL_ZONE_SPEED = 30.0

ButtonType = structs.CarState.ButtonEvent.Type
GearShifter = structs.CarState.GearShifter


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
          #ButtonType.lfaButton
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
    self.min_set_speed_clu = self.conv.to_current_unit(V_CRUISE_MIN) if CruiseStateManager.instance().cruise_state_control else self.conv.to_current_unit(V_CRUISE_INITIAL)
    self.max_set_speed_clu = self.conv.to_current_unit(V_CRUISE_MAX)

    self.btn = Buttons.NONE
    self.override_speed_clu = 0.
    self.apply_limit_speed_clu = 0.
    self.curve_speed_clu = 0.
    self.cruise_speed_kph = 0.
    self.real_set_speed_kph = 0.
    self.v_cruise_kph_last = 0.
    self.road_limit_speed_clu = 0.
    self.camera_limit_speed_clu = 0.
    self.steer_limit_speed_clu = 0.
    self.lead_limit_speed_clu = 0.
    self.prev_steering_angle = 0.
    self.prev_cruise_enabled = False
    self.steer_decel_active = False
    self.v_cruise_kph = V_CRUISE_UNSET
    self.v_cruise_cluster_kph = V_CRUISE_UNSET

    self.gas_pressed_count = 0
    self.ignore_road_limit_temporarily = False
    self.ignore_limit_timer = 0

    self.wait_timer = 0
    self.alive_timer = 0
    self.alive_index = 0
    self.wait_index = 0
    self.alive_count = 0
    self.wait_count_list = [16] if CP.flags & HyundaiFlags.CANFD else [16, 20]
    self.alive_count_list = [20] if CP.flags & HyundaiFlags.CANFD else [12, 14, 16, 18]
    random.shuffle(self.wait_count_list)
    random.shuffle(self.alive_count_list)

  def _get_alive_count(self):
    count = self.alive_count_list[self.alive_index]
    self.alive_index = (self.alive_index + 1) % len(self.alive_count_list)
    return count

  def _get_wait_count(self):
    count = self.wait_count_list[self.wait_index]
    self.wait_index = (self.wait_index + 1) % len(self.wait_count_list)
    return count

  def reset(self):
    self.btn = Buttons.NONE
    self.wait_timer = 0
    self.alive_timer = 0
    self.override_speed_clu = 0.
    self.apply_limit_speed_clu = 0.
    self.curve_speed_clu = 0.
    self.gas_pressed_count = 0
    self.ignore_road_limit_temporarily = False
    self.ignore_limit_timer = 0

  def _cal_limit_speed(self, CS, sm, current_speed_ms: float, cluster_speed_clu: float, v_cruise_kph: float):
    nda_active = SpeedLimiter.instance().get_active()
    road_limit_speed_nda = SpeedLimiter.instance().get_road_limit_speed()
    road_limit_speed_stock = CS.exState.navLimitSpeed
    road_signs = CS.exState.roadSigns
    school_zone = road_signs == 1
    is_limit_zone = False
    lead = sm['radarState'].leadOne
    model = sm['modelV2']

    # 1. Road limit speed
    road_limit_speed = None
    if nda_active and road_limit_speed_nda is not None and road_limit_speed_nda > 0:
      road_limit_speed = road_limit_speed_nda
    elif road_limit_speed_stock is not None and road_limit_speed_stock > 0:
      road_limit_speed = road_limit_speed_stock

    ratio = np.interp(road_limit_speed, [self.conv.to_current_unit(10.0), self.conv.to_current_unit(100.0)], [1.30, 1.10])
    road_limit_speed_clu = road_limit_speed * ratio if road_limit_speed else NO_LIMIT_SPEED

    if self.ignore_road_limit_temporarily:
      self.ignore_limit_timer += 1
      timeout_ticks = 2000

      # 1. 스쿨존은 즉시 안전 복귀
      if school_zone:
          self.ignore_road_limit_temporarily = False
          self.ignore_limit_timer = 0

      # 2. 1분 타임아웃 발생 시 로직
      elif self.ignore_limit_timer > timeout_ticks:
          self.ignore_road_limit_temporarily = False
          self.ignore_limit_timer = 0

          if road_limit_speed_clu != NO_LIMIT_SPEED and v_cruise_kph < road_limit_speed_clu:
               self.v_cruise_kph = road_limit_speed_clu
               self.real_set_speed_kph = road_limit_speed_clu
               if CruiseStateManager.instance().cruise_state_control:
                    CruiseStateManager.instance().speed_ms = self.conv.to_ms(road_limit_speed_clu)
      else:
        road_limit_speed_clu = NO_LIMIT_SPEED

    # 2. Camera limit speed
    camera_limit_speed_clu = NO_LIMIT_SPEED
    if nda_active:
      camera_limit_speed, is_limit_zone = (
        SpeedLimiter.instance().get_max_speed(cluster_speed_clu))
      section_limit_speed, section_left_dist = SpeedLimiter.instance().get_section_limit_speed()
      if section_limit_speed > 0 and section_left_dist > 0:
        camera_limit_speed_clu = section_limit_speed
      else:
        camera_limit_speed_clu = camera_limit_speed
    elif CS is not None and CS.speedLimit > 0 and CS.speedLimitDistance > 0:
      camera_limit_speed_stock, is_limit_zone = (
        SpeedLimiter.instance().get_camera_limit_speed_stock(CS, cluster_speed_clu))
      camera_limit_speed_clu = camera_limit_speed_stock

    if school_zone:
      limit_50 = self.conv.to_current_unit(50.0)
      if 0 < camera_limit_speed_clu < NO_LIMIT_SPEED:
        camera_limit_speed_clu = min(camera_limit_speed_clu, limit_50)
      elif road_limit_speed is not None and road_limit_speed > 0:
        camera_limit_speed_clu = min(road_limit_speed, limit_50)
      else:
        camera_limit_speed_clu = self.conv.to_current_unit(SCHOOL_ZONE_SPEED)
    self.camera_limit_speed_clu = camera_limit_speed_clu

    if school_zone and road_limit_speed_clu == self.conv.to_current_unit(SCHOOL_ZONE_SPEED):
      if camera_limit_speed_clu > road_limit_speed_clu:
        road_limit_speed_clu = camera_limit_speed_clu
      else:
        road_limit_speed_clu = min(road_limit_speed_clu, camera_limit_speed_clu)

    self.road_limit_speed_clu = road_limit_speed_clu

    # 3. Lead limit speed
    lead_speed = self._cal_lead_speed(lead, cluster_speed_clu)
    lead_limit_speed_clu = lead_speed if self.CP.openpilotLongitudinalControl and lead.status else NO_LIMIT_SPEED
    self.lead_limit_speed_clu = lead_limit_speed_clu

    # 4. Curve limit speed
    curve_limit_speed_clu = self._cal_curve_speed_adaptive(model, current_speed_ms, v_cruise_kph)
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

    valid_limits = [s for s in speed_candidates if s >= self.min_set_speed_clu and s != NO_LIMIT_SPEED]
    calculated_max_speed_clu = min(v_cruise_kph, min(valid_limits)) if valid_limits else self.apply_limit_speed_clu

    if valid_limits:
      is_curve_limit = (curve_limit_speed_clu != NO_LIMIT_SPEED and curve_limit_speed_clu == min(valid_limits))
    else:
      is_curve_limit = False

    immediate_apply_conditions = [
      not self.CP.openpilotLongitudinalControl,
      self.apply_limit_speed_clu <= 0,
      is_limit_zone,
      is_curve_limit
    ]

    if any(immediate_apply_conditions):
      self.apply_limit_speed_clu = calculated_max_speed_clu
    else:
      error = calculated_max_speed_clu - self.apply_limit_speed_clu
      kp = np.interp(abs(error), [0, 2, 5, 10], [0.01, 0.05, 0.10, 0.20])
      self.apply_limit_speed_clu += error * kp

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
      return NO_LIMIT_SPEED

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
      return NO_LIMIT_SPEED, 0.0

    curvatures = np.abs(self._calculate_curvature(x_positions, y_positions))
    curv_segment = curvatures[-10:]
    curv_variance = np.var(curv_segment)
    trajectory_length = np.sum(np.sqrt(np.diff(x_positions) ** 2 + np.diff(y_positions) ** 2))
    confidence = min(1.0, trajectory_length / 100.0) * (1.0 / (1.0 + curv_variance * 1000))

    a_y_max = 2.975 - current_speed_ms * 0.0375
    current_curve_speed_ms = float(np.mean(np.sqrt(a_y_max / np.clip(curv_segment, 1e-4, None)))) * 0.85

    current_model_speed = float(max(current_curve_speed_ms, min_curve_speed_ms)) \
      if not math.isnan(current_curve_speed_ms) and current_curve_speed_ms < current_speed_ms else NO_LIMIT_SPEED

    lookahead_distance = current_speed_ms * 5.0
    lookahead_indices = (x_positions <= lookahead_distance) & (x_positions > current_speed_ms * 0.5)

    predictive_speed = NO_LIMIT_SPEED

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

    if predictive_speed != NO_LIMIT_SPEED:
      if current_model_speed != NO_LIMIT_SPEED:
        model_based_speed = min(current_model_speed, predictive_speed)
      else:
        model_based_speed = predictive_speed
    else:
      model_based_speed = current_model_speed

    return model_based_speed, confidence

  def _get_acc_based_speed(self, model, current_speed_ms: float, min_curve_speed_ms: float):
    orientation_rate = np.array(model.orientationRate.z)
    velocity = np.array(model.velocity.x)

    predicted_lat_acc = float(np.max(np.abs(orientation_rate * velocity)))
    acc_based_curvature = predicted_lat_acc / max(current_speed_ms, 1.0) ** 2

    orientation_stability = 1.0 - min(1.0, np.std(orientation_rate) / (np.mean(np.abs(orientation_rate)) + 1e-6))
    velocity_stability = 1.0 - min(1.0, np.std(velocity) / (np.mean(velocity) + 1e-6))

    speed_factor = 1.0 if current_speed_ms < self.conv.to_ms(30.0) else 0.7
    confidence = (orientation_stability + velocity_stability) / 2.0 * speed_factor

    a_y_max = 2.975 - current_speed_ms * 0.0375
    acc_speed_ms = np.sqrt(a_y_max / np.clip(acc_based_curvature, 1e-4, None)) * 0.85

    acc_based_speed = float(max(acc_speed_ms, min_curve_speed_ms)) \
      if not math.isnan(acc_speed_ms) and acc_speed_ms < current_speed_ms else NO_LIMIT_SPEED

    return acc_based_speed, confidence

  def _cal_curve_speed_adaptive(self, model, current_speed_ms: float, v_cruise_kph: float):
    min_curve_speed_ms = max(self.conv.to_ms(30.0), current_speed_ms * 0.5)
    model_speed, model_confidence = self._get_model_based_speed(model, current_speed_ms, min_curve_speed_ms)
    acc_speed, acc_confidence = self._get_acc_based_speed(model, current_speed_ms, min_curve_speed_ms)

    model_weight = np.interp(current_speed_ms,
                             [self.conv.to_ms(30.0), self.conv.to_ms(60.0), self.conv.to_ms(100.0)],
                             [0.3, 0.5, 0.7])
    acc_weight = 1.0 - model_weight

    total_confidence = model_confidence + acc_confidence
    if total_confidence > 0:
      model_weight *= model_confidence
      acc_weight *= acc_confidence

      total_weight = model_weight + acc_weight
      if total_weight > 0:
        model_weight /= total_weight
        acc_weight /= total_weight

    valid_speeds = [(speed, weight) for speed, weight in
                    [(model_speed, model_weight), (acc_speed, acc_weight)]
                    if speed != NO_LIMIT_SPEED]

    calculated_curve_speed_ms = (
      sum(s * w for s, w in valid_speeds) / sum(w for _, w in valid_speeds)
      if valid_speeds else NO_LIMIT_SPEED
    )

    if calculated_curve_speed_ms != NO_LIMIT_SPEED:
      speed_reduction_ratio = current_speed_ms / calculated_curve_speed_ms
      if speed_reduction_ratio > 1.3:
        calculated_curve_speed_ms *= 0.9

    curve_speed_ms = min(calculated_curve_speed_ms, self.conv.to_ms(v_cruise_kph))
    curve_speed_clu = self.conv.to_clu(curve_speed_ms)

    return curve_speed_clu

  def _cal_steer_based_speed(self, current_speed_ms: float, steering_angle_deg: float):
    start_decel_angle = 45.
    end_decel_angle = 120.
    abs_steer_angle = abs(steering_angle_deg)

    if abs_steer_angle < start_decel_angle:
      self.steer_decel_active = False
      return NO_LIMIT_SPEED

    if hasattr(self, 'prev_steering_angle'):
      angle_change_rate = abs(abs_steer_angle - abs(self.prev_steering_angle))
      if not getattr(self, 'steer_decel_active', False):
        if angle_change_rate > 5.0 or abs_steer_angle > 60:
          self.steer_decel_active = True
        else:
          self.prev_steering_angle = abs_steer_angle
          return NO_LIMIT_SPEED
    else:
      self.steer_decel_active = True

    self.prev_steering_angle = abs_steer_angle

    if self.steer_decel_active:
      speed_multiplier = np.interp(abs_steer_angle, [start_decel_angle, end_decel_angle], [0.95, 0.75])
      target_speed_ms = current_speed_ms * speed_multiplier
      min_allowed_speed_ms = self.conv.to_ms(20)
      steer_based_speed_ms = max(target_speed_ms, min_allowed_speed_ms)
      steer_limit_speed_clu = self.conv.to_clu(steer_based_speed_ms)

      return steer_limit_speed_clu

    return NO_LIMIT_SPEED

  def _override_speed(self, CS, cluster_speed_clu: float, v_cruise_kph: float, cruise_btn_pressed: bool):
    syncing = CS.gasPressed and not cruise_btn_pressed
    sync_margin = 3.

    if not self.CP.openpilotLongitudinalControl:
      if syncing and cluster_speed_clu + sync_margin > self.conv.to_current_unit(v_cruise_kph):
        set_speed = np.clip(cluster_speed_clu + sync_margin, V_CRUISE_INITIAL, self.max_set_speed_clu)
        v_cruise_kph = int(round(self.conv.to_current_unit(set_speed)))

      self.override_speed_clu = self.conv.to_current_unit(v_cruise_kph)
      if self.apply_limit_speed_clu > V_CRUISE_INITIAL:
        self.override_speed_clu = np.clip(self.override_speed_clu, V_CRUISE_INITIAL, self.apply_limit_speed_clu)

    elif CS.cruiseState.enabled:
      if syncing:
        self.gas_pressed_count += 1
        if self.gas_pressed_count > 100:
          v_cruise_kph = int(round(cluster_speed_clu))
          self.ignore_road_limit_temporarily = True
          self.ignore_limit_timer = 0

        if cluster_speed_clu + sync_margin > self.conv.to_current_unit(v_cruise_kph):
          set_speed = np.clip(cluster_speed_clu + sync_margin, self.min_set_speed_clu, self.max_set_speed_clu)
          self.override_speed_clu = int(round(self.conv.to_current_unit(set_speed)))

          if CruiseStateManager.instance().cruise_state_control:
            CruiseStateManager.instance().speed_ms = self.conv.to_ms(set_speed)
      else:
        self.gas_pressed_count = 0

    return v_cruise_kph

  def _get_button_to_adjust_speed(self, current_set_speed: float) -> Buttons:
    if self.override_speed_clu < V_CRUISE_INITIAL:
      return Buttons.NONE
    error = self.override_speed_clu - current_set_speed
    if abs(error) < 0.9:
      return Buttons.NONE

    return Buttons.RES_ACCEL if error > 0 else Buttons.SET_DECEL

  def _initialize_v_cruise(self, CS):
    initial = V_CRUISE_INITIAL_EXPERIMENTAL_MODE if self.experimental_mode else V_CRUISE_INITIAL

    if any(b.type in (ButtonType.accelCruise, ButtonType.resumeCruise) for b in
           CS.buttonEvents) and self.v_cruise_kph != V_CRUISE_UNSET:
      self.v_cruise_kph = self.v_cruise_kph_last
    else:
      self.v_cruise_kph = int(round(np.clip(self.conv.to_clu(CS.vEgo), initial, V_CRUISE_MAX)))

    self.v_cruise_cluster_kph = self.v_cruise_kph

    return self.v_cruise_kph

  def update_v_cruise(self, CS, sm, enabled: bool):
    self.v_cruise_kph_last = self.v_cruise_kph
    v_cruise_kph = self.v_cruise_kph
    current_speed_ms = CS.vEgo
    cluster_speed_clu = self.conv.to_clu(CS.vEgoCluster)

    if CS.cruiseState.enabled:
      if not self.CP.openpilotLongitudinalControl or not self.CP.pcmCruise:
        v_cruise_kph = self._update_cruise_button(v_cruise_kph, CS.buttonEvents, enabled)
      else:
        v_cruise_kph = self.conv.to_clu(CS.cruiseState.speed)
        if CS.cruiseState.speed == 0:
          v_cruise_kph = V_CRUISE_UNSET
        elif CS.cruiseState.speed == -1:
          v_cruise_kph = -1
    else:
      v_cruise_kph = V_CRUISE_UNSET

    if self.prev_cruise_enabled != CS.cruiseState.enabled:
      self.prev_cruise_enabled = CS.cruiseState.enabled
      if CS.cruiseState.enabled:
        if not self.CP.pcmCruise:
          v_cruise_kph = self._initialize_v_cruise(CS)
        else:
          v_cruise_kph = self.conv.to_clu(CS.cruiseState.speed)
          if CS.cruiseState.speed == 0:
            v_cruise_kph = V_CRUISE_UNSET
          elif CS.cruiseState.speed == -1:
            v_cruise_kph = -1

    self.real_set_speed_kph = v_cruise_kph
    if CS.cruiseState.enabled and 1 < CS.cruiseState.speed < V_CRUISE_UNSET:
      self._cal_limit_speed(CS, sm, current_speed_ms, cluster_speed_clu, v_cruise_kph)

      if self.v_cruise_kph != v_cruise_kph:
          v_cruise_kph = self.v_cruise_kph

      self.cruise_speed_kph = float(np.clip(v_cruise_kph, V_CRUISE_MIN, self.conv.to_current_unit(self.apply_limit_speed_clu)))
      v_cruise_kph_from_override = self._override_speed(CS, cluster_speed_clu, self.real_set_speed_kph, self.CI.CS.cruise_buttons[-1] != Buttons.NONE)

      if v_cruise_kph_from_override != self.real_set_speed_kph:
          self.real_set_speed_kph = v_cruise_kph_from_override
          v_cruise_kph = v_cruise_kph_from_override

      if CruiseStateManager.instance().cruise_state_control:
        self.cruise_speed_kph = min(self.cruise_speed_kph, max(self.real_set_speed_kph, V_CRUISE_MIN))
    else:
      self.cruise_speed_kph = cluster_speed_clu
      self.reset()

    self.v_cruise_kph = v_cruise_kph
    self._update_message(CS)

  def _update_cruise_button(self, v_cruise_kph, btn_events, enabled):
    v_cruise_delta = 10 if self.conv.is_metric else IMPERIAL_INCREMENT * 5

    if enabled:
      btn, long_pressed, double_pressed = self.btn_handler.update(btn_events)

      if btn != Buttons.NONE:
        if double_pressed and btn == ButtonType.accelCruise:
          if self.road_limit_speed_clu > 0:
            v_cruise_kph = self.road_limit_speed_clu
        elif not long_pressed:
          if btn == ButtonType.accelCruise:
            v_cruise_kph += (1 if self.conv.is_metric else IMPERIAL_INCREMENT)
          elif btn == ButtonType.decelCruise:
            v_cruise_kph -= (1 if self.conv.is_metric else IMPERIAL_INCREMENT)
        else:
          if btn == ButtonType.accelCruise:
            v_cruise_kph += (v_cruise_delta - v_cruise_kph % v_cruise_delta)
          elif btn == ButtonType.decelCruise:
            v_cruise_kph -= (v_cruise_delta - (-v_cruise_kph) % v_cruise_delta)

      v_cruise_kph = np.clip(round(v_cruise_kph), V_CRUISE_MIN, V_CRUISE_MAX)

    return v_cruise_kph

  def spam_message(self, CS, can_sends):
    ascc_enabled = CS.cruiseState.enabled and 1 < CS.cruiseState.speed < V_CRUISE_UNSET and not CS.brakePressed
    btn_pressed = self.CI.CS.cruise_buttons[-1] != Buttons.NONE

    if not self.CP.openpilotLongitudinalControl:
      if not ascc_enabled or btn_pressed:
        self.reset()
        self.wait_timer = max(self.alive_count_list) + max(self.wait_count_list)
        return

    if not ascc_enabled:
      self.reset()

    if self.wait_timer > 0:
      self.wait_timer -= 1
    elif ascc_enabled and CS.vEgo > 0.1:
      if self.alive_timer == 0:
        current_set_speed_clu = round(self.conv.to_clu(CS.cruiseState.speed))
        self.btn = self._get_button_to_adjust_speed(current_set_speed_clu)
        self.alive_count = self._get_alive_count()

      if self.btn != Buttons.NONE:
        can = self.CI.create_buttons(self.btn)
        if can is not None:
          can_sends.append(can)

        self.alive_timer += 1
        if self.alive_timer >= self.alive_count:
          self.alive_timer = 0
          self.wait_timer = self._get_wait_count()
          self.btn = Buttons.NONE
      elif self.CP.openpilotLongitudinalControl and self.override_speed_clu >= V_CRUISE_INITIAL:
        self.override_speed_clu = 0.
    elif self.CP.openpilotLongitudinalControl:
      self.override_speed_clu = 0.

  def _update_message(self, CS):
    exState = CS.exState
    exState.vCruiseKph = float(self.v_cruise_kph)
    exState.vEgo = float(self.conv.to_clu(CS.vEgo))
    exState.vEgoCluster = float(self.conv.to_clu(CS.vEgoCluster))
    exState.cruiseMaxSpeed = float(self.real_set_speed_kph)
    exState.applyMaxSpeed = float(self.cruise_speed_kph)
    exState.overrideSpeed = float(self.override_speed_clu)
    exState.curveSpeed = float(self.curve_speed_clu)
    exState.roadSpeed = float(self.road_limit_speed_clu)
    exState.cameraSpeed = float(self.camera_limit_speed_clu)
    exState.steerSpeed = float(self.steer_limit_speed_clu)
    exState.leadSpeed = float(self.lead_limit_speed_clu)
    exState.applyLimitSpeed = float(self.apply_limit_speed_clu)

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

  @classmethod
  def instance(cls):
    if not hasattr(cls, "_instance"):
      cls._instance = cls()
    return cls._instance

  def _reset_available(self):
    self.available = False
    threading.Timer(3.0, lambda: setattr(self, 'available', True)).start()

  def _reset_speed(self, CS):
    self.enabled = False
    self.speed_ms_last = self.speed_ms
    self.speed_ms = CS.vEgoCluster

  def update(self, CS, main_buttons):
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
    nda_active = SpeedLimiter.instance().get_active()
    road_limit_speed_nda = SpeedLimiter.instance().get_road_limit_speed()
    road_limit_speed_stock = CS.exState.navLimitSpeed

    road_limit_speed = None
    if nda_active and road_limit_speed_nda is not None and road_limit_speed_nda > 0:
      road_limit_speed = road_limit_speed_nda
    elif road_limit_speed_stock is not None and road_limit_speed_stock > 0:
      road_limit_speed = road_limit_speed_stock

    v_cruise_delta = 10 if self.conv.is_metric else IMPERIAL_INCREMENT * 5
    v_cruise_kph = int(round(self.conv.to_clu(self.speed_ms)))
    cluster_speed_clu = self.conv.to_clu(CS.vEgoCluster)

    if btn == ButtonType.accelCruise:
      if self.enabled:
        if double_pressed:
          if road_limit_speed is not None and road_limit_speed > 0:
            v_cruise_kph = road_limit_speed
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
          if road_limit_speed is not None and road_limit_speed > 0:
            v_cruise_kph = road_limit_speed
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
        self.params.put_bool("ExperimentalMode", not self.params.get_bool("ExperimentalMode"))

    if btn == ButtonType.cancel:
      if not long_pressed:
        self._reset_speed(CS)
      else:
        self._reset_available()
        self._reset_speed(CS)

    """
    if btn == ButtonType.lfaButton:
      if not long_pressed:
        if road_limit_speed is not None:
          if self.enabled:
            v_cruise_kph = road_limit_speed
          elif not self.enabled and self.available and CS.gearShifter != GearShifter.park:
            self.enabled = True
            v_cruise_kph = road_limit_speed
      else:
        self._reset_available()
        self._reset_speed(CS)
    """

    v_cruise_kph = np.clip(round(v_cruise_kph), V_CRUISE_MIN, V_CRUISE_MAX)
    self.speed_ms = self.conv.to_ms(v_cruise_kph)
