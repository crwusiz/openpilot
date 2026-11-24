import math
import numpy as np
from opendbc.car.structs import car
from openpilot.common.pid import PIDController
from openpilot.common.realtime import DT_CTRL


LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(active, long_control_state, should_stop, brake_pressed, cruise_standstill):
  if not active:
    return LongCtrlState.off
  if should_stop or (long_control_state != LongCtrlState.pid and (brake_pressed or cruise_standstill)):
    return LongCtrlState.stopping
  return LongCtrlState.pid


class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController(0.0, (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             rate=1 / DT_CTRL)
    self.last_output_accel = 0.0
    self.stopping_accel_weight = 0.0

  def reset(self):
    self.pid.reset()
    self.last_output_accel = 0.0
    self.stopping_accel_weight = 0.0

  def update(self, active, CS, long_plan, accel_limits):
    """Update the longitudinal state and return the acceleration command."""
    self.long_control_state = long_control_state_trans(active, self.long_control_state, long_plan.shouldStop,
                                                       CS.brakePressed, CS.cruiseState.standstill)
    if self.long_control_state == LongCtrlState.off:
      self.reset()
      return 0.0

    accel_min, accel_max = accel_limits
    if CS.brakePressed and math.isfinite(accel_max):
      accel_max = min(accel_max, 0.0)
    if not math.isfinite(accel_min) or not math.isfinite(accel_max) or accel_min > accel_max:
      self.reset()
      return 0.0
    self.pid.set_limits(pos_limit=accel_max, neg_limit=accel_min)

    if self.long_control_state == LongCtrlState.stopping:
      output_accel = min(self.last_output_accel, 0.0)
      if output_accel > self.CP.stopAccel:
        self.stopping_accel_weight = 1.0
        d_accel = np.interp(output_accel, [-0.8, -0.3, 0.2], [1.0, 0.05, 1.0])
        output_accel = max(self.CP.stopAccel, output_accel - d_accel * DT_CTRL)
      else:
        self.stopping_accel_weight = 0.0
      self.pid.reset()
    else:  # LongCtrlState.pid
      a_target = long_plan.aTarget
      error = a_target - CS.aEgo
      if math.isfinite(error) and math.isfinite(CS.vEgo):
        output_accel = self.pid.update(error, speed=CS.vEgo, feedforward=a_target, freeze_integrator=CS.brakePressed)
        self.stopping_accel_weight = max(self.stopping_accel_weight - DT_CTRL, 0.0)
        output_accel = self.last_output_accel * self.stopping_accel_weight + output_accel * (1 - self.stopping_accel_weight)
      else:
        self.reset()
        output_accel = 0.0

    if not math.isfinite(output_accel):
      self.reset()
      output_accel = 0.0
    self.last_output_accel = float(np.clip(output_accel, accel_min, accel_max))
    return self.last_output_accel
