import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_h264_composition_keeps_usb_codec_watcher_clock_and_shared_guards(monkeypatch):
  prefix = "openpilot.selfdrive.addon.cluster."
  display = SimpleNamespace(prepare_frame=Mock(), check_encoder=Mock())
  factory, run, requested = Mock(return_value=display), Mock(), Mock(return_value="h264")
  clock = SimpleNamespace(wait=Mock())
  clock_factory = Mock(return_value=clock)
  for name, module in {
    "cluster_config": SimpleNamespace(requested_usb_codec=requested),
    "cluster_frame_clock": SimpleNamespace(ClusterFrameClock=clock_factory),
    "cluster_jpeg_runtime": SimpleNamespace(run_composed_cluster=run),
    "usb_display.h264_display": SimpleNamespace(H264DisplayAdapter=factory),
  }.items():
    monkeypatch.setitem(sys.modules, prefix + name, module)
  path = Path(__file__).parents[1] / "usb_display/h264_runtime.py"
  spec = importlib.util.spec_from_file_location("usb_h264_runtime_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  config = SimpleNamespace(usb_h264_fps=20, params=object())
  module.run_h264_usb_cluster(config)
  factory.assert_called_once_with(config)
  clock_factory.assert_called_once_with(20)
  assert run.call_args.args == (config, display)
  options = run.call_args.kwargs
  assert options["codec"] == "h264"
  assert options["prepare_frame"] == display.prepare_frame
  assert options["clear_on_close"] is False  # H264 transport stops and clears itself after worker join.
  assert options["wait_for_frame"](object(), 7) == 7
  clock.wait.assert_called_once()
  assert not options["mode_changed"]()
  requested.return_value = "jpeg"
  assert options["mode_changed"]()
  requested.assert_called_with(config.params)
  display.check_encoder.side_effect = RuntimeError("hardware failed")
  with pytest.raises(RuntimeError, match="hardware failed"):
    options["mode_changed"]()
