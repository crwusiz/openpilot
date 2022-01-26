from cereal import log
from common.realtime import DT_MDL
from selfdrive.config import Conversions as CV
from common.params import Params

AUTO_LCA_START_TIME = 1.0

LaneChangeState = log.LateralPlan.LaneChangeState
LaneChangeDirection = log.LateralPlan.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 60 * CV.KPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.

DESIRES = {
  LaneChangeDirection.none: {
    LaneChangeState.off: log.LateralPlan.Desire.none,
    LaneChangeState.preLaneChange: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeStarting: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeFinishing: log.LateralPlan.Desire.none,
  },
  LaneChangeDirection.left: {
    LaneChangeState.off: log.LateralPlan.Desire.none,
    LaneChangeState.preLaneChange: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeStarting: log.LateralPlan.Desire.laneChangeLeft,
    LaneChangeState.laneChangeFinishing: log.LateralPlan.Desire.laneChangeLeft,
  },
  LaneChangeDirection.right: {
    LaneChangeState.off: log.LateralPlan.Desire.none,
    LaneChangeState.preLaneChange: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeStarting: log.LateralPlan.Desire.laneChangeRight,
    LaneChangeState.laneChangeFinishing: log.LateralPlan.Desire.laneChangeRight,
  },
}


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_ll_prob = 1.0
    self.keep_pulse_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.LateralPlan.Desire.none

    self.lane_change_enabled = Params().get_bool('LaneChangeEnabled')
    self.auto_lane_change_enabled = Params().get_bool('AutoLaneChangeEnabled')
    self.auto_lane_change_timer = 0.0
    self.prev_torque_applied = False

  def update(self, carstate, active, lane_change_prob):
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    if (not active) or (self.lane_change_timer > LANE_CHANGE_TIME_MAX) or (not one_blinker) or (not self.lane_change_enabled):
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
    else:
      torque_applied = carstate.steeringPressed and \
                       ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                        (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right)) or \
                        self.auto_lane_change_enabled and \
                       (AUTO_LCA_START_TIME+0.25) > self.auto_lane_change_timer > AUTO_LCA_START_TIME

      blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                            (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

      # State transitions
      # off
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        if carstate.leftBlinker:
          self.lane_change_direction = LaneChangeDirection.left
        elif carstate.rightBlinker:
          self.lane_change_direction = LaneChangeDirection.right

        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_ll_prob = 1.0

      # LaneChangeState.preLaneChange
      elif self.lane_change_state == LaneChangeState.preLaneChange:
        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
        elif torque_applied and (not blindspot_detected or self.prev_torque_applied):
          self.lane_change_state = LaneChangeState.laneChangeStarting
        elif torque_applied and blindspot_detected and self.auto_lane_change_timer != 10.0:
          self.auto_lane_change_timer = 10.0
        elif not torque_applied and self.auto_lane_change_timer == 10.0 and not self.prev_torque_applied:
          self.prev_torque_applied = True

      # LaneChangeState.laneChangeStarting
      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        # fade out over .5s
        self.lane_change_ll_prob = max(self.lane_change_ll_prob - 2 * DT_MDL, 0.0)

        # 98% certainty
        if lane_change_prob < 0.02 and self.lane_change_ll_prob < 0.01:
          self.lane_change_state = LaneChangeState.laneChangeFinishing

      # LaneChangeState.laneChangeFinishing
      elif self.lane_change_state == LaneChangeState.laneChangeFinishing:
        # fade in laneline over 1s
        self.lane_change_ll_prob = min(self.lane_change_ll_prob + DT_MDL, 1.0)
        if self.lane_change_ll_prob > 0.99:
          self.lane_change_direction = LaneChangeDirection.none
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
          else:
            self.lane_change_state = LaneChangeState.off

    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

    if self.lane_change_state == LaneChangeState.off:
      self.auto_lane_change_timer = 0.0
      self.prev_torque_applied = False
    elif self.auto_lane_change_timer < (AUTO_LCA_START_TIME+0.25): # stop afer 3 sec resume from 10 when torque applied
      self.auto_lane_change_timer += DT_MDL

    self.prev_one_blinker = one_blinker

    self.desire = DESIRES[self.lane_change_direction][self.lane_change_state]

    # Send keep pulse once per second during LaneChangeStart.preLaneChange
    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.laneChangeStarting):
      self.keep_pulse_timer = 0.0
    elif self.lane_change_state == LaneChangeState.preLaneChange:
      self.keep_pulse_timer += DT_MDL
      if self.keep_pulse_timer > 1.0:
        self.keep_pulse_timer = 0.0
      elif self.desire in (log.LateralPlan.Desire.keepLeft, log.LateralPlan.Desire.keepRight):
        self.desire = log.LateralPlan.Desire.none
