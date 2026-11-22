import unittest
from types import SimpleNamespace
from unittest.mock import patch

import raylib_nb as rl

from openpilot.cereal import log
from openpilot.selfdrive.ui.mici.onroad import long_indicator


class TestLongIndicator(unittest.TestCase):
  def setUp(self):
    self.personality = SimpleNamespace(raw=log.LongitudinalPersonality.standard)
    sm = type('SubMaster', (dict,), {'alive': {'longitudinalPlan': True}})(
      selfdriveState=SimpleNamespace(personality=self.personality),
      longitudinalPlan=SimpleNamespace(hasLead=False),
    )
    self.state = SimpleNamespace(sm=sm, started=True, cluster_connected=False, has_longitudinal_control=True,
                                 received_since_started=lambda _: True)
    self.enterContext(patch.object(long_indicator, 'ui_state', self.state))
    self.enterContext(patch.object(long_indicator, 'gui_app', SimpleNamespace(target_fps=60)))
    self.enterContext(patch.object(long_indicator.status_fade, 'weight', return_value=0.0))
    self.clock = self.enterContext(patch.object(rl, 'get_time', return_value=10.0))
    self.enterContext(patch.object(long_indicator.LongIndicator, '_texture', side_effect=lambda name, *_: name))
    self.indicator = long_indicator.LongIndicator()
    self.indicator.set_should_draw(True)
    self.draw = self.enterContext(patch.object(self.indicator, '_draw_centered'))
    self.rect = rl.Rectangle(0, 0, 400, 300)

  def _draw_alphas(self):
    return {call.args[0]: call.args[3] for call in self.draw.call_args_list}

  def _settle_distance_bars(self):
    for _ in range(120):
      self.draw.reset_mock()
      self.indicator._draw_distance_bars(self.rect, 1.0, True)
    return self._draw_alphas()

  def test_each_personality_lights_the_matching_number_of_bars(self):
    personalities = (log.LongitudinalPersonality.aggressive, log.LongitudinalPersonality.standard,
                     log.LongitudinalPersonality.relaxed, log.LongitudinalPersonality.morerelaxed)
    for count, personality in enumerate(personalities, start=1):
      with self.subTest(personality=personality):
        self.personality.raw = personality
        self.indicator._draw_distance_bars(self.rect, 1.0, True)
        self.clock.return_value += long_indicator.HIGHLIGHT_TIME
        alphas = self._settle_distance_bars()
        for bar in range(1, 5):
          self.assertAlmostEqual(alphas[f'distance_{bar}'], 0.9 if bar <= count else 0.35)
          self.assertAlmostEqual(alphas[f'distance_{bar}_green'], 0.0)

  def test_more_relaxed_highlights_the_fourth_bar(self):
    self.indicator._draw_distance_bars(self.rect, 1.0, True)
    self.personality.raw = log.LongitudinalPersonality.morerelaxed

    alphas = self._settle_distance_bars()

    self.assertAlmostEqual(alphas['distance_4_green'], 1.0)
    self.assertAlmostEqual(alphas['distance_4'], 0.0)
    for bar in range(1, 4):
      self.assertAlmostEqual(alphas[f'distance_{bar}'], 0.9)
      self.assertAlmostEqual(alphas[f'distance_{bar}_green'], 0.0)

  def test_cluster_connection_hides_the_whole_indicator_until_disconnected(self):
    self.indicator._render(self.rect)
    self.assertGreater(self._draw_alphas()['car'], 0.0)
    self.assertGreater(self._draw_alphas()['distance_1'], 0.0)

    self.state.cluster_connected = True
    self.draw.reset_mock()
    self.indicator._render(self.rect)
    self.draw.assert_not_called()

    self.state.cluster_connected = False
    self.indicator._render(self.rect)
    self.assertGreater(self._draw_alphas()['car'], 0.0)
    self.assertGreater(self._draw_alphas()['distance_1'], 0.0)
