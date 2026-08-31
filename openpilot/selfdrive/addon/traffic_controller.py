from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import logging
from logging.handlers import RotatingFileHandler
import time

import numpy as np

from openpilot.common.constants import UnitConverter
from openpilot.common.filter_simple import StreamingMovingAverage


TRAFFIC_DEBUG_LOG = "/data/traffic_debug.log"
TRAFFIC_DEBUG_LOG_INTERVAL_S = 0.5


def _setup_traffic_logger() -> logging.Logger:
  logger = logging.getLogger("traffic")
  logger.setLevel(logging.DEBUG)
  logger.propagate = False

  if any(isinstance(handler, RotatingFileHandler) and handler.baseFilename == TRAFFIC_DEBUG_LOG
         for handler in logger.handlers):
    return logger

  try:
    handler = RotatingFileHandler(
      TRAFFIC_DEBUG_LOG,
      mode="a",
      maxBytes=10 * 1024 * 1024,
      backupCount=2,
    )
    handler.setFormatter(logging.Formatter(
      "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
      datefmt="%H:%M:%S",
    ))
    logger.addHandler(handler)
  except OSError:
    pass

  return logger


traffic_log = _setup_traffic_logger()


class XState(Enum):
  lead = 0
  cruise = 1
  e2eCruise = 2
  e2eStop = 3
  e2ePrepare = 4
  e2eStopped = 5


class TrafficState(Enum):
  """Compatibility state published to longitudinalPlan; inferred from model motion, not signal color."""
  off = 0
  red = 1
  green = 2


class ModelMotion(Enum):
  unknown = 0
  stopping = 1
  starting = 2


@dataclass(frozen=True)
class TrafficStopPlan:
  raw_stop_distance: float
  stop_distance: float
  signal_stop_active: bool


@dataclass(frozen=True)
class TrafficModelObservation:
  valid: bool
  initial_speed_ms: float
  terminal_speed_ms: float
  filtered_terminal_speed_ms: float
  terminal_distance: float
  lateral_offset: float
  stop_evidence: bool
  go_evidence: bool


class TrafficMotionDetector:
  """Convert model trajectory endpoints into time-qualified stop/go evidence."""

  _STOP_CONFIRM_S = 0.2
  _GO_CONFIRM_S = 1.0
  _EVIDENCE_DECAY_RATE = 2.0
  _STOP_DISTANCE_SPEED_BP_MS = (
    UnitConverter.kph_to_ms(60.0),
    UnitConverter.kph_to_ms(80.0),
  )

  def __init__(self, dt: float):
    self.dt = float(dt)
    self.v_filter = StreamingMovingAverage(10)
    self.motion = ModelMotion.unknown
    self.stop_evidence_time = 0.0
    self.go_evidence_time = 0.0

  def reset(self) -> None:
    self.v_filter = StreamingMovingAverage(10)
    self.motion = ModelMotion.unknown
    self.stop_evidence_time = 0.0
    self.go_evidence_time = 0.0

  @staticmethod
  def _invalid_observation() -> TrafficModelObservation:
    return TrafficModelObservation(False, np.nan, np.nan, np.nan, np.nan, np.nan, False, False)

  def update(self, velocity_ms, v_ego_kph: float, model_x: float, y, lead_distance: float) -> TrafficModelObservation:
    try:
      if len(velocity_ms) == 0 or len(y) == 0:
        return self._invalid_observation()
      initial_speed_ms = float(velocity_ms[0])
      terminal_speed_ms = float(velocity_ms[-1])
      terminal_distance = float(model_x)
      lateral_offset = float(y[-1])
      lead_distance = float(lead_distance)
    except (IndexError, TypeError, ValueError):
      return self._invalid_observation()

    if not all(np.isfinite(value) for value in
               (initial_speed_ms, terminal_speed_ms, terminal_distance, lateral_offset, lead_distance)):
      return self._invalid_observation()

    filtered_terminal_speed_ms = self.v_filter.process(terminal_speed_ms)
    is_stopped = v_ego_kph < 1.0

    stop_evidence = False
    if is_stopped:
      stop_evidence = terminal_distance < 20.0 and filtered_terminal_speed_ms < 10.0
    elif v_ego_kph < 82.0:
      stop_distance_threshold = np.interp(
        initial_speed_ms,
        self._STOP_DISTANCE_SPEED_BP_MS,
        (120.0, 150.0),
      )
      stop_evidence = (terminal_distance < lead_distance - 3.0 and
                       terminal_distance < stop_distance_threshold and
                       (filtered_terminal_speed_ms < 3.0 or
                        filtered_terminal_speed_ms < initial_speed_ms * 0.7) and
                       abs(lateral_offset) < 5.0)

    if is_stopped:
      go_speed_evidence = filtered_terminal_speed_ms > 5.0
    else:
      go_speed_evidence = (filtered_terminal_speed_ms > 5.0 or
                           filtered_terminal_speed_ms > initial_speed_ms + 2.0)
    go_evidence = (not stop_evidence and
                   terminal_distance >= 20.0 and
                   go_speed_evidence)

    observation = TrafficModelObservation(
      True,
      initial_speed_ms,
      terminal_speed_ms,
      filtered_terminal_speed_ms,
      terminal_distance,
      lateral_offset,
      stop_evidence,
      go_evidence,
    )
    self._update_motion(observation)
    return observation

  def _update_motion(self, observation: TrafficModelObservation) -> None:
    if not observation.valid:
      return

    self.stop_evidence_time = self._update_evidence_time(
      self.stop_evidence_time,
      observation.stop_evidence,
      self._STOP_CONFIRM_S,
    )
    self.go_evidence_time = self._update_evidence_time(
      self.go_evidence_time,
      observation.go_evidence,
      self._GO_CONFIRM_S,
    )

    if self.stop_evidence_time >= self._STOP_CONFIRM_S:
      self.motion = ModelMotion.stopping
    elif self.go_evidence_time >= self._GO_CONFIRM_S:
      self.motion = ModelMotion.starting
    elif self.stop_evidence_time == 0.0 and self.go_evidence_time == 0.0:
      self.motion = ModelMotion.unknown

  def _update_evidence_time(self, current: float, active: bool, maximum: float) -> float:
    if active:
      return min(maximum, current + self.dt)
    return max(0.0, current - self.dt * self._EVIDENCE_DECAY_RATE)


class TrafficStopDistanceTracker:
  """Hold transient near stop-line estimates while accounting for ego motion."""

  def __init__(self, sample_count: int = 8):  # 0.4 s at the 20 Hz model rate
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
  return abs(float(steering_angle_deg)) < 50.0


def get_traffic_stop_reference_speed(v_ego_kph: float, previous_reference_kph: float | None) -> float:
  """Latch the highest speed seen during a signal stop so its distance advance does not relax."""
  return max(0.0, float(v_ego_kph), float(previous_reference_kph or 0.0))


def get_virtual_traffic_stop_distance(model_distance: float, v_ego_kph: float) -> float:
  """Return the model stop distance with a bounded, near-line-fading advance."""
  model_distance = max(0.0, float(model_distance))
  v_ego_kph = max(0.0, float(v_ego_kph))

  # Advance the virtual line at speed, then fade the correction inside 50 m so
  # the final stopping position remains model-based.
  distance_ratio = float(np.interp(
    v_ego_kph,
    (0.0, 100.0),
    (1.0, 0.7),
  ))
  applied_ratio = float(np.interp(
    model_distance,
    (0.0, 50.0),
    (1.0, distance_ratio),
  ))
  return max(0.0, model_distance * applied_ratio)


def get_traffic_stop_obstacle_distance(stop_distance: float, mpc_stop_distance: float) -> float:
  """Convert an ego stop target to an MPC obstacle while retaining a small stop-line buffer."""
  obstacle_offset = max(0.0, float(mpc_stop_distance) - 1.0)
  return max(0.0, float(stop_distance) + obstacle_offset)


def get_traffic_stop_accel_floor(v_ego: float, raw_stop_distance: float, stop_distance: float) -> float:
  """Hold comfortable signal braking until the remaining distance becomes safety-critical."""
  # Positive deceleration magnitudes in m/s².
  soft_decel = 2.2
  max_decel = 4.0
  response_time_s = 0.5
  distance_uncertainty_m = 5.0
  decel_safety_buffer = 0.2
  decel_urgency_range = (4.0, 5.0)

  values = (v_ego, raw_stop_distance, stop_distance)
  if not all(np.isfinite(value) for value in values):
    return -max_decel

  v_ego = max(0.0, float(v_ego))
  available_distance = (
    float(raw_stop_distance)
    - max(0.0, float(stop_distance))
    - v_ego * response_time_s
    - distance_uncertainty_m
  )
  if available_distance <= 0.0:
    return -max_decel

  buffered_required_decel = v_ego ** 2 / (2.0 * available_distance) + decel_safety_buffer
  # position.x[-1] is a predicted trajectory endpoint, not a measured stop-line
  # distance. Do not increase braking continuously as that prediction contracts.
  # Keep the comfort floor through the normal margin range, then blend quickly
  # to the full safety limit only when the required decel is genuinely high.
  allowed_decel = np.interp(
    buffered_required_decel,
    decel_urgency_range,
    (soft_decel, max_decel),
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
    self.motion_detector = TrafficMotionDetector(self.dt)
    self.traffic_state = TrafficState.off
    self.x_state = XState.cruise
    self.x_stop = 0.0
    self.signal_stop_latched = False
    self.stopped_hold_time = 0.0
    self.stop_entry_suppression_time = 0.0
    self.adjusted_stop_distance = 0.0
    self.reference_speed_kph: float | None = None
    self._last_debug_log_time = 0.0
    self.reset()

  def reset(self) -> None:
    self.distance_tracker.reset()
    self.motion_detector.reset()
    self.traffic_state = TrafficState.off
    self.x_state = XState.cruise
    self.x_stop = 0.0
    self.signal_stop_latched = False
    self.stopped_hold_time = 0.0
    self.stop_entry_suppression_time = 0.0
    self.adjusted_stop_distance = 0.0
    self.reference_speed_kph: float | None = None
    self._last_debug_log_time = 0.0

  def update(self, car_state, model, radar_state, comfort_brake: float) -> TrafficStopPlan:
    previous_state = (self.traffic_state, self.x_state, self.signal_stop_latched, self.motion_detector.motion)
    inactive_stop_distance = 1000.0
    v_ego = max(0.0, float(car_state.vEgo))
    v_ego_kph = UnitConverter.ms_to_kph(v_ego)
    ego_distance = v_ego * self.dt

    self.x_stop = self.distance_tracker.update(self._get_model_stop_distance(model), ego_distance)
    raw_stop_distance = self.x_stop

    lead_present = bool(radar_state.leadOne.present)
    lead_distance = float(radar_state.leadOne.dRel) if lead_present else inactive_stop_distance
    if not np.isfinite(lead_distance):
      lead_present = False
      lead_distance = inactive_stop_distance
    observation = self._update_motion_detector(model, v_ego_kph, lead_distance)
    self.stop_entry_suppression_time = max(0.0, self.stop_entry_suppression_time - self.dt)

    gas_pressed = bool(car_state.gasPressed)
    left_blinker = bool(car_state.leftBlinker)
    entry_allowed = is_traffic_stop_entry_allowed(car_state.steeringAngleDeg)

    if gas_pressed:
      self.signal_stop_latched = False
      self.stop_entry_suppression_time = 10.0
    elif (observation.valid and
          self.motion_detector.motion == ModelMotion.stopping and
          self.stop_entry_suppression_time == 0.0 and
          (self.signal_stop_latched or entry_allowed)):
      self.signal_stop_latched = True
    elif (observation.valid and
          self.signal_stop_latched and
          self.motion_detector.motion == ModelMotion.starting and
          not left_blinker):
      self.signal_stop_latched = False

    if self.signal_stop_latched:
      self.traffic_state = TrafficState.red
    elif not observation.valid:
      self.traffic_state = TrafficState.off
    elif self.motion_detector.motion == ModelMotion.stopping:
      self.traffic_state = TrafficState.red
    elif self.motion_detector.motion == ModelMotion.starting:
      self.traffic_state = TrafficState.green
    else:
      self.traffic_state = TrafficState.off

    filtered_stop_distance = self.x_stop
    lead_near_stop = lead_present and (lead_distance - filtered_stop_distance) < 2.0

    if self.x_state == XState.e2eStopped:
      self.stopped_hold_time = max(0.0, self.stopped_hold_time - self.dt)
      if gas_pressed:
        self.x_state = XState.e2ePrepare
      elif lead_near_stop:
        self.x_state = XState.lead
      elif self.stopped_hold_time == 0.0 and not self.signal_stop_latched:
        self.x_state = XState.e2ePrepare

    elif self.x_state == XState.e2eStop:
      self.stopped_hold_time = 0.0
      if gas_pressed:
        self.x_state = XState.e2eCruise
      elif lead_near_stop:
        self.x_state = XState.lead
      elif not self.signal_stop_latched:
        self.x_state = XState.e2eCruise
      else:
        reference_speed_kph = get_traffic_stop_reference_speed(v_ego_kph, self.reference_speed_kph)
        self.reference_speed_kph = reference_speed_kph
        stop_distance = get_virtual_traffic_stop_distance(self.x_stop, reference_speed_kph)
        if stop_distance > 10.0:
          self.adjusted_stop_distance = stop_distance
        filtered_stop_distance = 0.0
        if v_ego < 0.3:
          self.stopped_hold_time = 0.5
          self.x_state = XState.e2eStopped

    elif self.x_state == XState.e2ePrepare:
      if lead_present:
        self.x_state = XState.lead
      elif v_ego_kph < 5.0 and self.signal_stop_latched:
        self.x_state = XState.e2eStop
        self.adjusted_stop_distance = 5.0
      elif v_ego_kph > 5.0:
        self.x_state = XState.e2eCruise

    else:  # XState.lead, XState.cruise, XState.e2eCruise
      if lead_present:
        self.x_state = XState.lead
      elif self.signal_stop_latched:
        self.x_state = XState.e2eStop
        reference_speed_kph = get_traffic_stop_reference_speed(v_ego_kph, None)
        self.reference_speed_kph = reference_speed_kph
        self.adjusted_stop_distance = get_virtual_traffic_stop_distance(self.x_stop, reference_speed_kph)
      else:
        self.x_state = XState.e2eCruise

    self.adjusted_stop_distance = max(0.0, self.adjusted_stop_distance - ego_distance)
    signal_stop_active = self.signal_stop_latched
    if not signal_stop_active:
      filtered_stop_distance = inactive_stop_distance
      self.adjusted_stop_distance = 0.0
      self.reference_speed_kph = None
    elif self.adjusted_stop_distance > 0.0:
      filtered_stop_distance = 0.0

    stop_distance = filtered_stop_distance + self.adjusted_stop_distance
    stop_distance = max(stop_distance, v_ego ** 2 / (2.0 * float(comfort_brake)))
    plan = TrafficStopPlan(raw_stop_distance, stop_distance, signal_stop_active)
    self._log_debug(
      model,
      car_state,
      observation,
      lead_present,
      lead_distance,
      plan,
      previous_state != (self.traffic_state, self.x_state, self.signal_stop_latched, self.motion_detector.motion),
    )
    return plan

  @staticmethod
  def _get_model_stop_distance(model) -> float:
    try:
      return float(model.position.x[31])
    except (AttributeError, IndexError, TypeError, ValueError):
      return np.nan

  def _update_motion_detector(self, model, v_ego_kph: float, lead_distance: float) -> TrafficModelObservation:
    try:
      return self.motion_detector.update(
        model.velocity.x,
        v_ego_kph,
        model.position.x[-1],
        model.position.y,
        lead_distance,
      )
    except (AttributeError, IndexError, TypeError, ValueError):
      return self.motion_detector.update([], v_ego_kph, np.nan, [], lead_distance)

  def _log_debug(self, model, car_state, observation: TrafficModelObservation,
                 lead_present: bool, lead_distance: float, plan: TrafficStopPlan,
                 state_changed: bool) -> None:
    now = time.monotonic()
    should_sample = (plan.signal_stop_active or
                     self.motion_detector.stop_evidence_time > 0.0 or
                     self.motion_detector.go_evidence_time > 0.0 or
                     not observation.valid)
    if not state_changed and (not should_sample or now - self._last_debug_log_time < TRAFFIC_DEBUG_LOG_INTERVAL_S):
      return

    self._last_debug_log_time = now
    event = "transition" if state_changed else ("invalid" if not observation.valid else "sample")
    log_format = " ".join((
      "event=%s big=%d valid=%d motion=%s traffic=%s x_state=%s latch=%d signal_active=%d",
      "v_ego=%.2f initial_v=%.2f terminal_v=%.2f filtered_v=%.2f terminal_x=%.2f lateral_y=%.2f",
      "stop_ev=%d go_ev=%d stop_ev_s=%.2f go_ev_s=%.2f raw_stop=%.2f stop=%.2f adjusted=%.2f",
      "lead=%d lead_d=%.2f gas=%d left_blinker=%d suppress_s=%.2f",
    ))
    traffic_log.debug(
      log_format,
      event,
      int(bool(getattr(model, "big", False))),
      int(observation.valid),
      self.motion_detector.motion.name,
      self.traffic_state.name,
      self.x_state.name,
      int(self.signal_stop_latched),
      int(plan.signal_stop_active),
      max(0.0, float(car_state.vEgo)),
      observation.initial_speed_ms,
      observation.terminal_speed_ms,
      observation.filtered_terminal_speed_ms,
      observation.terminal_distance,
      observation.lateral_offset,
      int(observation.stop_evidence),
      int(observation.go_evidence),
      self.motion_detector.stop_evidence_time,
      self.motion_detector.go_evidence_time,
      plan.raw_stop_distance,
      plan.stop_distance,
      self.adjusted_stop_distance,
      int(lead_present),
      lead_distance,
      int(bool(car_state.gasPressed)),
      int(bool(car_state.leftBlinker)),
      self.stop_entry_suppression_time,
    )
