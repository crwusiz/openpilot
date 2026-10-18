import unittest

from opendbc.car import structs
from openpilot.cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.lane_change_availability import LaneChangeAvailability


class TestLaneChangeAvailabilityInputs(unittest.TestCase):
  def setUp(self):
    self.carstate = structs.CarState(vEgo=20.)
    self.model = log.ModelDataV2.new_message(laneLineProbs=[0.9] * 4, roadEdgeStds=[1., 1.])
    for field, positions in (('laneLines', (-5.4, -1.8, 1.8, 5.4)), ('roadEdges', (-8., 8.))):
      for line, y in zip(self.model.init(field, len(positions)), positions, strict=True):
        line.x, line.y = [0., 30., 60.], [y] * 3
    self.model_data = {key: getattr(self.model, key) for key in ('laneLines', 'roadEdges', 'laneLineProbs', 'roadEdgeStds')}
    self.radar = log.RadarState.new_message()
    self.side = LaneChangeAvailability('left')

  def update(self, frames=1):
    for _ in range(frames):
      result = self.side.update(self.carstate, self.model_data, self.radar, True)
    return result

  def test_startup_requires_confirmed_space_and_clear_objects(self):
    self.assertFalse(self.update(round(0.3 / DT_MDL) - 1))
    self.assertTrue(self.update())
    self.assertEqual(len(self.model.laneLines[0].t), 0)

  def test_geometry_checks_use_current_distance_coordinates(self):
    self.model.roadEdges[0].y = [-8., -2.8, -2.8]
    self.assertFalse(self.update(20))

  def test_insufficient_lane_or_edge_space_blocks_only_that_side(self):
    cases = [('laneLines', 0, -3.8), ('roadEdges', 0, -3.8)]
    for field, index, y in cases:
      with self.subTest(field=field):
        self.setUp()
        getattr(self.model, field)[index].y = [y] * 3
        self.assertFalse(self.update(20))
        right = LaneChangeAvailability('right')
        for _ in range(20):
          available = right.update(self.carstate, self.model_data, self.radar, True)
        self.assertTrue(available)

  def test_new_nearby_obstacle_blocks_immediately_and_clears_after_300ms(self):
    self.assertTrue(self.update(20))
    self.radar.leadsLeft = [{'present': True, 'dRel': 4., 'vRel': 0.}]
    self.assertFalse(self.update())
    self.radar.leadsLeft = []
    self.assertFalse(self.update(round(0.3 / DT_MDL) - 1))
    self.assertTrue(self.update())

  def test_distance_and_relative_speed_cover_front_and_rear_objects(self):
    cases = [(4., 0., False), (10., -5., False), (30., -20., False),
             (30., 0., True), (10., 4., True), (-4., 0., False),
             (-10., 5., False), (-30., -5., True), (float('nan'), 0., False),
             (20., float('inf'), False)]
    for distance, relative_speed, expected in cases:
      with self.subTest(distance=distance, relative_speed=relative_speed):
        self.side = LaneChangeAvailability('left')
        self.radar.leadsLeft = [{'present': True, 'dRel': distance, 'vRel': relative_speed}]
        self.assertEqual(self.update(20), expected)

  def test_primary_side_lead_is_checked_without_object_list(self):
    self.radar.leadLeft = {'present': True, 'dRel': 8., 'vRel': -4.}
    self.assertFalse(self.update(20))

  def test_present_false_is_not_an_obstacle(self):
    self.radar.leadsLeft = [{'present': False, 'dRel': 4., 'vRel': 0.}]
    self.assertTrue(self.update(20))

  def test_blindspot_hold_survives_clear_object_list(self):
    self.assertTrue(self.update(20))
    self.carstate.leftBlindspot = True
    self.assertFalse(self.update())
    self.carstate.leftBlindspot = False
    self.assertFalse(self.update(round(2.0 / DT_MDL) - 1))
    self.assertTrue(self.update())

  def test_missing_or_faulted_radar_cannot_enable_green(self):
    for error in ('canError', 'radarFault', 'wrongConfig', 'radarUnavailableTemporary'):
      with self.subTest(error=error):
        self.radar = log.RadarState.new_message()
        setattr(self.radar.radarErrors, error, True)
        self.assertFalse(self.update(20))
    self.radar = None
    self.assertFalse(self.update(20))
    self.radar = log.RadarState.new_message()
    self.assertFalse(self.update(round(0.3 / DT_MDL) - 1))
    self.assertTrue(self.update())

  def test_missing_model_or_geometry_cannot_enable_green(self):
    self.assertTrue(self.update(20))
    for model_data in (None, {}, self.model_data | {'laneLines': []}, self.model_data | {'roadEdges': []}):
      with self.subTest(model_data=model_data):
        self.assertFalse(self.side.update(self.carstate, model_data, self.radar, True))
    self.assertFalse(self.update(round(0.2 / DT_MDL) - 1))
    self.assertTrue(self.update())

  def test_invalid_geometry_or_confidence_revokes_green_immediately(self):
    for field in ('x', 'y', 'probability', 'outer_probability', 'edge_std'):
      with self.subTest(field=field):
        self.setUp()
        self.assertTrue(self.update(20))
        if field in ('x', 'y'):
          setattr(self.model.laneLines[0], field, [0., float('nan'), 60.])
        elif field == 'probability':
          self.model_data['laneLineProbs'] = [0.9, 0.5, 0.9, 0.9]
        elif field == 'outer_probability':
          self.model_data['laneLineProbs'] = [0.1, 0.9, 0.9, 0.9]
        else:
          self.model_data['roadEdgeStds'] = [float('nan'), 1.]
        self.assertFalse(self.update())

  def test_present_field_survives_actual_radar_schema_roundtrip(self):
    self.radar.leadsLeft = [{'present': True, 'dRel': 4., 'vRel': 0.}]
    with log.RadarState.from_bytes(self.radar.to_bytes()) as reader:
      for _ in range(20):
        available = self.side.update(self.carstate, self.model_data, reader, True)
      self.assertFalse(available)


if __name__ == '__main__':
  unittest.main()
