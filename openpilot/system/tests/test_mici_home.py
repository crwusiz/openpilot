"""Exercise the home update button without loading a window or native services."""

import importlib.util
from collections import namedtuple
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def home_module(monkeypatch):
  @dataclass
  class Vector2:
    x: float
    y: float

  def check_collision_point_rec(point, rect):
    if not isinstance(point, Vector2):
      raise TypeError('point must be a Vector2')
    return rect.x <= point.x <= rect.x + rect.width and rect.y <= point.y <= rect.y + rect.height

  colors = SimpleNamespace(WARNING='warning', RED='red', LIME='lime')
  network_types = SimpleNamespace(none=0, wifi=1, cell2G=2, cell3G=3, cell4G=4, cell5G=5, ethernet=6)
  dependencies = {
    'raylib_nb': {'WHITE': 'white', 'Vector2': Vector2, 'check_collision_point_rec': check_collision_point_rec},
    'openpilot.cereal': {'log': SimpleNamespace(DeviceState=SimpleNamespace(NetworkType=network_types))},
    'openpilot.system.ui.widgets': {'Widget': object},
    'openpilot.system.ui.widgets.layouts': {'HBoxLayout': object},
    'openpilot.system.ui.widgets.icon_widget': {'IconWidget': object},
    'openpilot.system.ui.widgets.label': {'UnifiedLabel': object, 'gui_label': Mock()},
    'openpilot.system.ui.lib.application': {
      'gui_app': Mock(), 'FontWeight': Mock(), 'MousePos': namedtuple('MousePos', 'x y'),
      'TextAlignment': Mock(), 'TextAlignmentVertical': Mock(),
    },
    'openpilot.selfdrive.ui.ui_state': {'ui_state': Mock(), 'ChestnutState': Mock()},
    'openpilot.common.version': {'RELEASE_BRANCHES': []},
    'openpilot.common.params': {'Params': Mock()},
    'openpilot.selfdrive.ui': {'Colors': colors, 'colors_alpha': Mock()},
    'openpilot.system.ui.lib.text_measure': {'measure_text_cached': Mock()},
    'openpilot.system.ui.widgets.network': {'WifiManagerUI': Mock(), 'WifiManager': Mock()},
  }
  for name, attributes in dependencies.items():
    dependency = ModuleType(name)
    dependency.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, dependency)

  spec = importlib.util.spec_from_file_location('_mici_home_update_test', Path(__file__).parents[2] / 'selfdrive/ui/mici/layouts/home.py')
  module = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, spec.name, module)
  spec.loader.exec_module(module)

  # Replace only this module's dependencies; other tests can still use the real
  # subprocess, threading, and time modules.
  module.subprocess = SimpleNamespace(run=Mock(), Popen=Mock(), TimeoutExpired=subprocess.TimeoutExpired)
  module.threading = SimpleNamespace(Thread=lambda *, target, daemon: SimpleNamespace(start=target))
  module.time = SimpleNamespace(monotonic=time.monotonic, sleep=Mock())
  return module


@pytest.fixture
def home(home_module, tmp_path):
  layout = home_module.MiciHomeLayout.__new__(home_module.MiciHomeLayout)
  layout._is_processing = False
  layout._commit_results = SimpleQueue()
  layout._is_update_available = False
  layout._initial_commit_check_done = False
  layout._progress_frame = 0
  layout._last_progress_update = 0
  layout._commit_status = home_module.MetricData('UPDATE', 'CHECK', home_module.Colors.WARNING)
  layout._params = Mock()
  layout._params.get.return_value = '"aaaaaaa" == "aaaaaaa"'
  layout._commit_check_exit_flag = tmp_path / 'commit_check_exit_flag'
  layout._git_pull_exit_flag = tmp_path / 'git_pull_exit_flag'
  layout._is_network_connected = lambda: True
  return layout


@pytest.mark.parametrize('position, alert_count, update_available, expected_action', [
  ((232, 132), 0, False, 'settings'),
  ((700, 250), 0, False, 'check'),
  ((700, 250), 0, True, 'pull'),
  ((700, 250), 1, True, 'pull'),  # update button takes precedence over the alerts zone
  ((800, 180), 1, False, 'alerts'),
  ((800, 180), 0, False, 'settings'),
  ((400, 200), 1, False, 'settings'),
])
def test_home_touch_routes_mousepos_to_the_correct_action(home_module, home, position, alert_count, update_available, expected_action):
  home.rect = SimpleNamespace(x=200, y=100, width=640, height=200)
  home._settings_icon = SimpleNamespace(rect=SimpleNamespace(x=208, y=108, width=48, height=48))
  home._commit_btn_rect = SimpleNamespace(x=660, y=228, width=160, height=64)
  home._alert_count_callback = lambda: alert_count
  home._is_update_available = update_available
  actions = {action: Mock() for action in ('settings', 'alerts', 'check', 'pull')}
  home._on_settings_click = actions['settings']
  home._on_alerts_click = actions['alerts']
  home._start_commit_check = actions['check']
  home._start_git_pull = actions['pull']

  home._handle_mouse_release(home_module.MousePos(*position))

  for action, callback in actions.items():
    if action == expected_action:
      callback.assert_called_once_with()
    else:
      callback.assert_not_called()


@pytest.mark.parametrize('busy, connected', [(True, True), (False, False)])
def test_button_does_not_start_work_when_busy_or_offline(home_module, home, busy, connected):
  home._is_processing = busy
  home._is_network_connected = lambda: connected
  home._handle_commit_button_press()
  home_module.subprocess.run.assert_not_called()
  home_module.subprocess.Popen.assert_not_called()
  assert home._commit_status.value == ('WAIT' if busy else 'OFFLINE')


def test_check_button_runs_bash_and_displays_completed_result(home_module, home):
  home._commit_check_exit_flag.write_text('1')  # a previous attempt must not win

  def compare(command, **kwargs):
    assert not home._commit_check_exit_flag.exists()
    home._commit_check_exit_flag.write_text('0')
    return subprocess.CompletedProcess(command, 0)

  home_module.subprocess.run.side_effect = compare
  home._handle_commit_button_press()
  home._process_commit_results()
  home_module.subprocess.run.assert_called_once_with(
    ['/bin/bash', '/data/openpilot/scripts/commit_compare.sh'], timeout=15, check=False,
  )
  assert not home._is_processing
  assert not home._is_update_available
  assert (home._commit_status.label, home._commit_status.value) == ('UP TO DATE', 'aaaaaaa')
  assert not home._commit_check_exit_flag.exists()


def test_failed_check_does_not_wait_for_a_completion_file(home_module, home):
  home._is_update_available = True
  home_module.subprocess.run.return_value = subprocess.CompletedProcess([], 1)
  home._start_commit_check()
  home._process_commit_results()
  assert not home._is_processing
  assert not home._is_update_available
  assert home._commit_status.value == 'CHECK FAILED'
  home._params.get.assert_not_called()
  home_module.time.sleep.assert_not_called()


def test_check_timeout_releases_button_for_retry(home_module, home):
  home_module.subprocess.run.side_effect = subprocess.TimeoutExpired('bash', 15)
  home._start_commit_check()
  home._process_commit_results()
  assert not home._is_processing
  assert not home._is_update_available
  assert home._commit_status.value == 'TIMEOUT'


@pytest.mark.parametrize('completion_status, expected', [
  (None, 'FILE READ ERROR'), ('invalid', 'FILE READ ERROR'), ('1', 'CHECK FAILED'), ('124', 'CHECK FAILED'),
])
def test_successful_process_requires_a_valid_success_completion_flag(home_module, home, completion_status, expected):
  home._is_update_available = True

  def compare(command, **kwargs):
    if completion_status is not None:
      home._commit_check_exit_flag.write_text(completion_status)
    return subprocess.CompletedProcess(command, 0, stdout='', stderr='Commit check diagnostic')

  home_module.subprocess.run.side_effect = compare
  home._start_commit_check()
  home._process_commit_results()

  assert not home._is_processing
  assert not home._is_update_available
  assert home._commit_status.value == expected
  home._params.get.assert_not_called()


def test_failed_check_can_retry_with_empty_stdout_and_full_hash_result(home_module, home):
  local = 'a' * 40
  remote = 'a' * 7 + 'b' * 33

  def compare(command, **kwargs):
    status = 128 if home_module.subprocess.run.call_count == 1 else 0
    home._commit_check_exit_flag.write_text(str(status))
    if status == 0:
      home._params.get.return_value = f'"{local}" != "{remote}"'
    return subprocess.CompletedProcess(command, status, stdout='', stderr='Commit check diagnostic')

  home_module.subprocess.run.side_effect = compare
  home._handle_commit_button_press()
  home._process_commit_results()
  assert not home._is_processing
  assert not home._is_update_available
  assert home._commit_status.value == 'CHECK FAILED'
  home._params.get.assert_not_called()

  home._handle_commit_button_press()
  home._process_commit_results()
  assert home_module.subprocess.run.call_count == 2
  assert not home._is_processing
  assert home._is_update_available
  assert home._commit_status.label == home._commit_status.value == 'aaaaaaa'
  assert not home._commit_check_exit_flag.exists()

  home._start_git_pull = Mock()
  home._handle_commit_button_press()
  home._start_git_pull.assert_called_once_with()
  home_module.subprocess.run.assert_called_with(
    ['/bin/bash', '/data/openpilot/scripts/commit_compare.sh'], timeout=15, check=False,
  )


@pytest.mark.parametrize('operation', ['_start_commit_check', '_start_git_pull'])
def test_flag_cleanup_failure_releases_button(home_module, home, operation):
  blocked = Mock()
  blocked.unlink.side_effect = PermissionError('unwritable flag')
  if operation == '_start_commit_check':
    home._commit_check_exit_flag = blocked
  else:
    home._git_pull_exit_flag = blocked
  getattr(home, operation)()
  home._process_commit_results()
  assert not home._is_processing
  assert home._commit_status.color == home_module.Colors.RED
  home_module.subprocess.run.assert_not_called()
  home_module.subprocess.Popen.assert_not_called()


@pytest.mark.parametrize('operation', ['_start_commit_check', '_start_git_pull'])
def test_duplicate_start_does_not_spawn_another_worker(home_module, home, operation):
  home._is_processing = True
  home_module.threading.Thread = Mock()
  getattr(home, operation)()
  home_module.threading.Thread.assert_not_called()


@pytest.mark.parametrize('exit_code, expected', [('0', 'COMPLETE'), ('1', 'UPDATE FAILED')])
def test_update_button_uses_bash_and_honors_update_exit_code(home_module, home, exit_code, expected):
  home._is_update_available = True

  def pull(command):
    home._git_pull_exit_flag.write_text(exit_code)
    return Mock()

  home_module.subprocess.Popen.side_effect = pull
  home._handle_commit_button_press()
  home._process_commit_results()
  assert not home._is_processing
  assert home._commit_status.value == expected
  assert home._is_update_available is (exit_code != '0')
  assert not home._git_pull_exit_flag.exists()
  home_module.subprocess.Popen.assert_called_once_with(['/bin/bash', '/data/openpilot/scripts/gitpull.sh'])


def test_completed_check_is_applied_on_ui_frame_and_survives_spinner(home_module, home):
  def compare(command, **kwargs):
    home._commit_check_exit_flag.write_text('0')
    return subprocess.CompletedProcess(command, 0)

  home_module.subprocess.run.side_effect = compare
  home._start_commit_check()
  assert home._is_processing
  home._update_progress_indicator()
  assert home._commit_status.color == home_module.Colors.WARNING

  home._process_commit_results()
  assert not home._is_processing
  assert home._commit_status.label == 'UP TO DATE'
  home._update_progress_indicator()
  assert (home._commit_status.label, home._commit_status.value) == ('UP TO DATE', 'aaaaaaa')


@pytest.mark.parametrize('as_bytes', [False, True])
@pytest.mark.parametrize('different', [False, True])
def test_valid_full_hash_comparison_controls_next_button_action(home_module, home, as_bytes, different):
  local = 'a' * 40
  remote = 'b' * 40 if different else local
  output = f'"{local}" {"!=" if different else "=="} "{remote}"\n'
  home._parse_commit_compare_result(output.encode() if as_bytes else output)
  assert home._is_update_available is different
  assert home._commit_status.label == (local[:7] if different else 'UP TO DATE')
  assert home._commit_status.value == remote[:7]
  assert home._commit_status.color == (home_module.Colors.RED if different else home_module.Colors.LIME)


@pytest.mark.parametrize('output', [
  None, '', 'garbage', '"ERROR" != "ERROR"', '"aaaaaaa" != "aaaaaaa"', '"aaaaaaa" == "bbbbbbb"',
  '"" == ""', '"aaaaaaa" != "bbbbbbb" != "ccccccc"', b'\xff',
])
def test_invalid_comparison_cannot_enable_update(home_module, home, output):
  home._is_processing = True
  home._is_update_available = True
  home._parse_commit_compare_result(output)
  assert not home._is_processing
  assert not home._is_update_available
  assert home._commit_status.color == home_module.Colors.RED
