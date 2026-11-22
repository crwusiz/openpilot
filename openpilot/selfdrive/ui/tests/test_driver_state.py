import unittest
from types import SimpleNamespace
from unittest.mock import patch

import raylib_nb as rl

from openpilot.cereal import log
from openpilot.selfdrive.ui.onroad import driver_state


class TestBigDriverStateRenderer(unittest.TestCase):
  def setUp(self):
    self.sm = type('SubMaster', (dict,), {})(selfdriveState=SimpleNamespace(alertSize=driver_state.AlertSize.none))
    self.sm.alive = dict.fromkeys(('driverMonitoringState', 'driverStateV2'), False)
    self.sm.valid = dict.fromkeys(self.sm.alive, True)
    self.state = SimpleNamespace(sm=self.sm, engaged=False, received_since_started=lambda service: self.sm.alive[service])
    self.enterContext(patch.object(driver_state, 'ui_state', self.state))
    self.enterContext(patch.object(driver_state.gui_app, 'texture', return_value=SimpleNamespace(width=115, height=115)))
    self.enterContext(patch.object(rl, 'draw_circle'))
    self.draw_texture = self.enterContext(patch.object(rl, 'draw_texture_v'))
    self.draw_outline = self.enterContext(patch.object(rl, 'draw_spline_linear'))

  def test_onroad_icon_is_dim_when_driver_monitoring_data_stops(self):
    renderer = driver_state.DriverStateRenderer(always_visible=True)
    renderer.set_rect(rl.Rectangle(30, 30, 1000, 500))
    renderer._update_state()

    self.assertTrue(renderer.is_visible)
    renderer._render(renderer.rect)
    self.assertEqual(self.draw_texture.call_args.args[-1].a, 51)
    self.draw_outline.assert_not_called()

    self.sm.alive.update(driverMonitoringState=True, driverStateV2=True)
    self.sm['driverMonitoringState'] = SimpleNamespace(activePolicy=log.DriverMonitoringState.MonitoringPolicy.vision, isRHD=False)
    self.sm['driverStateV2'] = SimpleNamespace(leftDriverData=SimpleNamespace(faceOrientation=[0.0, 0.0, 0.0]))
    renderer._update_state()
    renderer._render(renderer.rect)
    self.assertEqual(self.draw_texture.call_args.args[-1].a, 165)
    self.draw_outline.assert_called_once()

    # Cached messages may still exist while DM is disabled or its feed has stopped.
    self.sm.alive['driverStateV2'] = False
    self.draw_outline.reset_mock()
    renderer._update_state()
    renderer._render(renderer.rect)
    self.assertEqual(self.draw_texture.call_args.args[-1].a, 51)
    self.draw_outline.assert_not_called()

  def test_cabin_preview_still_waits_for_driver_data_and_alerts_hide_the_onroad_icon(self):
    self.assertFalse(driver_state.DriverStateRenderer().is_visible)
    onroad = driver_state.DriverStateRenderer(always_visible=True)
    self.sm['selfdriveState'].alertSize = driver_state.AlertSize.mid
    self.assertFalse(onroad.is_visible)
