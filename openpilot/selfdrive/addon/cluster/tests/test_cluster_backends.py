import builtins
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

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


def test_dispatcher_import_does_not_load_display_backends_or_jpeg_pipeline(monkeypatch):
  for name, module in {
    "openpilot.common.swaglog": SimpleNamespace(cloudlog=Mock()),
    PREFIX + "cluster_config": SimpleNamespace(ClusterConfig=Mock()),
    PREFIX + "cluster_logging": SimpleNamespace(close_log=Mock(), initialize_log=Mock()),
    PREFIX + "cluster_policy": SimpleNamespace(enforce_cluster_transport=Mock()),
  }.items():
    monkeypatch.setitem(sys.modules, name, module)
  block_imports(monkeypatch, "cv2", "usb", "library", "teleoprtc", PREFIX + "usb_display", PREFIX + "hdmi_display",
                PREFIX + "cluster_jpeg_runtime", PREFIX + "cluster_live_camera", PREFIX + "cluster_renderer")
  path = Path(__file__).parents[1] / "main.py"
  spec = importlib.util.spec_from_file_location("cluster_dispatcher_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)


@pytest.mark.parametrize("available_locale", ["C.UTF-8", "C"])
@pytest.mark.parametrize("vendor_already_present", [False, True])
def test_usb_runtime_sets_vendor_locale_and_adds_path_only_once(monkeypatch, available_locale, vendor_already_present):
  backend = load_backend("usb_display")
  vendor_root = str(Path(backend.__file__).resolve().parent / ".vendor" / "turing-smart-screen-python-main")
  path = ["project"] + ([vendor_root] if vendor_already_present else [])
  environment = {"LANG": "unsupported_locale"}
  real_locale = backend.locale

  def set_locale(_category, candidate):
    if candidate != available_locale:
      raise real_locale.Error("unsupported locale")
    return candidate

  locale = SimpleNamespace(LC_ALL=real_locale.LC_ALL, Error=real_locale.Error, setlocale=Mock(side_effect=set_locale))
  monkeypatch.setattr(backend, "locale", locale)
  monkeypatch.setattr(backend, "os", SimpleNamespace(environ=environment))
  monkeypatch.setattr(backend, "sys", SimpleNamespace(path=path))

  backend.configure_usb_runtime()
  backend.configure_usb_runtime()

  expected = [call(locale.LC_ALL, "C.UTF-8")]
  if available_locale == "C":
    expected.append(call(locale.LC_ALL, "C"))
  assert locale.setlocale.call_args_list == expected * 2
  assert environment == dict.fromkeys(("LC_ALL", "LC_CTYPE", "LANG"), available_locale)
  assert path.count(vendor_root) == 1
  if not vendor_already_present:
    assert path[0] == vendor_root


def test_usb_prepares_vendor_before_imports_and_preserves_camera_cadence(monkeypatch):
  backend = load_backend("usb_display")
  prepared = []
  monkeypatch.setattr(backend, "configure_usb_runtime", lambda: prepared.append(True))
  display = object()
  display_factory = Mock(return_value=display)
  runner = Mock()
  modules = {
    PREFIX + "usb_display.turing_usb_display": SimpleNamespace(TuringUsbDisplay=display_factory),
    PREFIX + "cluster_jpeg_runtime": SimpleNamespace(run_jpeg_cluster=runner),
  }
  for name, module in modules.items():
    monkeypatch.setitem(sys.modules, name, module)
  original_import = builtins.__import__

  def guarded_import(name, *args, **kwargs):
    if name in modules:
      assert prepared, "USB dependencies imported before vendor runtime preparation"
    return original_import(name, *args, **kwargs)

  monkeypatch.setattr(builtins, "__import__", guarded_import)
  config = SimpleNamespace(fps=20)

  backend.run_usb_cluster(config)

  display_factory.assert_called_once_with(config)
  assert runner.call_args.args == (config, display)
  camera = SimpleNamespace(wait_for_frame=Mock(return_value=8))
  assert runner.call_args.kwargs["wait_for_frame"](camera, 7) == 8
  camera.wait_for_frame.assert_called_once_with(7, 0.05)


@pytest.mark.parametrize("codec", ["jpeg", None])
@pytest.mark.parametrize("screen_off_fps", [None, 3])
def test_network_jpeg_runs_without_usb_or_h264_dependencies(monkeypatch, codec, screen_off_fps):
  backend = load_backend("hdmi_display")
  block_imports(monkeypatch, "usb", "library", "aiortc", "teleoprtc", PREFIX + "usb_display", PREFIX + "hdmi_display.network_h264")
  display = object()
  clock = SimpleNamespace(wait=Mock())
  display_factory = Mock(return_value=display)
  clock_factory = Mock(return_value=clock)
  runner = Mock()
  requested_codec = Mock(return_value="jpeg")
  modules = {
    PREFIX + "cluster_frame_clock": SimpleNamespace(ClusterFrameClock=clock_factory),
    PREFIX + "cluster_jpeg_runtime": SimpleNamespace(run_jpeg_cluster=runner),
    PREFIX + "hdmi_display.network_codec": SimpleNamespace(requested_codec=requested_codec),
    PREFIX + "hdmi_display.network_display": SimpleNamespace(ClusterNetworkDisplay=display_factory),
  }
  for name, module in modules.items():
    monkeypatch.setitem(sys.modules, name, module)
  config = SimpleNamespace(params=object(), fps=20)
  if codec is not None:
    config.network_codec = codec
  if screen_off_fps is not None:
    config.network_screen_off_fps = screen_off_fps

  backend.run_network_cluster(config)

  display_factory.assert_called_once_with(config)
  clock_factory.assert_called_once_with(config.fps)
  assert runner.call_args.args == (config, display)
  options = runner.call_args.kwargs
  camera = Mock()
  assert options["wait_for_frame"](camera, 7) == 7
  clock.wait.assert_called_once()
  camera.wait_for_frame.assert_not_called()
  assert options["screen_off_fps"] == (5 if screen_off_fps is None else screen_off_fps)
  assert not options["mode_changed"]()
  requested_codec.return_value = "h264"
  assert options["mode_changed"]()
  requested_codec.assert_called_with(config.params)


def test_network_h264_runs_without_jpeg_camera_or_usb_dependencies(monkeypatch):
  backend = load_backend("hdmi_display")
  block_imports(
    monkeypatch, "cv2", "usb", "library", PREFIX + "usb_display",
    PREFIX + "cluster_jpeg_runtime", PREFIX + "cluster_frame_clock", PREFIX + "cluster_live_camera",
    PREFIX + "cluster_display_pipeline", PREFIX + "cluster_renderer", PREFIX + "hdmi_display.network_display",
  )
  runner = Mock()
  monkeypatch.setitem(sys.modules, PREFIX + "hdmi_display.network_h264", SimpleNamespace(run_h264_cluster=runner))
  config = SimpleNamespace(network_codec="h264")

  backend.run_network_cluster(config)

  runner.assert_called_once_with(config)
