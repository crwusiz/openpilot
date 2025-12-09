from cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params

ALC_START_TIME = 3.
ROAD_EDGE_CONFIDENCE_THRESHOLD = 0.5
LANE_LINE_PROB_THRESHOLD = 0.3

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 50 * CV.KPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.

DESIRES = {
  LaneChangeDirection.none: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.none,
    LaneChangeState.laneChangeFinishing: log.Desire.none,
  },
  LaneChangeDirection.left: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.laneChangeLeft,
    LaneChangeState.laneChangeFinishing: log.Desire.laneChangeLeft,
  },
  LaneChangeDirection.right: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.laneChangeRight,
    LaneChangeState.laneChangeFinishing: log.Desire.laneChangeRight,
  },
}


def check_invalid_lane(lane_line_probs, road_edge_stds, direction_left: bool):
  if direction_left:
    left_edge_prob = max(0.0, min(1.0 - road_edge_stds[0], 1.0))
    left_close_prob = lane_line_probs[1] if len(lane_line_probs) > 1 else 0

    if left_edge_prob < ROAD_EDGE_CONFIDENCE_THRESHOLD:
      return True
    elif left_close_prob < LANE_LINE_PROB_THRESHOLD:
      return True
  else:
    right_edge_prob = max(0.0, min(1.0 - road_edge_stds[1], 1.0))
    right_close_prob = lane_line_probs[2] if len(lane_line_probs) > 2 else 0

    if right_edge_prob < ROAD_EDGE_CONFIDENCE_THRESHOLD:
      return True
    elif right_close_prob < LANE_LINE_PROB_THRESHOLD:
      return True

  return False


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_ll_prob = 1.0
    self.keep_pulse_timer = 0.0
    self.lane_change_pulse_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none

    self.auto_lane_change_enable = Params().get_bool('AutoLaneChangeEnable')
    self.auto_lane_change_timer = 0.0

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def update(self, carstate, lateral_active, lane_change_prob, model_data=None):
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    invalid_lane_detected = False
    if model_data is not None:
      lane_line_probs = model_data.get('laneLineProbs', [0, 0, 0, 0])
      road_edge_stds = model_data.get('roadEdgeStds', [1.0, 1.0])

      if carstate.leftBlinker:
        invalid_lane_detected = check_invalid_lane(lane_line_probs, road_edge_stds, True)
      elif carstate.rightBlinker:
        invalid_lane_detected = check_invalid_lane(lane_line_probs, road_edge_stds, False)

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX or not one_blinker:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
    else:
      # LaneChangeState.off
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        if not invalid_lane_detected:
          self.lane_change_state = LaneChangeState.preLaneChange
          self.lane_change_ll_prob = 1.0
          # Initialize lane change direction to prevent UI alert flicker
          self.lane_change_direction = self.get_lane_change_direction(carstate)

      # LaneChangeState.preLaneChange
      elif self.lane_change_state == LaneChangeState.preLaneChange:
        self.lane_change_direction = self.get_lane_change_direction(carstate)

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

        if not invalid_lane_detected and not blindspot_detected:
          self.lane_change_pulse_timer += DT_MDL

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right)) or \
                         self.auto_lane_change_enable and (ALC_START_TIME + 0.25) > self.auto_lane_change_timer > ALC_START_TIME

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
        elif invalid_lane_detected or blindspot_detected:
          self.lane_change_pulse_timer = 0.0
          self.auto_lane_change_timer = 0.0
        elif (torque_applied or self.lane_change_pulse_timer > 2.) and not blindspot_detected and not invalid_lane_detected:
          self.lane_change_state = LaneChangeState.laneChangeStarting

      # LaneChangeState.laneChangeStarting
      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

        if invalid_lane_detected or blindspot_detected:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
        else:
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

    if self.lane_change_state in (LaneChangeState.laneChangeFinishing, LaneChangeState.off):
      self.lane_change_pulse_timer = 0.0
    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

    blindspot_for_timer = ((carstate.leftBlindspot and carstate.leftBlinker) or
                          (carstate.rightBlindspot and carstate.rightBlinker))

    if self.lane_change_state == LaneChangeState.off:
      self.auto_lane_change_timer = 0.0
    elif self.lane_change_state == LaneChangeState.preLaneChange:
      if invalid_lane_detected or blindspot_for_timer:
        self.auto_lane_change_timer = 0.0
      elif self.auto_lane_change_timer < (ALC_START_TIME + 0.25):
        self.auto_lane_change_timer += DT_MDL
    elif self.lane_change_state != LaneChangeState.preLaneChange:
      if self.auto_lane_change_timer < (ALC_START_TIME + 0.25):
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
      elif self.desire in (log.Desire.keepLeft, log.Desire.keepRight):
        self.desire = log.Desire.none
