import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import raylib_nb as rl

from openpilot.selfdrive.ui.mici.onroad import model_renderer, status
from openpilot.selfdrive.ui.ui_state import UIStatus
from openpilot.system.ui.lib.polygon import Gradient


def planned_model(velocity=(4.0, 2.0, 0.5, 0.0), position=(0.0, 10.0, 20.0, 30.0)):
  return SimpleNamespace(velocity=SimpleNamespace(x=velocity), position=SimpleNamespace(x=position), leadsV3=[])


class TestStopPoint(unittest.TestCase):
  def test_planned_stop_interpolates_speed_threshold(self):
    self.assertAlmostEqual(model_renderer.StopPoint._get_planned_stop(planned_model()), 50 / 3)

  def test_absent_or_mismatched_stop_predictions(self):
    for model in (planned_model((4.0, 3.0), (0.0, 10.0)), planned_model((), ()), planned_model((2.0, 0.0), (0.0,))):
      with self.subTest(model=model):
        self.assertIsNone(model_renderer.StopPoint._get_planned_stop(model))
    self.assertEqual(model_renderer.StopPoint._get_planned_stop(planned_model((0.0,), (0.0,))), 0.0)

  def test_stop_persists_moves_with_car_and_hides_behind_lead(self):
    stop = model_renderer.StopPoint()
    model = planned_model()
    self.assertFalse(stop.update(model, True, 0.0, []))
    for _ in range(120):
      stop.update(model, True, 0.0, [])
    self.assertTrue(stop.visible)
    distance = stop.d
    stop.update(model, False, 6.0, [])
    self.assertAlmostEqual(stop.d, distance - 6.0 / model_renderer.gui_app.target_fps)
    for _ in range(120):
      stop.update(model, True, 0.0, [10.0])
    self.assertFalse(stop.visible)

  def test_stop_does_not_first_appear_too_close_or_too_far(self):
    for distance in (4.0, 80.0):
      with self.subTest(distance=distance):
        stop = model_renderer.StopPoint()
        model = planned_model((2.0, 0.0), (0.0, 2 * distance))
        for _ in range(120):
          stop.update(model, True, 0.0, [])
        self.assertFalse(stop.visible)


class TestModelRendererUpdates(unittest.TestCase):
  def make_renderer(self, longitudinal_control):
    renderer = model_renderer.ModelRenderer.__new__(model_renderer.ModelRenderer)
    renderer._longitudinal_control = longitudinal_control
    renderer._lead_bars = [model_renderer.LeadVehicle(), model_renderer.LeadVehicle()]
    renderer._lead_info = [model_renderer.LeadInfo(), model_renderer.LeadInfo()]
    renderer._stop_point = model_renderer.StopPoint()
    renderer._path = SimpleNamespace(raw_points=np.array([[0, 0, 0], [100, 0, 0]], dtype=np.float32))
    renderer._get_lead_bar = Mock(return_value=np.array([[0, 0], [0, 5], [5, 5], [5, 0]], dtype=np.float32))
    return renderer

  def make_sm(self, lead_present=False):
    lead = SimpleNamespace(present=lead_present, dRel=30.0, yRel=0.0, vRel=-2.0)
    sm = {
      'longitudinalPlan': SimpleNamespace(longitudinalPlanSource=None),
      'modelV2': planned_model(),
      'radarState': SimpleNamespace(leadOne=lead, leadTwo=SimpleNamespace(present=False, dRel=0.0, yRel=0.0, vRel=0.0)),
      'selfdriveState': SimpleNamespace(enabled=True, engageable=True),
      'carState': SimpleNamespace(brakePressed=False, vEgo=0.0),
    }
    return type('SubMaster', (dict,), {'updated': {'modelV2': True}})(sm)

  def test_planned_stop_is_updated_and_projected_without_lane_lines(self):
    renderer = self.make_renderer(longitudinal_control=True)
    with patch.object(model_renderer.status_fade, 'weight', return_value=0.0):
      for _ in range(120):
        renderer._update_lead_bars(self.make_sm())
    self.assertGreater(renderer._stop_point.fade_filter.x, 0.5)
    self.assertEqual(renderer._stop_point.bar.shape, (4, 2))
    lane, distance, lateral_offset = renderer._get_lead_bar.call_args.args
    self.assertIs(lane, renderer._path.raw_points)
    self.assertAlmostEqual(distance, 50 / 3 + model_renderer.RADAR_TO_CAMERA)
    self.assertEqual(lateral_offset, 0.0)

  def test_stock_longitudinal_keeps_radar_lead_labels_without_stop_bar(self):
    renderer = self.make_renderer(longitudinal_control=False)
    with patch.object(model_renderer.status_fade, 'weight', return_value=0.0):
      for _ in range(120):
        renderer._update_lead_bars(self.make_sm(lead_present=True))
    self.assertGreater(renderer._lead_bars[0].fade_filter.x, 0.5)
    self.assertEqual(renderer._lead_info[0].d_rel, 30.0)
    self.assertEqual(renderer._lead_info[0].v_rel, -2.0)
    self.assertEqual(renderer._stop_point.fade_filter.x, 0.0)

  def test_lane_colors_support_all_extended_statuses_and_visibility_fade(self):
    renderer = model_renderer.ModelRenderer.__new__(model_renderer.ModelRenderer)
    renderer._visible = 0.5
    renderer._torque_filter = SimpleNamespace(x=0.0)
    for ui_status in UIStatus:
      with self.subTest(status=ui_status):
        ui_state = SimpleNamespace(status=ui_status, sm=SimpleNamespace(frame=0))
        with (patch.object(status, 'ui_state', ui_state), patch.object(status.rl, 'get_time', return_value=0.0),
              patch.object(model_renderer, 'status_fade', status.StatusFade())):
          color = renderer._get_ll_color(1.0, adjacent=True, left=True)
        self.assertEqual(color.a, int(0.7 * 0.5 * 255))
        if ui_status in (UIStatus.ENGAGED, UIStatus.ACTIVE):
          self.assertEqual((color.r, color.g, color.b), (0, 255, 64))

  def test_custom_steering_path_gradient_fades_without_mutating_source(self):
    renderer = model_renderer.ModelRenderer.__new__(model_renderer.ModelRenderer)
    renderer._rect = rl.Rectangle(0, 0, 100, 100)
    renderer._path = SimpleNamespace(projected_points=np.array([[0, 0], [0, 5], [5, 5]], dtype=np.float32))
    renderer._visible = 0.5
    renderer._steering_pressed_gradient = Gradient((0, 1), (0, 0), [rl.Color(0, 100, 255, 200)], [0.0])
    ui_state = SimpleNamespace(status=UIStatus.STEERING, enabled=True, steeringPressed=True)
    with patch.object(model_renderer, 'ui_state', ui_state), patch.object(model_renderer, 'draw_polygon') as draw:
      renderer._draw_path()
    self.assertEqual(draw.call_args.kwargs['gradient'].colors[0].a, 100)
    self.assertEqual(renderer._steering_pressed_gradient.colors[0].a, 200)


if __name__ == '__main__':
  unittest.main()
