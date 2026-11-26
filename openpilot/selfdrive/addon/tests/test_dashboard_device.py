import asyncio
import inspect
import math
import re
import unittest
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
  assert '측정값 없음' in centered


def test_device_orientation_clamps_visual_tilt_and_omits_pose_if_unavailable():
  exaggerated = dashboard._device_position_html((-40.0, 100.0))
  assert camera_rotation(exaggerated) == pytest.approx((28.0, -28.0))
  assert '40.0° 위' in exaggerated and '100.0° 왼쪽' in exaggerated

  unavailable = dashboard._device_position_html(None)
  assert 'device-camera-posed' not in unavailable
  assert '표시할 보정 각도가 없습니다.' in unavailable
  assert '측정값 없음' in unavailable


async def invoke(callback, *args):
  result = callback(*args)
  if inspect.isawaitable(result):
    await result


class TestDashboardDevice(unittest.IsolatedAsyncioTestCase):
  async def test_position_refreshes_only_on_device_tab_and_reset_clears_pose(self):
    client = Client(ui.page('/test-dashboard-device-position'))
    self.addCleanup(client.delete)
    tabs = SimpleNamespace(value='FUNCTIONS', on_value_change=Mock())
    payload = calibration_bytes([0.0, math.radians(2), math.radians(-3)])
    values = {'CalibrationParams': payload}
    params = Mock(get=Mock(side_effect=values.get), remove=Mock(side_effect=lambda key: values.pop(key, None)))
    callbacks = {}
    on_click = ui.button.on_click

    def capture(button, callback):
      callbacks[button.text] = callback
      return on_click(button, callback)

    with client, patch.object(core, 'loop', asyncio.get_running_loop()), patch.object(dashboard, 'params', params), \
         patch.object(ui, 'timer') as timer, patch.object(ui, 'notify') as notify, patch.object(ui.button, 'on_click', capture):
      dashboard.render_tab_device(tabs)
      self.assertFalse(timer.call_args.kwargs['active'])
      refresh = timer.call_args.args[1]
      tab_change = tabs.on_value_change.call_args.args[0]
      position = next(element for element in client.elements.values()
                      if isinstance(element, ui.html) and 'device-position' in element.content)

      await invoke(refresh)
      params.get.assert_not_called()
      self.assertNotIn('device-camera-posed', position.content)

      tabs.value = 'DEVICE'
      await invoke(tab_change, SimpleNamespace(value='DEVICE'))
      timer.return_value.activate.assert_called_once()
      params.get.assert_called_once_with('CalibrationParams')
      self.assertIn('device-camera-posed', position.content)

      await invoke(callbacks['RESET CALIBRATION'])
      self.assertEqual(params.remove.call_args_list, [call('CalibrationParams'), call('LiveTorqueParameters'),
                                                    call('LiveParametersV2'), call('LiveDelay')])
      params.put_bool.assert_called_once_with('OnroadCycleRequested', True)
      notify.assert_called_once()
      self.assertNotIn('device-camera-posed', position.content)
      await invoke(refresh)
      self.assertNotIn('device-camera-posed', position.content)

      tabs.value = 'FUNCTIONS'
      await invoke(tab_change, SimpleNamespace(value='FUNCTIONS'))
      timer.return_value.deactivate.assert_called_once()
      params.get.reset_mock()
      await invoke(refresh)
      params.get.assert_not_called()
