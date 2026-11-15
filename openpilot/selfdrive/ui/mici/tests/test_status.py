import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import raylib_nb as rl

from openpilot.selfdrive.ui.mici.onroad import status
from openpilot.selfdrive.ui.ui_state import UIStatus


class TestStatusFade(unittest.TestCase):
  def setUp(self):
    self.ui_state = SimpleNamespace(status=UIStatus.DISENGAGED, sm=SimpleNamespace(frame=0))
    state_patch = patch.object(status, 'ui_state', self.ui_state)
    state_patch.start()
    self.addCleanup(state_patch.stop)
    time_patch = patch.object(rl, 'get_time', return_value=0.0)
    self.clock = time_patch.start()
    self.addCleanup(time_patch.stop)
    self.colors = {
      UIStatus.DISENGAGED: rl.Color(100, 100, 100, 255),
      UIStatus.ENGAGED: rl.Color(0, 255, 64, 255),
      UIStatus.OVERRIDE: rl.Color(255, 255, 255, 255),
    }

  def assert_color_equal(self, actual, expected):
    self.assertEqual((actual.r, actual.g, actual.b, actual.a), (expected.r, expected.g, expected.b, expected.a))

  def test_base_palette_supports_all_ui_statuses(self):
    for ui_status in UIStatus:
      with self.subTest(status=ui_status):
        self.ui_state.status = ui_status
        fade = status.StatusFade()
        base_status = UIStatus.ENGAGED if ui_status == UIStatus.ACTIVE else ui_status
        expected = self.colors.get(base_status, self.colors[UIStatus.DISENGAGED])
        self.assert_color_equal(fade.color(self.colors), expected)

  def test_explicit_extended_colors_take_precedence(self):
    for ui_status in (UIStatus.RED, UIStatus.ACTIVE):
      with self.subTest(status=ui_status):
        self.ui_state.status = ui_status
        color = rl.Color(201, 34, 49, 200)
        self.assert_color_equal(status.StatusFade().color({**self.colors, ui_status: color}), color)

  def test_active_and_engaged_transition_preserves_engaged_appearance(self):
    self.ui_state.status = UIStatus.ACTIVE
    fade = status.StatusFade()
    self.assert_color_equal(fade.color(self.colors), self.colors[UIStatus.ENGAGED])
    for frame, ui_status in enumerate((UIStatus.ENGAGED, UIStatus.ACTIVE, UIStatus.ENGAGED), start=1):
      self.ui_state.sm.frame = frame
      self.ui_state.status = ui_status
      self.assert_color_equal(fade.color(self.colors), self.colors[UIStatus.ENGAGED])
      self.assertAlmostEqual(fade.weight(UIStatus.ENGAGED) + fade.weight(UIStatus.ACTIVE), 1.0)

  def test_extended_status_transition_blends_without_losing_opacity(self):
    fade = status.StatusFade()
    fade.color(self.colors)
    self.ui_state.sm.frame += 1
    self.ui_state.status = UIStatus.RED
    self.assert_color_equal(fade.color(self.colors), self.colors[UIStatus.DISENGAGED])
    self.ui_state.sm.frame += 1
    self.ui_state.status = UIStatus.ACTIVE
    color = fade.color(self.colors)
    self.assertGreater(color.g, self.colors[UIStatus.DISENGAGED].g)
    self.assertLess(color.g, self.colors[UIStatus.ENGAGED].g)
    self.assertEqual(color.a, 255)
    self.assertAlmostEqual(sum(fade.weight(s) for s in UIStatus), 1.0)
    weights = fade._update().copy()
    fade.color(self.colors)
    np.testing.assert_array_equal(fade._update(), weights)

  def test_override_is_immediate_and_holds_before_engaged(self):
    self.ui_state.status = UIStatus.ENGAGED
    fade = status.StatusFade()
    fade.color(self.colors)
    self.ui_state.sm.frame += 1
    self.ui_state.status = UIStatus.OVERRIDE
    self.assert_color_equal(fade.color(self.colors), self.colors[UIStatus.OVERRIDE])
    self.ui_state.sm.frame += 1
    self.ui_state.status = UIStatus.ENGAGED
    self.clock.return_value = status.OVERRIDE_HOLD / 2
    self.assert_color_equal(fade.color(self.colors), self.colors[UIStatus.OVERRIDE])
    self.ui_state.sm.frame += 1
    self.clock.return_value = status.OVERRIDE_HOLD + 0.01
    self.assertLess(fade.weight(UIStatus.OVERRIDE), 1.0)


if __name__ == '__main__':
  unittest.main()
