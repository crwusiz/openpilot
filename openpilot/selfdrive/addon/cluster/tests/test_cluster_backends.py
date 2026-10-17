import builtins
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


PREFIX = "openpilot.selfdrive.addon.cluster."


def load_backend(directory):
  path = Path(__file__).parents[1] / directory / "main.py"
  spec = importlib.util.spec_from_file_location(f"cluster_{directory}_backend_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def block_imports(monkeypatch, *prefixes):
  original_import = builtins.__import__

  def guarded_import(name, *args, **kwargs):
    if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
      raise ModuleNotFoundError(f"Optional dependency deliberately unavailable: {name}")
    return original_import(name, *args, **kwargs)

  monkeypatch.setattr(builtins, "__import__", guarded_import)


@pytest.mark.parametrize("directory", ["usb_display", "hdmi_display"])
def test_importing_backend_does_not_load_optional_display_dependencies(monkeypatch, directory):
  block_imports(monkeypatch, "cv2", "numpy", "usb", "library", "aiortc", "teleoprtc", PREFIX.rstrip("."))
  load_backend(directory)


def test_dispatcher_import_does_not_load_display_backends_or_composition_pipeline(monkeypatch):
  for name, module in {
    "openpilot.common.swaglog": SimpleNamespace(cloudlog=Mock()),
    PREFIX + "cluster_config": SimpleNamespace(ClusterConfig=Mock()),
    PREFIX + "cluster_logging": SimpleNamespace(close_log=Mock(), flog=Mock(), initialize_log=Mock()),
    PREFIX + "cluster_policy": SimpleNamespace(enforce_cluster_transport=Mock()),
  }.items():
    monkeypatch.setitem(sys.modules, name, module)
  block_imports(monkeypatch, "cv2", "usb", "library", "teleoprtc", PREFIX + "usb_display", PREFIX + "hdmi_display",
                PREFIX + "usb_display.runtime", PREFIX + "cluster_live_camera", PREFIX + "cluster_renderer")
  path = Path(__file__).parents[1] / "main.py"
  spec = importlib.util.spec_from_file_location("cluster_dispatcher_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)


def test_usb_dispatch_always_uses_h264_without_loading_network_runtime(monkeypatch):
  backend = load_backend("usb_display")
  block_imports(monkeypatch, PREFIX + "hdmi_display")
  runner = Mock()
  monkeypatch.setitem(sys.modules, PREFIX + "usb_display.h264_runtime", SimpleNamespace(run_h264_usb_cluster=runner))
  config = SimpleNamespace()
  backend.run_usb_cluster(config)
  runner.assert_called_once_with(config)


def test_network_dispatch_always_uses_h264_without_raw_camera_or_usb_dependencies(monkeypatch):
  backend = load_backend("hdmi_display")
  block_imports(
    monkeypatch, "cv2", "usb", "library", PREFIX + "usb_display",
    PREFIX + "usb_display.runtime", PREFIX + "cluster_frame_clock", PREFIX + "cluster_live_camera",
    PREFIX + "cluster_display_pipeline", PREFIX + "cluster_renderer",
  )
  runner = Mock()
  monkeypatch.setitem(sys.modules, PREFIX + "hdmi_display.network_h264", SimpleNamespace(run_h264_cluster=runner))
  config = SimpleNamespace()

  backend.run_network_cluster(config)

  runner.assert_called_once_with(config)
