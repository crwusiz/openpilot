from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

from openpilot.common.filter_simple import StreamingMovingAverage
from openpilot.common.constants import UnitConverter


# Pull the virtual stop line toward the car at speed so the stationary-obstacle
# constraint starts the stop before the model endpoint becomes urgent. Preserve
# the historical high-speed correction, but fade it out inside 50 m so the final
# stopping position remains model-based.
TRAFFIC_STOP_DISTANCE_RATIO_SPEED_BP_KPH = (0.0, 100.0)
TRAFFIC_STOP_DISTANCE_RATIO = (1.0, 0.7)
TRAFFIC_STOP_DISTANCE_FADE_BP_M = (0.0, 50.0)
TRAFFIC_STOP_ENTRY_STEERING_LIMIT_DEG = 50.0
TRAFFIC_STOP_SOFT_DECEL_MPS2 = 2.2
TRAFFIC_STOP_MAX_DECEL_MPS2 = 4.0
TRAFFIC_STOP_RESPONSE_TIME_S = 0.5
TRAFFIC_STOP_DISTANCE_UNCERTAINTY_M = 5.0
TRAFFIC_STOP_DECEL_SAFETY_BUFFER_MPS2 = 0.2
TRAFFIC_STOP_DISTANCE_STABILITY_SAMPLES = 8  # 0.4 s at the 20 Hz model rate
TRAFFIC_STOP_INACTIVE_DISTANCE_M = 1000.0
TRAFFIC_STOP_LEAD_DISTANCE_MARGIN_M = 2.0
TRAFFIC_STOP_PREPARE_SPEED_KPH = 5.0
TRAFFIC_STOP_PREPARE_DISTANCE_M = 5.0


class XState(Enum):
  lead = 0
  cruise = 1
  e2eCruise = 2
  e2eStop = 3
  e2ePrepare = 4
  e2eStopped = 5


class TrafficState(Enum):
  off = 0
  red = 1
  green = 2


@dataclass(frozen=True)
class TrafficStopPlan:
  raw_stop_distance: float
  stop_distance: float
  signal_stop_active: bool


class TrafficStopDistanceTracker:
  """Hold transient near stop-line estimates while accounting for ego motion."""

  def __init__(self, sample_count: int = TRAFFIC_STOP_DISTANCE_STABILITY_SAMPLES):
    self._sample_count = max(1, int(sample_count))
    self._world_candidates = deque(maxlen=self._sample_count)
    self._distance_traveled = 0.0

  def reset(self) -> None:
    self._world_candidates.clear()
    self._distance_traveled = 0.0

  def update(self, model_distance: float, ego_distance: float) -> float:
    ego_distance = float(ego_distance)
    if np.isfinite(ego_distance):
      self._distance_traveled += max(0.0, ego_distance)

    model_distance = float(model_distance)
    if np.isfinite(model_distance):
      # Convert every candidate to the same fixed world coordinate. The largest
      # recent candidate rejects a one-frame closer estimate, while a persistent
      # closer line is accepted as soon as the older samples leave the window.
      self._world_candidates.append(self._distance_traveled + max(0.0, model_distance))

    if not self._world_candidates:
      return 0.0
    return max(0.0, max(self._world_candidates) - self._distance_traveled)


def is_traffic_stop_entry_allowed(steering_angle_deg: float) -> bool:
  """Allow steering angle to suppress only entry into a new signal stop."""
  return abs(float(steering_angle_deg)) < TRAFFIC_STOP_ENTRY_STEERING_LIMIT_DEG


def get_traffic_stop_reference_speed(v_ego_kph: float, previous_reference_kph: float | None) -> float:
  """Latch the highest speed seen during a signal stop so its distance advance does not relax."""
  return max(0.0, float(v_ego_kph), float(previous_reference_kph or 0.0))


def get_virtual_traffic_stop_distance(model_distance: float, v_ego_kph: float) -> float:
  """Return the model stop distance with a bounded, near-line-fading advance."""
  model_distance = max(0.0, float(model_distance))
  v_ego_kph = max(0.0, float(v_ego_kph))

  distance_ratio = float(np.interp(
    v_ego_kph,
    TRAFFIC_STOP_DISTANCE_RATIO_SPEED_BP_KPH,
    TRAFFIC_STOP_DISTANCE_RATIO,
  ))
  applied_ratio = float(np.interp(
    model_distance,
    TRAFFIC_STOP_DISTANCE_FADE_BP_M,
    (1.0, distance_ratio),
  ))
  return max(0.0, model_distance * applied_ratio)


def get_traffic_stop_obstacle_distance(stop_distance: float, distance_adjust: float) -> float:
  """Apply the configured stop-line correction without placing an obstacle behind the ego."""
  return max(0.0, float(stop_distance) + float(distance_adjust))


def get_traffic_stop_accel_floor(v_ego: float, raw_stop_distance: float, stop_distance: float) -> float:
  """Return a comfortable signal-stop accel floor that releases as stopping margin shrinks."""
  values = (v_ego, raw_stop_distance, stop_distance)
  if not all(np.isfinite(value) for value in values):
    return -TRAFFIC_STOP_MAX_DECEL_MPS2

  v_ego = max(0.0, float(v_ego))
  available_distance = (
    float(raw_stop_distance)
    - max(0.0, float(stop_distance))
    - v_ego * TRAFFIC_STOP_RESPONSE_TIME_S
    - TRAFFIC_STOP_DISTANCE_UNCERTAINTY_M
  )
  if available_distance <= 0.0:
    return -TRAFFIC_STOP_MAX_DECEL_MPS2

  required_decel = v_ego ** 2 / (2.0 * available_distance)
  allowed_decel = np.clip(
    max(TRAFFIC_STOP_SOFT_DECEL_MPS2, required_decel + TRAFFIC_STOP_DECEL_SAFETY_BUFFER_MPS2),
    TRAFFIC_STOP_SOFT_DECEL_MPS2,
    TRAFFIC_STOP_MAX_DECEL_MPS2,
  )
  return -float(allowed_decel)


def should_limit_traffic_stop_accel(signal_stop_active: bool, mpc_source: str) -> bool:
  """Limit signal braking unless a real lead obstacle is the active MPC source."""
  return bool(signal_stop_active) and mpc_source in ("cruise", "e2e")


class TrafficStopController:
  """Detect traffic-signal stops and produce the virtual MPC stop obstacle."""

  def __init__(self, dt: float):
    self.dt = float(dt)
    self.distance_tracker = TrafficStopDistanceTracker()
    self.conv = UnitConverter()
    self.v_filter = StreamingMovingAverage(10)
    self.traffic_state = TrafficState.off
    self.x_state = XState.cruise
    self.x_stop = 0.0
    self.stopping_count = 0
    self.traffic_starting_count = 0
    self.start_sign_count = 0
    self.stop_sign_count = 0
    self.adjusted_stop_distance = 0.0
    self.reference_speed_kph: float | None = None
    self.reset()

  def reset(self) -> None:
    self.distance_tracker.reset()
    self.v_filter = StreamingMovingAverage(10)
    self.traffic_state = TrafficState.off
    self.x_state = XState.cruise
    self.x_stop = 0.0
    self.stopping_count = 0
    self.traffic_starting_count = 0
    self.start_sign_count = 0
    self.stop_sign_count = 0
    self.adjusted_stop_distance = 0.0
    self.reference_speed_kph: float | None = None

  def update(self, car_state, model, radar_state, comfort_brake: float) -> TrafficStopPlan:
    v_ego = max(0.0, float(car_state.vEgo))
    v_ego_kph = self.conv.to_kph(self.conv.to_clu(v_ego))
    ego_distance = v_ego * self.dt

    self.x_stop = self.distance_tracker.update(model.position.x[31], ego_distance)
    raw_stop_distance = self.x_stop

    lead_present = bool(radar_state.leadOne.present)
    lead_distance = float(radar_state.leadOne.dRel) if lead_present else TRAFFIC_STOP_INACTIVE_DISTANCE_M
    self._check_model_stopping(model.velocity.x, v_ego_kph, model.position.x[-1], model.position.y, lead_distance)

    filtered_stop_distance = self.x_stop

    if self.x_state == XState.e2eStopped:
      self.stopping_count = max(0, int(self.stopping_count) - 1)
      if car_state.gasPressed:
        self.x_state = XState.e2ePrepare
      elif lead_present and (lead_distance - filtered_stop_distance) < TRAFFIC_STOP_LEAD_DISTANCE_MARGIN_M:
        self.x_state = XState.lead
      elif self.stopping_count == 0 and self.traffic_state == TrafficState.green and not car_state.leftBlinker:
        self.x_state = XState.e2ePrepare

    elif self.x_state == XState.e2eStop:
      self.stopping_count = 0
      if car_state.gasPressed:
        self.x_state = XState.e2eCruise
        self.traffic_starting_count = 10.0 / self.dt
      elif lead_present and (lead_distance - filtered_stop_distance) < TRAFFIC_STOP_LEAD_DISTANCE_MARGIN_M:
        self.x_state = XState.lead
      elif self.traffic_state == TrafficState.green:
        self.x_state = XState.e2eCruise
      else:
        reference_speed_kph = get_traffic_stop_reference_speed(v_ego_kph, self.reference_speed_kph)
        self.reference_speed_kph = reference_speed_kph
        stop_distance = get_virtual_traffic_stop_distance(self.x_stop, reference_speed_kph)
        if stop_distance > 10.0:
          self.adjusted_stop_distance = stop_distance
        filtered_stop_distance = 0.0
        if v_ego < 0.3:
          self.stopping_count = int(0.5 / self.dt)
          self.x_state = XState.e2eStopped

    elif self.x_state == XState.e2ePrepare:
      if lead_present:
        self.x_state = XState.lead
      elif v_ego_kph < TRAFFIC_STOP_PREPARE_SPEED_KPH and self.traffic_state != TrafficState.green:
        self.x_state = XState.e2eStop
        self.adjusted_stop_distance = TRAFFIC_STOP_PREPARE_DISTANCE_M
      elif v_ego_kph > TRAFFIC_STOP_PREPARE_SPEED_KPH:
        self.x_state = XState.e2eCruise

    else:  # XState.lead, XState.cruise, XState.e2eCruise
      self.traffic_starting_count = max(0, self.traffic_starting_count - 1)
      if lead_present:
        self.x_state = XState.lead
      elif (self.traffic_state == TrafficState.red and
            is_traffic_stop_entry_allowed(car_state.steeringAngleDeg) and
            self.traffic_starting_count == 0):
        self.x_state = XState.e2eStop
        reference_speed_kph = get_traffic_stop_reference_speed(v_ego_kph, None)
        self.reference_speed_kph = reference_speed_kph
        self.adjusted_stop_distance = get_virtual_traffic_stop_distance(self.x_stop, reference_speed_kph)
      else:
        self.x_state = XState.e2eCruise

    signal_stop_active = self.x_state in (XState.e2eStop, XState.e2eStopped)
    if self.traffic_state in (TrafficState.off, TrafficState.green) or not signal_stop_active:
      filtered_stop_distance = TRAFFIC_STOP_INACTIVE_DISTANCE_M

    self.adjusted_stop_distance = max(0.0, self.adjusted_stop_distance - ego_distance)

    if filtered_stop_distance == TRAFFIC_STOP_INACTIVE_DISTANCE_M:
      self.adjusted_stop_distance = 0.0
      self.reference_speed_kph = None
    elif self.adjusted_stop_distance > 0.0:
      filtered_stop_distance = 0.0

    stop_distance = filtered_stop_distance + self.adjusted_stop_distance
    stop_distance = max(stop_distance, v_ego ** 2 / (2.0 * float(comfort_brake)))
    return TrafficStopPlan(raw_stop_distance, stop_distance, signal_stop_active)

  def _check_model_stopping(self, velocity, v_ego_kph: float, model_x: float, y, lead_distance: float) -> None:
    model_v = self.v_filter.process(velocity[-1])
    start_sign = model_v > 5.0 or model_v > (velocity[0] + 2.0)

    stop_sign = False
    if v_ego_kph < 1.0:
      stop_sign = model_x < 20.0 and model_v < 10.0
    elif v_ego_kph < 82.0:
      stop_distance_threshold = np.interp(velocity[0], [60.0 / 3.6, 80.0 / 3.6], [120.0, 150.0])
      stop_sign = (model_x < lead_distance - 3.0 and
                   model_x < stop_distance_threshold and
                   (model_v < 3.0 or model_v < velocity[0] * 0.7) and
                   abs(y[-1]) < 5.0)

    self.stop_sign_count = min(20, self.stop_sign_count + 1) if stop_sign else max(0, self.stop_sign_count - 2)
    self.start_sign_count = min(20, self.start_sign_count + 1) if start_sign and not stop_sign else max(0, self.start_sign_count - 2)

    if self.stop_sign_count > 3:
      self.traffic_state = TrafficState.red
    elif self.start_sign_count > 5:
      self.traffic_state = TrafficState.green
    elif self.stop_sign_count == 0 and self.start_sign_count == 0:
      self.traffic_state = TrafficState.off
