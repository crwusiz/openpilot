import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import raylib_nb as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad import left_column_rect
from openpilot.selfdrive.ui.mici.onroad import hud_renderer as hud
from openpilot.selfdrive.ui.ui_state import ChestnutState, UIStatus


class FakeSubMaster(dict):
  pass


class TestHudRenderer(unittest.TestCase):
  def setUp(self):
    self.ui_state = SimpleNamespace(is_metric=False, status=UIStatus.ACTIVE, started=True,
                                    started_frame=0, cluster_enabled=False, cluster_connected=False,
                                    chestnut_state=ChestnutState.ACTIVE,
                                    received_since_started=lambda service: True)
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
    self.renderer.set_speed = hud.SET_SPEED_NA
    self.renderer.cruise_speed = 100.0
    self.renderer.apply_speed = 90.0
    self.renderer.v_ego_cluster_seen = False
    self.renderer._set_speed_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self.renderer._set_speed_alpha = 0.0
    self.renderer._chestnut_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / 60)
    self.renderer._chestnut = SimpleNamespace(width=48, height=35)
    self.renderer._chestnut_green = SimpleNamespace(width=48, height=35)
    self.renderer._chestnut_orange = SimpleNamespace(width=60, height=35)
    self.renderer._wheel = SimpleNamespace(width=50, height=50)
    self.renderer._wheel_green = SimpleNamespace(width=50, height=50)
    self.renderer._wheel_blue = SimpleNamespace(width=50, height=50)
    self.renderer._wheel_critical = SimpleNamespace(width=50, height=50)
    self.renderer._exclamation_point = SimpleNamespace(width=9, height=44)
    self.renderer._wheel_alpha_filter = FirstOrderFilter(0, 0.05, 1 / 60)
    self.renderer._show_wheel_critical = False
    self.renderer._can_draw_top_icons = True
    self.renderer.is_cruise_set = True
    self.renderer.nda_state = 1
    self.renderer.stock_limit_speed = 0.0
    self.renderer._draw_text = Mock()
    self.rect = rl.Rectangle(0, 0, 200, 600)

    self.draws = {}
    for name in ('draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_texture_ex', 'draw_texture_pro'):
      draw_patch = patch.object(rl, name)
      self.draws[name] = draw_patch.start()
      self.addCleanup(draw_patch.stop)

  def test_imperial_speed_does_not_reconvert_state_every_frame(self):
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
    self.clock.return_value = 101.0
    self.renderer._update_state()
    self.assertEqual(self.renderer.set_speed, 100.0)
    self.assertEqual(self.renderer.cruise_speed, 100.0)
    self.assertEqual(self.renderer.apply_speed, 90.0)

  def test_speed_icons_hide_only_while_cluster_is_connected(self):
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
    self.renderer._draw_set_speed(self.rect)
    self.assertTrue(self.renderer.drawing_top_icons())

    self.ui_state.cluster_connected = True
    self.renderer._draw_text.reset_mock()
    self.renderer._draw_set_speed(self.rect)
    self.assertLess(self.renderer._set_speed_alpha_filter.x, 1.0)
    self.assertFalse(self.renderer.drawing_top_icons())
    self.renderer._draw_text.assert_not_called()

    self.ui_state.cluster_connected = False
    self.renderer._draw_set_speed(self.rect)
    self.assertTrue(self.renderer.drawing_top_icons())

  def test_wheel_and_traffic_icons_return_on_disconnect(self):
    for name in ("_draw_current_speed", "_draw_borders", "_draw_set_speed", "_draw_ignore_limit_timer", "_draw_model_source",
                 "_draw_traffic_light", "_draw_steering_wheel"):
      setattr(self.renderer, name, Mock())
    self.ui_state.cluster_enabled = True
    self.renderer._render(self.rect)
    self.ui_state.cluster_connected = True
    self.renderer._render(self.rect)
    self.renderer._draw_traffic_light.assert_called_once()
    self.renderer._draw_steering_wheel.assert_called_once()
    self.ui_state.cluster_connected = False
    self.renderer._render(self.rect)
    self.assertEqual(self.renderer._draw_traffic_light.call_count, 2)
    self.assertEqual(self.renderer._draw_steering_wheel.call_count, 2)

  def test_current_speed_and_unit_hide_only_while_cluster_is_connected(self):
    self.renderer.speed = 45.0
    self.renderer.accel = 0.0
    self.renderer._font_bold = object()
    self.renderer._font_medium = object()
    self.ui_state.cluster_enabled = True

    with (patch.object(rl, 'draw_text_ex') as draw_text,
          patch.object(hud, 'measure_text_cached', return_value=rl.Vector2(40, 20))):
      for cluster_connected, expected_text in ((False, ['45', 'mph']), (True, []), (False, ['45', 'mph'])):
        with self.subTest(cluster_connected=cluster_connected):
          self.ui_state.cluster_connected = cluster_connected
          draw_text.reset_mock()
          self.renderer._draw_current_speed(self.rect)
          self.assertEqual([call.args[1] for call in draw_text.call_args_list], expected_text)

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

  def test_speed_and_chestnut_stay_visible_after_a_few_seconds(self):
    self.renderer._draw_set_speed(self.rect)
    speed_alpha = self.renderer._set_speed_alpha
    self.renderer._draw_model_source(self.rect)
    self.assertIs(self.draws['draw_texture_ex'].call_args.args[0], self.renderer._chestnut_green)
    active_alpha = self.renderer._chestnut_alpha_filter.x
    self.clock.return_value = 104.0
    self.renderer._draw_set_speed(self.rect)
    self.assertGreater(self.renderer._set_speed_alpha, speed_alpha)
    self.renderer._draw_model_source(self.rect)
    self.assertGreater(self.renderer._chestnut_alpha_filter.x, active_alpha)

    self.ui_state.chestnut_state = ChestnutState.FAILED
    self.renderer._draw_model_source(self.rect)
    self.assertIs(self.draws['draw_texture_ex'].call_args.args[0], self.renderer._chestnut_orange)
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
    self.draws['draw_texture_ex'].assert_not_called()

  def test_wheel_uses_status_images_beside_driver_monitoring(self):
    self.ui_state.sm = FakeSubMaster(carState=SimpleNamespace(steeringAngleDeg=15.0))
    for status, texture in ((UIStatus.DISENGAGED, self.renderer._wheel),
                            (UIStatus.ENGAGED, self.renderer._wheel_green),
                            (UIStatus.ACTIVE, self.renderer._wheel_green),
                            (UIStatus.STEERING, self.renderer._wheel_blue),
                            (UIStatus.BLINKER, self.renderer._wheel_critical)):
      with self.subTest(status=status):
        self.ui_state.status = status
        self.draws['draw_texture_pro'].reset_mock()
        self.renderer._draw_steering_wheel(self.rect)
        self.draws['draw_texture_pro'].assert_called_once()
        drawn_texture, _, dest, origin, rotation, tint = self.draws['draw_texture_pro'].call_args.args
        self.assertIs(drawn_texture, texture)
        dm_rect = left_column_rect(self.rect, 2)
        self.assertGreater(dest.x - origin.x, dm_rect.x + dm_rect.width)
        self.assertEqual(dest.y - origin.y, dm_rect.y)
        self.assertEqual(rotation, -15.0)
        self.assertEqual((tint.r, tint.g, tint.b), (255, 255, 255))

    self.renderer._show_wheel_critical = True
    self.ui_state.status = UIStatus.DISENGAGED
    self.renderer._draw_steering_wheel(self.rect)
    self.assertIs(self.draws['draw_texture_pro'].call_args.args[0], self.renderer._wheel_critical)


if __name__ == '__main__':
  unittest.main()
