"""Exercise sidebar commit checks without a window or native services."""

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def sidebar_module(monkeypatch):
  colors = SimpleNamespace(WARNING='warning', RED='red', LIME='lime')
  network_types = SimpleNamespace(none=0, wifi=1, ethernet=2, cell2G=3, cell3G=4, cell4G=5, cell5G=6)
  dependencies = {
    'raylib_nb': {'WHITE': 'white', 'Color': object, 'Rectangle': lambda *_args: Mock()},
    'openpilot.cereal': {'log': SimpleNamespace(DeviceState=SimpleNamespace(NetworkType=network_types, ThermalStatus=Mock()))},
    'openpilot.selfdrive.ui.ui_state': {'ui_state': Mock(), 'ChestnutState': Mock()},
    'openpilot.system.ui.lib.application': {'gui_app': Mock(), 'FontWeight': Mock(), 'MousePos': object, 'FONT_SCALE': 1},
    'openpilot.system.ui.lib.multilang': {'tr': lambda text: text, 'tr_noop': lambda text: text},
    'openpilot.system.ui.lib.text_measure': {'measure_text_cached': Mock()},
    'openpilot.system.ui.widgets': {'Widget': object},
    'openpilot.common.params': {'Params': Mock()},
    'openpilot.selfdrive.ui': {'Colors': colors, 'colors_alpha': Mock()},
    'openpilot.system.ui.widgets.network': {'WifiManagerUI': Mock(), 'WifiManager': Mock()},
  }
  for name, attributes in dependencies.items():
    dependency = ModuleType(name)
    dependency.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, dependency)

  spec = importlib.util.spec_from_file_location('_sidebar_commit_test', Path(__file__).parents[2] / 'selfdrive/ui/layouts/sidebar.py')
  module = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, spec.name, module)
  spec.loader.exec_module(module)
  module.subprocess = SimpleNamespace(run=Mock(), TimeoutExpired=subprocess.TimeoutExpired)
  module.threading = SimpleNamespace(Thread=lambda *, target, daemon: SimpleNamespace(start=target))
  return module


@pytest.fixture
def sidebar(sidebar_module, tmp_path):
  layout = sidebar_module.Sidebar.__new__(sidebar_module.Sidebar)
  layout._is_processing = False
  layout._is_update_available = False
  layout._commit_status = sidebar_module.MetricData('UPDATE', 'CHECK', sidebar_module.Colors.WARNING)
  layout._params = Mock()
  layout._params.get.return_value = f'"{"a" * 40}" == "{"a" * 40}"'
  layout._commit_check_exit_flag = tmp_path / 'commit_check_exit_flag'
  layout._is_network_connected = lambda: True
  layout._check_github_connection = lambda: True
  return layout


def test_check_button_accepts_file_result_without_stdout(sidebar_module, sidebar):
  sidebar._commit_check_exit_flag.write_text('1')

  def compare(command, **_kwargs):
    assert not sidebar._commit_check_exit_flag.exists()
    sidebar._commit_check_exit_flag.write_text('0')
    return subprocess.CompletedProcess(command, 0, stdout='', stderr='Commit check succeeded.')

  sidebar_module.subprocess.run.side_effect = compare
  sidebar._handle_commit_button_press()
  sidebar_module.subprocess.run.assert_called_once_with(
    ['/bin/sh', '/data/openpilot/scripts/commit_compare.sh'], timeout=15, check=False,
  )
  assert not sidebar._is_processing
  assert not sidebar._is_update_available
  assert (sidebar._commit_status.label, sidebar._commit_status.value) == ('UP TO DATE', 'aaaaaaa')
  assert not sidebar._commit_check_exit_flag.exists()


def test_nonzero_process_rejects_a_stale_success_flag(sidebar_module, sidebar):
  sidebar._is_update_available = True

  def fail(command, **_kwargs):
    sidebar._commit_check_exit_flag.write_text('0')
    return subprocess.CompletedProcess(command, 1)

  sidebar_module.subprocess.run.side_effect = fail
  sidebar._start_commit_check()
  assert not sidebar._is_processing
  assert not sidebar._is_update_available
  assert sidebar._commit_status.value == 'CHECK FAILED'
  sidebar._params.get.assert_not_called()


@pytest.mark.parametrize('flag, reason', [(None, 'NO LOG FILE'), ('1', 'CHECK FAILED'), ('invalid', 'FILE READ ERROR')])
def test_missing_or_invalid_completion_cannot_enable_update(sidebar_module, sidebar, flag, reason):
  sidebar._is_update_available = True

  def compare(command, **_kwargs):
    if flag is not None:
      sidebar._commit_check_exit_flag.write_text(flag)
    return subprocess.CompletedProcess(command, 0)

  sidebar_module.subprocess.run.side_effect = compare
  sidebar._start_commit_check()
  assert not sidebar._is_processing
  assert not sidebar._is_update_available
  assert sidebar._commit_status.value == reason
  sidebar._params.get.assert_not_called()


@pytest.mark.parametrize('error, reason', [(subprocess.TimeoutExpired('sh', 15), 'TIMEOUT'), (FileNotFoundError('sh'), 'FAILED TO START')])
def test_failed_worker_releases_button_for_retry(sidebar_module, sidebar, error, reason):
  sidebar._is_update_available = True
  sidebar_module.subprocess.run.side_effect = error
  sidebar._start_commit_check()
  assert not sidebar._is_processing
  assert not sidebar._is_update_available
  assert sidebar._commit_status.value == reason


def test_flag_cleanup_failure_releases_button(sidebar_module, sidebar):
  sidebar._commit_check_exit_flag = Mock()
  sidebar._commit_check_exit_flag.unlink.side_effect = PermissionError('unwritable flag')
  sidebar._start_commit_check()
  assert not sidebar._is_processing
  assert sidebar._commit_status.value == 'FAILED TO START'
  sidebar_module.subprocess.run.assert_not_called()


def test_duplicate_check_does_not_spawn_another_worker(sidebar_module, sidebar):
  sidebar._is_processing = True
  sidebar_module.threading.Thread = Mock()
  sidebar._start_commit_check()
  sidebar_module.threading.Thread.assert_not_called()


@pytest.mark.parametrize('length', [7, 40])
@pytest.mark.parametrize('as_bytes', [False, True])
@pytest.mark.parametrize('different', [False, True])
def test_valid_comparison_controls_update_action(sidebar_module, sidebar, length, as_bytes, different):
  local = 'a' * length
  remote = 'b' * length if different else local
  output = f'"{local}" {"!=" if different else "=="} "{remote}"\n'
  sidebar._parse_commit_compare_result(output.encode() if as_bytes else output)
  assert sidebar._is_update_available is different
  assert sidebar._commit_status.label == (local[:7] if different else 'UP TO DATE')
  assert sidebar._commit_status.value == remote[:7]
  assert sidebar._commit_status.color == (sidebar_module.Colors.RED if different else sidebar_module.Colors.LIME)


@pytest.mark.parametrize('output', [
  None, '', 'garbage', '"ERROR" != "ERROR"', '"aaaaaaa" != "aaaaaaa"', '"aaaaaaa" == "bbbbbbb"',
  '"" == ""', '"aaaaaaa" != "bbbbbbb" != "ccccccc"', b'\xff',
])
def test_invalid_comparison_cannot_enable_update(sidebar_module, sidebar, output):
  sidebar._is_processing = True
  sidebar._is_update_available = True
  sidebar._parse_commit_compare_result(output)
  assert not sidebar._is_processing
  assert not sidebar._is_update_available
  assert sidebar._commit_status.color == sidebar_module.Colors.RED
