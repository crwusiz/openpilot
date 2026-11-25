import importlib.util
from io import BytesIO
from pathlib import Path
import runpy
import signal
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from openpilot.common import spinner as spinner_module


@pytest.fixture
def build_module(monkeypatch):
  hardware = ModuleType("openpilot.common.hardware")
  hardware.HARDWARE = SimpleNamespace(set_power_save=Mock())
  hardware.AGNOS = False
  monkeypatch.setitem(sys.modules, "openpilot.common.hardware", hardware)
  spec = importlib.util.spec_from_file_location("build_under_test", Path(__file__).resolve().parents[1] / "build.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module, "os", SimpleNamespace(environ={}, getenv=lambda _: None, sync=Mock()))
  monkeypatch.setattr(module, "subprocess", SimpleNamespace(PIPE=-1, Popen=Mock()))

  spinner = MagicMock()
  spinner.active = True
  spinner.__enter__.return_value = spinner
  spinner.close.side_effect = lambda: setattr(spinner, "active", False)
  spinner.__exit__.side_effect = lambda *_: spinner.close()
  monkeypatch.setattr(module, "Spinner", Mock(return_value=spinner))
  return module, spinner


def completed_build(status):
  process = MagicMock()
  process.returncode = status
  process.poll.return_value = status
  process.stderr = BytesIO(b"compiler error\n" if status else b"")
  process.__enter__.return_value = process
  return process


def test_success_stops_build_spinner(build_module):
  module, spinner = build_module
  module.subprocess.Popen.return_value = completed_build(0)
  module.build()
  module.Spinner.assert_called_once_with()
  spinner.close.assert_called_once()
  assert not spinner.active


def test_unexpected_build_exception_stops_spinner(build_module):
  module, spinner = build_module
  module.subprocess.Popen.side_effect = OSError("compiler could not start")
  with pytest.raises(OSError, match="compiler could not start"):
    module.build()
  spinner.close.assert_called_once()
  assert not spinner.active


def test_failed_build_stops_spinner_before_showing_error(build_module, monkeypatch):
  module, spinner = build_module
  module.subprocess.Popen.side_effect = lambda *args, **kwargs: completed_build(1)
  window = MagicMock()
  window.__enter__.return_value = window

  def ensure_spinner_stopped():
    assert not spinner.active

  def show_error(text):
    ensure_spinner_stopped()
    assert "openpilot failed to build" in text
    assert "compiler error" in text
    return window

  window.wait_for_exit.side_effect = ensure_spinner_stopped
  monkeypatch.setattr(module, "TextWindow", show_error)
  with pytest.raises(SystemExit) as error:
    module.build()
  assert error.value.code == 1
  assert module.subprocess.Popen.call_count == 3
  window.wait_for_exit.assert_called_once()
  assert not spinner.active


def test_boot_spinner_stays_open_until_sigterm(monkeypatch):
  process = Mock()
  launcher = Mock(return_value=process)
  monkeypatch.setattr(spinner_module.subprocess, 'Popen', launcher)
  handlers = {}
  monkeypatch.setattr(signal, 'signal', handlers.__setitem__)

  def stop_after_start(_interval):
    process.stdin.write.assert_called_once_with(b'Installing add-on dependencies...\n')
    process.terminate.assert_not_called()
    handlers[signal.SIGTERM](signal.SIGTERM, None)

  sleep = Mock(side_effect=stop_after_start)
  monkeypatch.setattr(time, 'sleep', sleep)
  runpy.run_path(str(Path(__file__).resolve().parents[1] / 'boot_spinner.py'), run_name='__main__')

  launcher.assert_called_once()
  sleep.assert_called_once_with(0.5)
  process.stdin.flush.assert_called_once()
  process.terminate.assert_called_once()
  process.communicate.assert_called_once_with(timeout=2.)


def test_uninitialized_spinner_can_be_closed():
  spinner = spinner_module.Spinner.__new__(spinner_module.Spinner)
  spinner.close()


def test_spinner_update_handles_broken_pipe_during_write(monkeypatch):
  process = Mock()
  process.stdin.write.side_effect = BrokenPipeError
  monkeypatch.setattr(spinner_module.subprocess, 'Popen', Mock(return_value=process))

  with spinner_module.Spinner() as spinner:
    spinner.update('Installing add-on dependencies...')

  process.stdin.write.assert_called_once()
  process.terminate.assert_called_once()
