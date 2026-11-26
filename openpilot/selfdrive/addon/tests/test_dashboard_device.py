import asyncio
import inspect
import math
import re
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from nicegui import Client, core, ui

from openpilot.cereal import log
from openpilot.selfdrive.addon import dashboard


def calibration_bytes(rpy, status='calibrated'):
  return log.Event.new_message(valid=True, extrinsicsCalibration={'calStatus': status, 'rpyCalib': rpy}).to_bytes()


def test_read_device_position_converts_persisted_calibration_to_degrees(monkeypatch):
  payload = calibration_bytes([math.radians(17), math.radians(2), math.radians(-3)])
  params = Mock(get=Mock(return_value=payload))
  monkeypatch.setattr(dashboard, 'params', params)

  assert dashboard._read_device_position() == pytest.approx((2.0, -3.0))
  params.get.assert_called_once_with('CalibrationParams')


@pytest.mark.parametrize('payload', [
  pytest.param(None, id='missing'),
  pytest.param(b'', id='empty'),
  pytest.param(b'corrupt calibration payload', id='corrupt'),
  pytest.param(log.Event.new_message(valid=True, logMessage='wrong cereal union').to_bytes(), id='wrong-union'),
  pytest.param(calibration_bytes([0.0, 0.01, 0.02], 'uncalibrated'), id='uncalibrated'),
  pytest.param(calibration_bytes([0.0, 0.01, 0.02], 'recalibrating'), id='recalibrating'),
  pytest.param(calibration_bytes([0.0, 0.01, 0.02], 'invalid'), id='invalid-status'),
  pytest.param(calibration_bytes([0.0, 0.01]), id='short-vector'),
  pytest.param(calibration_bytes([0.0, 0.01, 0.02, 0.03]), id='long-vector'),
  pytest.param(calibration_bytes([math.nan, 0.01, 0.02]), id='nan-roll'),
  pytest.param(calibration_bytes([0.0, -math.inf, 0.02]), id='inf-pitch'),
  pytest.param(calibration_bytes([0.0, 0.01, math.inf]), id='inf-yaw'),
])
def test_read_device_position_rejects_missing_or_unusable_calibration(monkeypatch, payload):
  monkeypatch.setattr(dashboard, 'params', Mock(get=Mock(return_value=payload)))

  assert dashboard._read_device_position() is None


def camera_rotation(html):
  assert 'device-camera-posed' in html
  angles = re.search(r'--pitch:\s*([+\-\d.]+)deg;\s*--yaw:\s*([+\-\d.]+)deg', html)
  assert angles is not None
  return float(angles[1]), float(angles[2])


def test_device_orientation_shows_measured_pose_and_when_to_correct_it():
  up_right = dashboard._device_position_html((-1.5, -3.2))
  assert camera_rotation(up_right) == pytest.approx((6.0, 12.8))
  assert '1.5° 위' in up_right and '3.2° 오른쪽' in up_right
  assert '렌즈를 아래로 기울이기' in up_right and '렌즈를 왼쪽으로 돌리기' in up_right

  down_left = dashboard._device_position_html((1.5, 3.2))
  assert camera_rotation(down_left) == pytest.approx((-6.0, -12.8))
  assert '1.5° 아래' in down_left and '3.2° 왼쪽' in down_left
  assert '렌즈를 위로 기울이기' in down_left and '렌즈를 오른쪽으로 돌리기' in down_left

  centered = dashboard._device_position_html((0.0, 0.0))
  assert camera_rotation(centered) == pytest.approx((0.0, 0.0))
  assert '0.0°' in centered and '기준 방향' in centered
  assert 'Roll' not in centered


def test_device_orientation_clamps_visual_tilt_and_omits_pose_if_unavailable():
  exaggerated = dashboard._device_position_html((-40.0, 100.0))
  assert camera_rotation(exaggerated) == pytest.approx((28.0, -28.0))
  assert '40.0° 위' in exaggerated and '100.0° 왼쪽' in exaggerated

  unavailable = dashboard._device_position_html(None)
  assert 'device-camera-posed' not in unavailable
  assert '표시할 보정 각도가 없습니다.' in unavailable
  assert 'Roll' not in unavailable


async def invoke(callback, *args):
  result = callback(*args)
  if inspect.isawaitable(result):
    await result


class TestDashboardDevice(unittest.IsolatedAsyncioTestCase):
  @contextmanager
  def device_page(self):
    client = Client(ui.page(f'/test-dashboard-device-{self._testMethodName}'))
    self.addCleanup(client.delete)
    tabs = SimpleNamespace(value='FUNCTIONS', on_value_change=Mock())
    payload = calibration_bytes([0.0, math.radians(-1.5), math.radians(-3.2)])
    values = {'CalibrationParams': payload}
    params = Mock(get=Mock(side_effect=values.get), remove=Mock(side_effect=lambda key: values.pop(key, None)))
    callbacks = {}
    timers = {}
    on_click = ui.button.on_click

    def capture(button, callback):
      callbacks[button.text] = callback
      return on_click(button, callback)

    def capture_timer(interval, callback, *, active=True, **_kwargs):
      handle = Mock(active=active)
      handle.activate.side_effect = lambda: setattr(handle, 'active', True)
      handle.deactivate.side_effect = lambda: setattr(handle, 'active', False)
      timers[interval] = SimpleNamespace(tick=callback, initially_active=active, handle=handle)
      return handle

    with client, patch.object(core, 'loop', asyncio.get_running_loop()), patch.object(dashboard, 'params', params), \
         patch.object(ui, 'timer', side_effect=capture_timer), patch.object(ui, 'notify') as notify, \
         patch.object(ui.button, 'on_click', capture):
      dashboard.render_tab_device(tabs)
      yield SimpleNamespace(
        client=client, tabs=tabs, params=params, callbacks=callbacks, timers=timers, notify=notify,
        position=next(element for element in client.elements.values()
                      if isinstance(element, ui.html) and element.props.get('id') == 'device-position'),
      )

  async def select_tab(self, page, tab):
    page.tabs.value = tab
    await invoke(page.tabs.on_value_change.call_args.args[0], SimpleNamespace(value=tab))

  async def test_position_refreshes_only_on_device_tab_and_reset_clears_pose(self):
    with self.device_page() as page:
      saved_timer = page.timers[1.0]
      self.assertFalse(saved_timer.initially_active)
      await invoke(saved_timer.tick)
      page.params.get.assert_not_called()
      self.assertNotIn('device-camera-posed', page.position.content)

      await self.select_tab(page, 'DEVICE')
      saved_timer.handle.activate.assert_called_once()
      page.params.get.assert_called_once_with('CalibrationParams')
      self.assertIn('device-camera-posed', page.position.content)

      await invoke(page.callbacks['RESET CALIBRATION'])
      self.assertEqual(page.params.remove.call_args_list, [call('CalibrationParams'), call('LiveTorqueParameters'),
                                                         call('LiveParametersV2'), call('LiveDelay')])
      page.params.put_bool.assert_called_once_with('OnroadCycleRequested', True)
      page.notify.assert_called_once()
      self.assertNotIn('device-camera-posed', page.position.content)
      await invoke(saved_timer.tick)
      self.assertNotIn('device-camera-posed', page.position.content)

      await self.select_tab(page, 'FUNCTIONS')
      saved_timer.handle.deactivate.assert_called_once()
      page.params.get.reset_mock()
      await invoke(saved_timer.tick)
      page.params.get.assert_not_called()

  async def test_live_adjustment_keeps_saved_position_and_stops_on_vehicle_movement(self):
    first_batch = ([(101, (0.0, 0.0, -9.81))], [(102, (0.0, 0.0, 0.0))])
    second_batch = ([(201, (0.0, 0.0, -9.81))], [(202, (0.0, 0.0, 0.0))])
    stream = Mock(read=Mock(side_effect=[first_batch, second_batch, RuntimeError('차량이 움직였습니다')]))
    adjustment = Mock(update=Mock(side_effect=[None, (2.0, -3.0)]))

    with patch.object(dashboard, 'get_mount_sensor_stream', return_value=stream), \
         patch.object(dashboard, 'MountAdjustment', return_value=adjustment), self.device_page() as page:
      live_timer = page.timers[0.1]
      self.assertFalse(live_timer.initially_active)
      await self.select_tab(page, 'DEVICE')
      saved_rotation = camera_rotation(page.position.content)
      self.assertEqual(saved_rotation, (6.0, 12.8))
      await invoke(live_timer.tick)
      stream.read.assert_not_called()

      await invoke(page.callbacks['기준 잡기'])
      live_timer.handle.activate.assert_called_once()
      await invoke(live_timer.tick)
      await invoke(live_timer.tick)
      self.assertEqual(stream.read.call_args, call(101, 102))
      self.assertEqual(adjustment.update.call_args_list, [call(*first_batch), call(*second_batch)])
      self.assertIn('기준 자세 대비', page.position.content)
      self.assertEqual(camera_rotation(page.position.content), (-8.0, 12.0))
      saved_title = next(element for element in page.client.elements.values()
                         if isinstance(element, ui.label) and element.text == '저장된 보정값')
      saved_row = saved_title.parent_slot.parent
      self.assertTrue(saved_row.visible)
      self.assertIn('Pitch 1.5° 위 · Yaw 3.2° 오른쪽', [label.text for label in saved_row if isinstance(label, ui.label)])

      await invoke(live_timer.tick)
      self.assertEqual(stream.read.call_args, call(201, 202))
      live_timer.handle.deactivate.assert_called_once()
      self.assertEqual(camera_rotation(page.position.content), saved_rotation)
      self.assertNotIn('기준 자세 대비', page.position.content)
      self.assertFalse(saved_row.visible)
      await invoke(live_timer.tick)
      self.assertEqual(stream.read.call_count, 3)

      await invoke(page.callbacks['기준 잡기'])
      self.assertTrue(live_timer.handle.active)
      await invoke(page.callbacks['조정 종료'])
      self.assertFalse(live_timer.handle.active)
      await invoke(live_timer.tick)
      self.assertEqual(stream.read.call_count, 3)
      page.params.remove.assert_not_called()
      page.params.put.assert_not_called()
      page.params.put_bool.assert_not_called()
