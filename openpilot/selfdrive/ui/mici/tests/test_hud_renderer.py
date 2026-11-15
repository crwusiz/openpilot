import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import raylib_nb as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad import hud_renderer as hud
from openpilot.selfdrive.ui.ui_state import ChestnutState, UIStatus


class FakeSubMaster(dict):
  pass


class TestHudRenderer(unittest.TestCase):
  def setUp(self):
    self.ui_state = SimpleNamespace(is_metric=False, status=UIStatus.ACTIVE, started=True,
                                    started_frame=0, cluster_enabled=False, chestnut_state=ChestnutState.ACTIVE)
    state_patch = patch.object(hud, 'ui_state', self.ui_state)
    state_patch.start()
    self.addCleanup(state_patch.stop)
    time_patch = patch.object(rl, 'get_time', return_value=100.0)
    self.clock = time_patch.start()
    self.addCleanup(time_patch.stop)
    translation_patch = patch.object(hud, 'tr', side_effect=lambda text: text)
    translation_patch.start()
    self.addCleanup(translation_patch.stop)
    self.disengaged_weight = 0.0
    fade_patch = patch.object(hud, 'status_fade', SimpleNamespace(
      weight=lambda status: self.disengaged_weight if status == UIStatus.DISENGAGED else 0.0))
    fade_patch.start()
    self.addCleanup(fade_patch.stop)

    self.renderer = hud.HudRenderer.__new__(hud.HudRenderer)
    self.renderer._engaged = False
    self.renderer.set_speed = hud.SET_SPEED_NA
    self.renderer.cruise_speed = 100.0
    self.renderer.apply_speed = 90.0
    self.renderer.v_ego_cluster_seen = False
    self.renderer._set_speed_changed_time = 99.5
    self.renderer._set_speed_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self.renderer._set_speed_alpha = 0.0
    self.renderer._chestnut_fade_time = 0.0
    self.renderer._chestnut_icon = None
    self.renderer._chestnut_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self.renderer._txt_chestnut = SimpleNamespace(width=48, height=35)
    self.renderer._txt_chestnut_green = SimpleNamespace(width=48, height=35)
    self.renderer._txt_chestnut_orange = SimpleNamespace(width=60, height=35)
    self.renderer._txt_wheel = SimpleNamespace(height=50)
    self.renderer._can_draw_top_icons = True
    self.renderer.is_cruise_set = True
    self.renderer.nda_state = 1
    self.renderer.stock_limit_speed = 0.0
    self.renderer._draw_text = Mock()
    self.rect = rl.Rectangle(0, 0, 200, 600)

    self.draws = {}
    for name in ('draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_texture_ex'):
      draw_patch = patch.object(rl, name)
      self.draws[name] = draw_patch.start()
      self.addCleanup(draw_patch.stop)

  def test_imperial_speed_does_not_restart_persistence_every_frame(self):
    car_state = SimpleNamespace(vCruiseCluster=100.0, vCruise=90.0, vEgoCluster=20.0, vEgo=20.0,
                                aEgo=0.5, speedLimit=100.0, ignoreLimitTimer=0.0)
    selfdrive_state = SimpleNamespace(enabled=True)
    self.ui_state.sm = FakeSubMaster(carState=car_state, selfdriveState=selfdrive_state,
                                    naviData=SimpleNamespace(active=1), longitudinalPlan=SimpleNamespace(trafficState=2))
    self.ui_state.sm.recv_frame = {'carState': 1}
    self.renderer._update_state()
    self.assertEqual(self.renderer.set_speed, 100.0)
    self.assertEqual(self.renderer.cruise_speed, 100.0)
    self.assertEqual(self.renderer.apply_speed, 90.0)
    self.assertEqual(self.renderer._set_speed_changed_time, 100.0)
    self.assertEqual(self.renderer._chestnut_fade_time, 100.0)

    self.clock.return_value = 101.0
    self.renderer._update_state()
    self.assertEqual(self.renderer._set_speed_changed_time, 100.0)
    self.assertEqual(self.renderer._chestnut_fade_time, 100.0)
    selfdrive_state.enabled = False
    self.renderer._update_state()
    self.assertEqual(self.renderer._chestnut_fade_time, 0.0)
    self.clock.return_value = 102.0
    selfdrive_state.enabled = True
    self.renderer._update_state()
    self.assertEqual(self.renderer._chestnut_fade_time, 102.0)

  def test_hidden_speed_icons_finish_fading_and_cluster_hides_them(self):
    self.renderer._set_speed_alpha_filter.x = 1.0
    self.renderer._set_speed_alpha = 1.0
    self.renderer._can_draw_top_icons = False
    for _ in range(60):
      self.renderer._draw_set_speed(self.rect)
    self.assertLess(self.renderer._set_speed_alpha_filter.x, 1e-2)
    self.assertFalse(self.renderer.drawing_top_icons())

    self.renderer._can_draw_top_icons = True
    self.renderer._set_speed_alpha_filter.x = 1.0
    self.renderer._set_speed_alpha = 1.0
    self.ui_state.cluster_enabled = True
    self.renderer._draw_text.reset_mock()
    self.renderer._draw_set_speed(self.rect)
    self.assertLess(self.renderer._set_speed_alpha_filter.x, 1.0)
    self.assertFalse(self.renderer.drawing_top_icons())
    self.renderer._draw_text.assert_not_called()

  def test_speed_boxes_convert_display_values_and_apply_fade_alpha(self):
    self.renderer._set_speed_alpha_filter.x = 0.5
    self.renderer._draw_set_speed(self.rect)
    texts = self.renderer._draw_text.call_args_list
    self.assertEqual([call.args[2] for call in texts], ['MAX', '62', 'SET', '56'])
    alpha = self.renderer._set_speed_alpha
    self.assertGreater(alpha, 0.0)
    self.assertLess(alpha, 1.0)
    self.assertTrue(all(call.args[4].a == round(255 * alpha) for call in texts))
    for call in self.draws['draw_rectangle_rounded'].call_args_list:
      self.assertEqual(call.args[-1].a, round(100 * alpha))
    for call in self.draws['draw_rectangle_rounded_lines_ex'].call_args_list:
      self.assertEqual(call.args[-1].a, round(200 * alpha))
    self.assertEqual(self.renderer.cruise_speed, 100.0)
    self.assertEqual(self.renderer.apply_speed, 90.0)

  def test_chestnut_icon_events_reset_persistence_and_loading_stays_visible(self):
    self.renderer._draw_model_source(self.rect)
    self.assertIs(self.renderer._chestnut_icon, self.renderer._txt_chestnut_green)
    self.assertEqual(self.renderer._chestnut_fade_time, 100.0)
    active_alpha = self.renderer._chestnut_alpha_filter.x
    self.clock.return_value = 104.0
    self.renderer._draw_model_source(self.rect)
    self.assertLess(self.renderer._chestnut_alpha_filter.x, active_alpha)

    self.ui_state.chestnut_state = ChestnutState.FAILED
    self.renderer._draw_model_source(self.rect)
    self.assertIs(self.renderer._chestnut_icon, self.renderer._txt_chestnut_orange)
    self.assertEqual(self.renderer._chestnut_fade_time, 104.0)
    self.ui_state.chestnut_state = ChestnutState.LOADING
    self.renderer._draw_model_source(self.rect)
    loading_alpha = self.renderer._chestnut_alpha_filter.x
    self.clock.return_value = 110.0
    self.renderer._draw_model_source(self.rect)
    self.assertGreater(self.renderer._chestnut_alpha_filter.x, loading_alpha)

    self.ui_state.chestnut_state = ChestnutState.DISCONNECTED
    loading_alpha = self.renderer._chestnut_alpha_filter.x
    self.draws['draw_texture_ex'].reset_mock()
    self.renderer._draw_model_source(self.rect)
    self.assertLess(self.renderer._chestnut_alpha_filter.x, loading_alpha)
    self.assertIsNone(self.renderer._chestnut_icon)
    self.draws['draw_texture_ex'].assert_not_called()


if __name__ == '__main__':
  unittest.main()
