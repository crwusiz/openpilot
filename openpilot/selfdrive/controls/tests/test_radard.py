from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

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
