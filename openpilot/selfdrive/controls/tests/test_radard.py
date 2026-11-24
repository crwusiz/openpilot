from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

from openpilot.selfdrive.controls import radard


class TestRadarLeadAssociation(unittest.TestCase):
  def setUp(self):
    self.radar = radard.RadarD.__new__(radard.RadarD)
    self.radar.v_ego = 2.0
    self.radar.ready = True
    self.radar.enable_radar_tracks = True
    self.radar.corner_tracks_available = False
    self.model_lead = SimpleNamespace(x=[50.0], y=[0.0], v=[8.0], a=[0.0])

  def test_unmatched_radar_lead_has_no_vision_match_confidence(self):
    sticky = radard.Track(1)
    sticky.cnt = 3
    sticky.selected_count = 2
    sticky.dRel = 20.0

    scc = radard.Track(0)
    scc.cnt = 3
    scc.dRel = 12.0

    low_speed = radard.Track(2)
    low_speed.dRel = 10.0

    for track, low_speed_override in ((sticky, False), (scc, False), (low_speed, True)):
      with self.subTest(track_id=track.identifier), patch.object(radard, 'match_vision_to_track', return_value=None):
        lead, _ = self.radar.get_lead({track.identifier: track}, 0, self.model_lead, model_v_ego=2.0, lead_prob=0.9,
                                     low_speed_override=low_speed_override)

        self.assertTrue(lead['present'])
        self.assertTrue(lead['radar'])
        self.assertEqual(lead['radarTrackId'], track.identifier)
        self.assertEqual(lead['modelProb'], 0.0)

  def test_matched_radar_lead_retains_vision_match_confidence(self):
    track = radard.Track(1)
    track.dRel = 45.0

    with patch.object(radard, 'match_vision_to_track', return_value=track):
      lead, _ = self.radar.get_lead({track.identifier: track}, 0, self.model_lead, model_v_ego=2.0, lead_prob=0.9,
                                   low_speed_override=False)

    self.assertTrue(lead['radar'])
    self.assertEqual(lead['radarTrackId'], track.identifier)
    self.assertEqual(lead['modelProb'], 0.9)


class TestRadarDState(unittest.TestCase):
  def setUp(self):
    params = self.enterContext(patch.object(radard, 'Params')).return_value
    params.get_bool.return_value = False
    self.radar = radard.RadarD()
    model_lead = SimpleNamespace(x=[50.0], y=[0.0], v=[8.0], a=[0.0], prob=0.9)
    lane_line = SimpleNamespace(x=[], y=[])
    self.model = SimpleNamespace(leadsV3=[model_lead, SimpleNamespace(prob=0.0)],
                                 position=SimpleNamespace(x=[], y=[], yStd=[]), velocity=SimpleNamespace(x=[2.0]),
                                 laneLines=[lane_line] * 4, laneLineProbs=[0.0] * 4)
    self.sm = MagicMock(seen={'modelV2': True}, updated={'modelV2': True}, recv_frame={'carState': 1},
                        logMonoTime={'modelV2': 1})
    messages = {'modelV2': self.model, 'carState': SimpleNamespace(vEgo=2.0)}
    self.sm.__getitem__.side_effect = messages.__getitem__
    self.sm.all_checks.return_value = True
    self.radar_data = radard.car.RadarData.new_message()

  def test_complete_frames_use_a_fresh_message(self):
    self.radar.update(self.sm, self.radar_data.as_reader())
    first_state = self.radar.radar_state
    self.assertTrue(first_state.leadOne.present)

    self.model.leadsV3[0].x[0] = 60.0
    self.radar.update(self.sm, self.radar_data.as_reader())
    self.assertIsNot(self.radar.radar_state, first_state)
    self.assertAlmostEqual(self.radar.radar_state.leadOne.dRel - first_state.leadOne.dRel, 10.0, places=4)

  def test_valid_incomplete_frames_keep_leads_and_refresh_metadata(self):
    self.radar.update(self.sm, self.radar_data.as_reader())
    first_state = self.radar.radar_state
    self.model.leadsV3.pop()
    self.sm.logMonoTime['modelV2'] = 2
    self.radar_data.errors.radarFault = True
    self.radar.update(self.sm, self.radar_data.as_reader())

    state = self.radar.radar_state
    self.assertIsNot(state, first_state)
    self.assertEqual(state.leadOne.to_dict(), first_state.leadOne.to_dict())
    self.assertEqual(state.mdMonoTime, 2)
    self.assertTrue(state.radarErrors.radarFault)


class TestRadarDMain(unittest.TestCase):
  def test_updates_only_on_new_model_frames(self):
    setup_realtime = self.enterContext(patch.object(radard, 'config_realtime_process'))
    params = self.enterContext(patch.object(radard, 'Params')).return_value
    submaster = self.enterContext(patch.object(radard.messaging, 'SubMaster'))
    sm = submaster.return_value
    pm = self.enterContext(patch.object(radard.messaging, 'PubMaster')).return_value
    radar = self.enterContext(patch.object(radard, 'RadarD')).return_value
    fresh_tracks = (object(), object())
    frames = iter(((False, None), (True, fresh_tracks[0]), (False, fresh_tracks[0]), (True, fresh_tracks[1])))

    def poll_frame():
      updated, tracks = next(frames)
      sm.updated = {'modelV2': updated}
      sm.__getitem__.return_value = tracks

    sm.update.side_effect = poll_frame
    with self.assertRaises(StopIteration):
      radard.main()

    setup_realtime.assert_called_once_with(6, radard.Priority.CTRL_LOW)
    params.get.assert_called_once_with('CarParams', block=True)
    submaster.assert_called_once_with(['modelV2', 'carState', 'radarTracks'], poll='modelV2')
    self.assertEqual(radar.mock_calls, [call.update(sm, fresh_tracks[0]), call.publish(pm),
                                       call.update(sm, fresh_tracks[1]), call.publish(pm)])
