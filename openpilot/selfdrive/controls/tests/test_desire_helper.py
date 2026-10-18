import unittest
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car import structs
from openpilot.cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib import desire_helper


class TestLaneChangeAvailability(unittest.TestCase):
  def setUp(self):
    params_patch = patch.object(desire_helper, 'Params')
    self.params = params_patch.start().return_value
    self.addCleanup(params_patch.stop)
    self.params.get_bool.return_value = False
    self.params.get.return_value = 0
    self.helper = desire_helper.DesireHelper()
    self.carstate = structs.CarState(vEgo=20., leftLaneLine=10, rightLaneLine=10)
    self.model_data = {
      'laneLineProbs': [0.9, 0.9, 0.9, 0.9], 'roadEdgeStds': [1., 1.],
      'laneLines': [SimpleNamespace(x=[0., 30., 60.], y=[y] * 3) for y in (-5.4, -1.8, 1.8, 5.4)],
      'roadEdges': [SimpleNamespace(x=[0., 30., 60.], y=[y] * 3) for y in (-8., 8.)],
    }
    self.radar_state = log.RadarState.new_message()
    self.warmup(self.model_data)

  def warmup(self, model_data):
    for _ in range(round(0.3 / DT_MDL)):
      result = self.update(model_data)
    return result

  def update(self, model_data, lateral_active=True):
    self.helper.update(self.carstate, lateral_active, 0., model_data, self.radar_state)
    return self.helper.lane_change_available_left, self.helper.lane_change_available_right

  def test_both_sides_are_reported_without_a_blinker(self):
    self.assertEqual(self.update(self.model_data), (True, True))
    self.assertEqual(self.helper.lane_change_state, log.LaneChangeState.off)
    self.assertEqual(self.helper.desire, log.Desire.none)

  def test_each_side_uses_its_own_lane_and_road_edge(self):
    cases = (
      ({'laneLineProbs': [0.9, 0.4, 0.9, 0.9]}, (False, True)),
      ({'laneLineProbs': [0.9, 0.9, 0.4, 0.9]}, (True, False)),
      ({'laneLineProbs': [0.9, 0.5, 0.5, 0.9]}, (False, False)),
      ({'roadEdgeStds': [0.4, 1.]}, (False, True)),
      ({'roadEdgeStds': [1., 0.4]}, (True, False)),
    )
    for changes, expected in cases:
      with self.subTest(changes=changes):
        self.assertEqual(self.warmup(self.model_data | changes), expected)

  def test_line_colours_and_types_match_lane_line_check(self):
    cases = (
      (0, 20, 30, (False, True)),
      (0, 25, 35, (False, True)),
      (0, 11, 15, (True, True)),
      (1, 20, 30, (True, True)),
      (1, 11, 15, (False, True)),
      (1, 15, 11, (True, False)),
      (1, 18, 17, (False, False)),
      (2, 15, 11, (True, False)),
      (None, 20, 30, (False, True)),
    )
    for mode, left, right, expected in cases:
      with self.subTest(mode=mode, left=left, right=right):
        self.helper = desire_helper.DesireHelper()
        self.params.get.return_value = mode
        self.carstate.leftLaneLine, self.carstate.rightLaneLine = left, right
        self.assertEqual(self.warmup(self.model_data), expected)

  def test_blindspots_block_only_the_corresponding_side(self):
    self.carstate.leftBlindspot = True
    self.assertEqual(self.update(self.model_data), (False, True))
    self.carstate.rightBlindspot = True
    self.assertEqual(self.update(self.model_data), (False, False))
    self.carstate.leftBlindspot = False
    for _ in range(round(2.0 / DT_MDL) - 1):
      self.assertEqual(self.update(self.model_data), (False, False))
    self.assertEqual(self.update(self.model_data), (True, False))
    self.carstate.rightBlindspot = False
    for _ in range(round(2.0 / DT_MDL) - 1):
      self.assertEqual(self.update(self.model_data), (True, False))
    self.assertEqual(self.update(self.model_data), (True, True))

  def test_missing_model_clears_previous_availability(self):
    for model_data in (None, {}, {'laneLineProbs': [], 'roadEdgeStds': []}):
      with self.subTest(model_data=model_data):
        self.assertEqual(self.warmup(self.model_data), (True, True))
        self.assertEqual(self.update(model_data), (False, False))

  def test_partial_or_nonfinite_model_data_cannot_enable_the_missing_side(self):
    cases = (
      ({'laneLineProbs': [0.9, 0.9]}, (True, False)),
      ({'roadEdgeStds': [1.]}, (True, False)),
      ({'laneLineProbs': [0.9, float('nan'), 0.9, 0.9]}, (False, True)),
      ({'laneLineProbs': [0.9, 0.9, float('inf'), 0.9]}, (True, False)),
      ({'roadEdgeStds': [float('nan'), 1.]}, (False, True)),
      ({'roadEdgeStds': [1., float('inf')]}, (True, False)),
    )
    for changes, expected in cases:
      with self.subTest(changes=changes):
        self.assertEqual(self.warmup(self.model_data | changes), expected)

  def test_display_availability_does_not_require_active_steering(self):
    self.carstate.vEgo = 0.
    self.assertEqual(self.update(self.model_data, lateral_active=False), (True, True))
    self.assertEqual(self.helper.lane_change_state, log.LaneChangeState.off)

  def test_setting_changes_are_refreshed_within_one_second(self):
    self.carstate.leftLaneLine, self.carstate.rightLaneLine = 11, 20
    self.assertEqual(self.update(self.model_data), (True, False))
    self.params.get.return_value = 1
    while self.helper.frame % round(1. / DT_MDL) != 0:
      self.assertEqual(self.update(self.model_data), (True, False))
    self.assertEqual(self.update(self.model_data), (False, True))
    self.assertEqual(self.params.get.call_count, 2)
    self.params.get.assert_called_with('LaneLineCheck', return_default=True)

  def test_existing_lane_change_progression(self):
    self.carstate.leftBlinker = True
    self.update(self.model_data)
    self.assertEqual(self.helper.lane_change_state, log.LaneChangeState.preLaneChange)
    self.carstate.steeringPressed = True
    self.carstate.steeringTorque = 1.
    self.update(self.model_data)
    self.assertEqual(self.helper.lane_change_state, log.LaneChangeState.laneChangeStarting)
    self.assertEqual(self.helper.desire, log.Desire.laneChangeLeft)
    self.carstate.leftBlindspot = True
    self.update(self.model_data)
    self.assertEqual(self.helper.lane_change_state, log.LaneChangeState.off)
    self.assertEqual(self.helper.desire, log.Desire.none)
    self.assertFalse(self.helper.lane_change_available_left)

  def test_availability_fields_round_trip_through_model_schema(self):
    model = log.ModelDataV2.new_message()
    self.assertFalse(model.meta.laneChangeAvailableLeft)
    self.assertFalse(model.meta.laneChangeAvailableRight)
    for side in ('left', 'right'):
      with self.subTest(side=side):
        model = log.ModelDataV2.new_message()
        self.carstate.leftBlindspot = side == 'left'
        self.carstate.rightBlindspot = side == 'right'
        self.helper = desire_helper.DesireHelper()
        self.warmup(self.model_data)
        model.meta.laneChangeAvailableLeft = self.helper.lane_change_available_left
        model.meta.laneChangeAvailableRight = self.helper.lane_change_available_right
        model.meta.desire = log.Desire.laneChangeRight
        with log.ModelDataV2.from_bytes(model.to_bytes()) as reader:
          self.assertEqual(reader.meta.laneChangeAvailableLeft, side != 'left')
          self.assertEqual(reader.meta.laneChangeAvailableRight, side != 'right')
          self.assertEqual(reader.meta.desire, log.Desire.laneChangeRight)


if __name__ == '__main__':
  unittest.main()
