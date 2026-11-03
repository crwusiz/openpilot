import importlib.util
from io import BytesIO
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest


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
  module.Spinner.assert_called_once_with(keep_alive=True)
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
