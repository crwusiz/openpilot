import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.cluster_frame_clock import ClusterFrameClock


@pytest.fixture
def cluster(monkeypatch):
  config = SimpleNamespace(params=Mock(), display_transport="usb", fps=20, width=10, height=10)
  config.params.get.return_value = "usb"
  display = Mock(connected=True)
  pipeline = Mock()
  pipeline.close.return_value = True
  camera, models, renderer = Mock(), Mock(), Mock()
  camera_factory = Mock(return_value=camera)
  display_factory = Mock(return_value=display)
  policy = Mock(return_value=True)
  prefix = "openpilot.selfdrive.addon.cluster."
  modules = {
    "openpilot.common.swaglog": SimpleNamespace(cloudlog=Mock()),
    prefix + "cluster_config": SimpleNamespace(ClusterConfig=lambda: config),
    prefix + "cluster_logging": SimpleNamespace(close_log=Mock(), flog=Mock(), initialize_log=Mock()),
    prefix + "cluster_display_pipeline": SimpleNamespace(ClusterDisplayPipeline=lambda d: pipeline),
    prefix + "cluster_live_camera": SimpleNamespace(ClusterLiveCamera=camera_factory),
    prefix + "cluster_models": SimpleNamespace(ClusterModels=lambda: models),
    prefix + "cluster_renderer": SimpleNamespace(ClusterRenderer=lambda c: renderer),
    prefix + "cluster_policy": SimpleNamespace(enforce_cluster_transport=policy),
    prefix + "dependencies": SimpleNamespace(ensure_cluster_dependencies=Mock(return_value=True)),
    prefix + "usb_display.turing_usb_display": SimpleNamespace(TuringUsbDisplay=display_factory),
    prefix + "hdmi_display.network_display": SimpleNamespace(ClusterNetworkDisplay=display_factory),
    prefix + "cluster_frame_clock": SimpleNamespace(ClusterFrameClock=lambda fps: module.ClusterFrameClock(fps)),
  }
  for name, module in modules.items():
    monkeypatch.setitem(sys.modules, name, module)
  spec = importlib.util.spec_from_file_location("cluster_runtime_under_test", Path(__file__).parents[1] / "cluster_jpeg_runtime.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  module.ClusterFrameClock = ClusterFrameClock
  monkeypatch.setitem(sys.modules, prefix + "cluster_jpeg_runtime", module)
  for backend in ("usb_display", "hdmi_display"):
    spec = importlib.util.spec_from_file_location(prefix + backend + ".main", Path(__file__).parents[1] / backend / "main.py")
    backend_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend_module)
    if backend == "usb_display":
      monkeypatch.setattr(backend_module, "configure_usb_runtime", Mock())
    monkeypatch.setitem(sys.modules, prefix + backend + ".main", backend_module)
  spec = importlib.util.spec_from_file_location("cluster_main_under_test", Path(__file__).parents[1] / "main.py")
  entrypoint = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(entrypoint)
  module.cluster_main = entrypoint.cluster_main
  module.close_log = entrypoint.close_log
  monkeypatch.setattr(module.signal, "signal", Mock())
  return SimpleNamespace(module=module, config=config, display=display, pipeline=pipeline, policy=policy,
                         camera=camera, camera_factory=camera_factory, display_factory=display_factory, models=models,
                         entrypoint=entrypoint)


def test_existing_egpu_prevents_opening_usb_display(cluster):
  cluster.policy.return_value = False
  cluster.module.cluster_main()
  cluster.display_factory.assert_not_called()
  cluster.module.close_log.assert_called_once()


def test_configuration_error_still_closes_session_log(cluster, monkeypatch):
  monkeypatch.setattr(cluster.entrypoint, "ClusterConfig", Mock(side_effect=ValueError("bad transport")))
  with pytest.raises(ValueError, match="bad transport"):
    cluster.module.cluster_main()
  cluster.display_factory.assert_not_called()
  cluster.module.close_log.assert_called_once()


def test_backend_error_still_closes_session_log(cluster, monkeypatch):
  backend = sys.modules["openpilot.selfdrive.addon.cluster.usb_display.main"]
  monkeypatch.setattr(backend, "run_usb_cluster", Mock(side_effect=RuntimeError("backend startup failed")))
  with pytest.raises(RuntimeError, match="backend startup failed"):
    cluster.module.cluster_main()
  cluster.module.close_log.assert_called_once()


def test_hotplug_stops_workers_without_sending_clear_frame(cluster):
  cluster.policy.side_effect = [True, False, False]
  cluster.module.cluster_main()
  cluster.pipeline.close.assert_called_once()
  cluster.pipeline.push.assert_not_called()
  cluster.display.send_image.assert_not_called()
  cluster.display.close.assert_called_once()
  cluster.camera.close.assert_called_once()
  cluster.models.close.assert_called_once()


def test_network_shutdown_still_clears_display(cluster):
  cluster.config.display_transport = "network"
  # A transport change exits the loop normally.
  cluster.module.cluster_main()
  cluster.display.send_image.assert_called_once()
  cluster.display.close.assert_called_once()


def test_h264_dispatch_does_not_start_jpeg_pipeline_or_raw_rgb_camera(cluster, monkeypatch):
  cluster.config.display_transport = "network"
  cluster.config.network_codec = "h264"
  runner = Mock()
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.hdmi_display.network_h264", SimpleNamespace(run_h264_cluster=runner))
  cluster.module.cluster_main()
  runner.assert_called_once_with(cluster.config)
  cluster.display_factory.assert_not_called()
  cluster.pipeline.start.assert_not_called()
  cluster.camera_factory.assert_not_called()
  cluster.module.close_log.assert_called_once()


def test_jpeg_restarts_when_network_codec_is_changed(cluster, monkeypatch):
  cluster.config.display_transport = "network"
  cluster.config.network_codec = "jpeg"
  cluster.config.params.get.side_effect = {"ClusterDisplayTransport": "network", "ClusterNetworkCodec": "h264"}.get
  cluster.module.cluster_main()
  cluster.pipeline.push.assert_not_called()
  cluster.pipeline.close.assert_called_once()


def test_network_display_clock_skips_heavy_work_when_transport_has_no_capacity(cluster, monkeypatch):
  cluster.config.display_transport = "network"
  cluster.config.fps = 60
  cluster.config.params.get.return_value = "network"
  cluster.display.screen_off = False
  cluster.policy.side_effect = [True, True, False, False]
  cluster.pipeline.has_render_capacity.side_effect = [False] * 58 + [True, True]
  now = [0.0]

  def wait():
    now[0] += 1 / 60

  monkeypatch.setattr(cluster.module.time, "monotonic", lambda: now[0])
  monkeypatch.setattr(cluster.module, "ClusterFrameClock", lambda fps: SimpleNamespace(wait=wait))
  monkeypatch.setattr(cluster.module, "ClusterRuntimeMetrics", lambda: SimpleNamespace(sample=lambda _health: None))
  renderer = cluster.module.ClusterRenderer(cluster.config)
  renderer.last_frame_timings = dict.fromkeys(("camera_copy", "snapshot", "path", "hud"), 0.0)
  cluster.module.cluster_main()
  cluster.camera.wait_for_frame.assert_not_called()
  assert renderer.render.call_count == 2
  assert cluster.pipeline.push.call_count == 2
  assert cluster.models.get_health_data.call_count == 60


def _set_frame_clock(cluster, monkeypatch):
  ticks = [0]
  fps = cluster.config.fps

  def wait(*_args):
    ticks[0] += 1
    return ticks[0]

  monkeypatch.setattr(cluster.module, "time", SimpleNamespace(monotonic=lambda: ticks[0] / fps))
  monkeypatch.setattr(cluster.module, "ClusterFrameClock", lambda _fps: SimpleNamespace(wait=wait))
  monkeypatch.setattr(cluster.module, "ClusterRuntimeMetrics", lambda: SimpleNamespace(sample=lambda _health: None))
  cluster.camera.wait_for_frame.side_effect = wait
  renderer = cluster.module.ClusterRenderer(cluster.config)
  renderer.last_frame_timings = dict.fromkeys(("camera_copy", "snapshot", "path", "hud"), 0.0)
  return renderer


def test_usb_skips_render_and_encode_while_transport_is_full(cluster, monkeypatch):
  renderer = _set_frame_clock(cluster, monkeypatch)
  cluster.policy.side_effect = [True, True, False, False]
  cluster.pipeline.has_render_capacity.side_effect = [False] * 18 + [True, True]

  cluster.module.cluster_main()

  assert renderer.render.call_count == cluster.pipeline.push.call_count == 2
  assert cluster.models.get_health_data.call_count == 20
  cluster.pipeline.has_render_capacity.side_effect = None
  cluster.pipeline.has_render_capacity.return_value = False
  frame_needed = cluster.camera_factory.call_args.kwargs["frame_needed"]
  assert not frame_needed()
  cluster.pipeline.has_render_capacity.return_value = True
  assert frame_needed()


def test_render_cpu_statistics_exclude_waiting_and_reset_each_window(cluster, monkeypatch):
  cluster.config.display_transport = "network"
  cluster.config.params.get.return_value = "network"
  cluster.display.screen_off = False
  renderer = _set_frame_clock(cluster, monkeypatch)
  now, cpu = [0.0], [0.0]
  monkeypatch.setattr(cluster.module, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(cluster.module, "thread_time", lambda: cpu[0])

  def wait():
    now[0] += 0.05

  monkeypatch.setattr(cluster.module, "ClusterFrameClock", lambda _fps: SimpleNamespace(wait=wait))
  cluster.policy.side_effect = lambda *_args: now[0] < 21.0
  cluster.models.get_health_data.return_value = {}
  cluster.pipeline.get_stats.return_value = dict.fromkeys(("sent", "dropped_raw", "dropped_prepared", "dropped_stale", "send_failures"), 0)
  renderer.last_frame_timings.update(path=0.020, path_cpu=0.003, hud=0.010)

  def render(*_args):
    now[0] += 0.04  # Rendering includes time when the thread is not running.
    cpu[0] += 0.01
    return object()

  renderer.render.side_effect = render
  cluster.module.cluster_main()
  logs = [call.args[0] for call in cluster.module.flog.call_args_list if "[CLUSTER_MAIN_PERF]" in call.args[0]]
  assert len(logs) == 2
  assert all("render_avg=40.0ms" in log and "render_cpu_avg=10.0ms" in log for log in logs)
  assert all("path_avg=20.0ms" in log and "path_cpu_avg=3.0ms" in log for log in logs)


def test_driving_input_fault_limits_rendering_and_camera_preprocessing(cluster, monkeypatch):
  cluster.config.display_transport = 'network'
  cluster.config.params.get.return_value = 'network'
  cluster.display.screen_off = False
  renderer = _set_frame_clock(cluster, monkeypatch)
  cluster.policy.side_effect = [True, True, True, False, False]
  cluster.models.get_health_data.return_value = {
    'onroad_started': True, 'model_seen': True, 'model_age_ms': 1200.0, 'diagnostic_events': (),
  }
  cluster.module.cluster_main()
  assert 0 < renderer.render.call_count <= 10
  assert cluster.pipeline.push.call_count == renderer.render.call_count
  assert cluster.models.get_health_data.call_count == 40
  assert not cluster.camera_factory.call_args.kwargs['frame_needed']()


@pytest.mark.parametrize("transport", ["usb", "network"])
def test_disconnected_display_prepares_only_one_bootstrap_image_per_second(cluster, monkeypatch, transport):
  cluster.config.display_transport = transport
  cluster.config.params.get.return_value = transport
  cluster.display.connected = False
  renderer = _set_frame_clock(cluster, monkeypatch)
  cluster.policy.side_effect = [True, True, True, False, False]

  cluster.module.cluster_main()

  assert renderer.render.call_count == cluster.pipeline.push.call_count == 2
  assert cluster.models.get_health_data.call_count == 40
  frame_needed = cluster.camera_factory.call_args.kwargs["frame_needed"]
  assert not frame_needed()
