import numpy as np
from opendbc.car.structs import car
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from openpilot.common.pid import PIDController
from openpilot.selfdrive.modeld.constants import ModelConstants

CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]

LongCtrlState = car.CarControl.Actuators.LongControlState


def long_control_state_trans(CP, active, long_control_state, v_ego,
                             should_stop, brake_pressed, cruise_standstill, lead):
  stopping_condition = should_stop or cruise_standstill or brake_pressed
  starting_condition = (not should_stop and
                        not cruise_standstill and
                        not brake_pressed)

  if lead.status:
    starting_condition = starting_condition and lead.vLeadK > 1.0 and lead.dRel > 6.0

  started_condition = v_ego > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state == LongCtrlState.off:
      if not starting_condition:
        long_control_state = LongCtrlState.stopping
      else:
        if starting_condition and CP.startingState:
          long_control_state = LongCtrlState.starting
        else:
          long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition and CP.startingState:
        long_control_state = LongCtrlState.starting
      elif starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state in [LongCtrlState.starting, LongCtrlState.pid]:
      if stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid
  return long_control_state

class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             rate=1 / DT_CTRL)
    self.last_output_accel = 0.0
    self.stopping_accel_weight = 0.0
    self.prev_long_control_state = self.long_control_state

  def reset(self):
    self.pid.reset()

  def update(self, active, CS, long_plan, accel_limits, sm):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    lead = sm['radarState'].leadOne

    self.prev_long_control_state = self.long_control_state
    self.long_control_state = long_control_state_trans(self.CP, active, self.long_control_state, CS.vEgo,
                                                       long_plan.shouldStop, CS.brakePressed,
                                                       CS.cruiseState.standstill, lead)
    if self.long_control_state == LongCtrlState.off:
      self.reset()
      output_accel = 0.
      self.stopping_accel_weight = 0.0

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel
      if output_accel > self.CP.stopAccel:
        output_accel = min(output_accel, 0.0)
        self.stopping_accel_weight = 1.0

        if self.prev_long_control_state == LongCtrlState.starting:
          output_accel -= self.CP.stoppingDecelRate * 1.5 * DT_CTRL
        else:
          m_accel = -0.3
          d_accel = np.interp(output_accel,
                              [m_accel - 0.5, m_accel, m_accel + 0.5],
                              [self.CP.stoppingDecelRate, 0.05, self.CP.stoppingDecelRate])

          output_accel -= d_accel * DT_CTRL
      else:
        self.stopping_accel_weight = 0.0

      self.reset()

    elif self.long_control_state == LongCtrlState.starting:
      output_accel = self.CP.startAccel

      if lead.status:
        accel_scale = np.interp(lead.dRel, [4.0, 8.0], [0.0, 1.0])
        output_accel *= accel_scale

      self.reset()
      self.stopping_accel_weight = 0.0

    else:  # LongCtrlState.pid
      #error = long_plan.a_target - CS.aEgo
      error = long_plan.vTarget - CS.vEgo
      output_accel = self.pid.update(error, speed=CS.vEgo, feedforward=long_plan.aTarget)

      self.stopping_accel_weight = max(self.stopping_accel_weight - 1.0 * DT_CTRL, 0.)
      output_accel = self.last_output_accel * self.stopping_accel_weight + output_accel * (1. - self.stopping_accel_weight)

    self.last_output_accel = np.clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel
