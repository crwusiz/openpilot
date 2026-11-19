from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.selfdrive.controls import radard


class TestRadarLeadAssociation(unittest.TestCase):
  def setUp(self):
    self.radar = radard.RadarD.__new__(radard.RadarD)
    self.radar.v_ego = 2.0
    self.radar.ready = True
    self.radar.enable_radar_tracks = True
    self.radar.corner_tracks_available = False
    self.model_lead = SimpleNamespace(x=[50.0], y=[0.0], v=[8.0], a=[0.0])

  def test_unmatched_radar_lead_has_no_model_trajectory_confidence(self):
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

  def test_matched_radar_lead_retains_model_trajectory_confidence(self):
    track = radard.Track(1)
    track.dRel = 45.0

    with patch.object(radard, 'match_vision_to_track', return_value=track):
      lead, _ = self.radar.get_lead({track.identifier: track}, 0, self.model_lead, model_v_ego=2.0, lead_prob=0.9,
                                   low_speed_override=False)

    self.assertTrue(lead['radar'])
    self.assertEqual(lead['radarTrackId'], track.identifier)
    self.assertEqual(lead['modelProb'], 0.9)
