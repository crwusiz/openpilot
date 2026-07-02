from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params

ALC_START_TIME = 3.
ROAD_EDGE_CONFIDENCE_THRESHOLD = 0.5
LANE_LINE_PROB_THRESHOLD = 0.3

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
TurnDirection = log.Desire

LANE_CHANGE_SPEED_MIN = 50 * CV.KPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
LANE_CHANGE_START_TIME = 0.5

def check_invalid_lane(lane_line_probs, road_edge_stds, direction_left: bool):
  if direction_left:
    left_edge_prob = max(0.0, min(1.0 - road_edge_stds[0], 1.0))
    left_close_prob = lane_line_probs[1] if len(lane_line_probs) > 1 else 0

    if road_edge_stds[0] < ROAD_EDGE_CONFIDENCE_THRESHOLD:
      return True
    elif left_close_prob < LANE_LINE_PROB_THRESHOLD and left_edge_prob > 0.35:
      return True

  else:
    right_edge_prob = max(0.0, min(1.0 - road_edge_stds[1], 1.0))
    right_close_prob = lane_line_probs[2] if len(lane_line_probs) > 2 else 0

    if road_edge_stds[1] < ROAD_EDGE_CONFIDENCE_THRESHOLD:
      return True
    elif right_close_prob < LANE_LINE_PROB_THRESHOLD and right_edge_prob > 0.35:
      return True

  return False


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_pulse_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none

    self.auto_lane_change_enable = Params().get_bool("AutoLaneChangeEnable")
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

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
    else:
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        self.lane_change_direction = self.get_lane_change_direction(carstate)

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

        auto_timer_ready = (self.auto_lane_change_enable and
                            (ALC_START_TIME + 0.25) > self.auto_lane_change_timer > ALC_START_TIME and
                            not invalid_lane_detected and not blindspot_detected and lane_change_prob > 0.5)

        torque_applied = (carstate.steeringPressed and
                          ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                           (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right)) or
                          auto_timer_ready)

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
        elif invalid_lane_detected or blindspot_detected:
          self.auto_lane_change_timer = 0.0
        elif torque_applied or self.lane_change_pulse_timer > 2.:
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

        # Lane change cancellation safety feature (Custom)
        if invalid_lane_detected or blindspot_detected:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
        elif lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    # ALC and Pulse timers
    blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

    if self.lane_change_state == LaneChangeState.off:
      self.auto_lane_change_timer = 0.0
      self.lane_change_pulse_timer = 0.0
    elif self.lane_change_state == LaneChangeState.preLaneChange:
      if invalid_lane_detected or blindspot_detected:
        self.auto_lane_change_timer = 0.0
      else:
        self.lane_change_pulse_timer += DT_MDL
        if self.auto_lane_change_timer < (ALC_START_TIME + 0.25):
          self.auto_lane_change_timer += DT_MDL
    else:
      if self.auto_lane_change_timer < (ALC_START_TIME + 0.25):
        self.auto_lane_change_timer += DT_MDL

    self.prev_one_blinker = one_blinker and lateral_active

    self.desire = log.Desire.none
    if self.lane_change_state == LaneChangeState.laneChangeStarting:
      if self.lane_change_direction == LaneChangeDirection.left:
        self.desire = log.Desire.laneChangeLeft
      elif self.lane_change_direction == LaneChangeDirection.right:
        self.desire = log.Desire.laneChangeRight
