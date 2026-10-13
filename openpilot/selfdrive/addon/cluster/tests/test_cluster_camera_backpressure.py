import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest


@pytest.fixture
def camera_module(monkeypatch):
  monkeypatch.setitem(sys.modules, "msgq.visionipc", SimpleNamespace(VisionIpcClient=Mock()))
  monkeypatch.setitem(sys.modules, "openpilot.cereal.visionipc",
                      SimpleNamespace(VisionStreamType=SimpleNamespace(VISION_STREAM_NARROW_ROAD=0)))
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.cluster_logging", SimpleNamespace(flog=Mock()))
  spec = importlib.util.spec_from_file_location("camera_backpressure_under_test",
                                               Path(__file__).parents[1] / "cluster_live_camera.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module.ClusterLiveCamera, "_init_camera", lambda self: None)
  monkeypatch.setattr(module.ClusterLiveCamera, "_start_thread", lambda self: setattr(self, "running", True))
  return module


def _run_camera(module, monkeypatch, events, demand=None):
  now = [10.0]
  ready = [False]
  monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
  config = SimpleNamespace(camera_panel_width=4, camera_panel_height=2, height=2, camera_contrast=1.0)
  callback = None if demand is None else lambda: ready[0]
  camera = module.ClusterLiveCamera(config, frame_needed=callback)
  camera._connected_at = now[0]
  converted, received = [], []
  pending = iter(events)
  consumed = [0]

  class Stream:
    def is_connected(self):
      return True

    def recv(self, timeout_ms):
      assert timeout_ms == 50
      try:
        when, identity, capacity = next(pending)
      except StopIteration:
        camera.running = False
        return None
      now[0], ready[0] = when, capacity
      consumed[0] += 1
      if consumed[0] == len(events):
        camera.running = False
      if identity is None:
        return None
      received.append(identity)
      return SimpleNamespace(identity=identity, width=4, height=2)

  def convert(buffer):
    converted.append((now[0], buffer.identity))
    return np.full((2, 4, 3), buffer.identity, dtype=np.uint8), np.eye(3)

  camera.vipc = Stream()
  monkeypatch.setattr(camera, "_panel_rgb_from_vision_buffer", convert)
  camera._camera_thread()
  return camera, received, converted, now[0]


@pytest.mark.parametrize("blocked", [False, True])
def test_backpressure_drains_every_raw_frame_and_keeps_camera_fresh(camera_module, monkeypatch, blocked):
  events = [(10.0 + index * 0.05, index, not blocked) for index in range(60)]
  camera, received, converted, now = _run_camera(camera_module, monkeypatch, events, demand=True)
  assert received == list(range(60))
  assert camera._last_frame_at == now
  assert camera.has_frame()
  assert now - converted[-1][0] <= 0.25
  assert int(camera.get_frame()[0, 0, 0]) == converted[-1][1]
  if blocked:
    assert 12 <= len(converted) <= 16
    assert camera._perf_skipped == len(received) - len(converted)
  else:
    assert len(converted) == len(received)
    assert camera._perf_skipped == 0


def test_camera_without_capacity_callback_processes_all_frames(camera_module, monkeypatch):
  events = [(10.0 + index * 0.05, index, False) for index in range(40)]
  camera, received, converted, _ = _run_camera(camera_module, monkeypatch, events)
  assert len(converted) == len(received) == 40
  assert camera.frame_count == 40


def test_released_transport_capacity_prepares_current_buffer_immediately(camera_module, monkeypatch):
  events = [(10.0, 1, False), (10.05, 2, False), (10.1, 3, True)]
  camera, received, converted, _ = _run_camera(camera_module, monkeypatch, events, demand=True)
  assert received == [1, 2, 3]
  assert converted == [(10.0, 1), (10.1, 3)]
  assert np.all(camera.get_frame() == 3)


def test_raw_camera_stall_still_clears_last_image_under_backpressure(camera_module, monkeypatch):
  events = [(10.0, 1, False), (10.05, 2, False), (11.3, None, False)]
  camera, received, _, _ = _run_camera(camera_module, monkeypatch, events, demand=True)
  assert received == [1, 2]
  assert not camera.has_frame()
  assert camera.vipc is None
  assert any("Camera stream stale" in call.args[0] for call in camera_module.flog.call_args_list)


def test_camera_diagnostics_distinguish_received_and_preprocessed_rates(camera_module, monkeypatch):
  events = [(10.0 + index * 0.05, index % 255, False) for index in range(201)]
  _run_camera(camera_module, monkeypatch, events, demand=True)
  messages = [call.args[0] for call in camera_module.flog.call_args_list]
  perf = next(message for message in messages if "[CLUSTER_CAM_PERF]" in message)
  assert "receive_fps=20.10" in perf
  assert "skipped=0" not in perf
