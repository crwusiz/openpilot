from pathlib import Path
import threading
import time

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_scene import H264Scene
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.scene_layers import LayeredRenderer


ASSETS = Path(__file__).resolve().parents[5] / "assets"


def state(**updates):
  xs = list(range(5, 81, 5))
  def lane(offset):
    return [xs, [offset] * len(xs), [1.22] * len(xs)]
  value = {"source_size": [1344, 760], "model_valid": True,
           "hud": {"v_ego": 20, "enabled": True, "lat_active": True},
           "path": {"path_x": xs, "path_y": [0] * len(xs), "path_z": [0] * len(xs),
                    "calib_transform": [[672, -910, 0], [380, 0, 910], [1, 0, 0]],
                    "lane_lines": [lane(-3.6), lane(-1.8), lane(1.8), lane(3.6)],
                    "lane_line_probs": [0.5, 0.9, 0.9, 0.5]}}
  value.update(updates)
  return value


def video(scene, value):
  scene.set_video(np.full((720, 1280, 3), value, np.uint8))


def wait_done(worker):
  deadline = time.monotonic() + 3
  while worker._future is not None and not worker._future.done() and time.monotonic() < deadline:
    time.sleep(0.002)
  assert worker._future is not None and worker._future.done()


@pytest.fixture
def scene():
  now = [10.0]
  value = H264Scene(ASSETS, clock=lambda: now[0])
  value.renderer._parallel_path = True
  value.update(state())
  video(value, 50)
  yield value, now
  value.close()


def block_path(monkeypatch):
  entered, release = threading.Event(), threading.Event()
  original = LayeredRenderer._paint_path_request
  requests = []

  def paint(painter, request):
    requests.append(request)
    entered.set()
    assert release.wait(3), "camera presentation must not join the path worker"
    return original(painter, request)

  monkeypatch.setattr(LayeredRenderer, "_paint_path_request", paint)
  return entered, release, requests


def test_gpu_camera_and_current_hud_continue_while_path_is_blocked(scene, monkeypatch):
  value, now = scene
  first = value.render_layers()
  assert first.path is not None
  entered, release, requests = block_path(monkeypatch)
  speeds = []
  centered = value.renderer._centered
  def draw(draw, x, y, text, font, *args, **kwargs):
    if font is value.renderer.font_current_speed:
      speeds.append(text)
    return centered(draw, x, y, text, font, *args, **kwargs)
  monkeypatch.setattr(value.renderer, "_centered", draw)
  try:
    value.set_gpu_camera(True)
    now[0] += 0.05
    value.render_layers()
    assert entered.wait(3)
    now[0] += 0.05
    update = state()
    update["hud"]["v_ego"] = 25
    value.update(update)
    video(value, 100)
    current = value.render_layers()
    assert speeds[-1] == 90
    assert current.camera[0, 0, 0] == value._contrast_lut[100]
    np.testing.assert_array_equal(current.path, first.path)
    assert not release.is_set() and not value.renderer._path_worker._future.done()
    assert len(requests) == 1 and requests[0].hud["v_ego"] == 20
    assert value.last_frame_timings["path_wait"] == 0
    assert value.renderer._path_painter.config is not value.config
    assert value.renderer._path_painter._lead_vehicles is not value.renderer._lead_vehicles
    assert value.renderer._path_executor is None
  finally:
    release.set()


@pytest.mark.parametrize("source_age_ms", [0, 480])
def test_fresh_metadata_never_extends_a_completed_paths_original_expiry(scene, monkeypatch, source_age_ms):
  value, now = scene
  value.update(state(model_age_ms=source_age_ms))
  assert value.render_layers().path is not None
  entered, release, _ = block_path(monkeypatch)
  try:
    now[0] += 0.005
    value.set_gpu_camera(True)
    assert value.render_layers().path is not None
    assert entered.wait(3)
    now[0] += 0.03 if source_age_ms else 0.16
    value.update(state())
    video(value, 70)
    result = value.render_layers()
    assert result.path is None
    assert result.camera is not None and value.last_status["model_valid"]
    assert not value.renderer._async_path_failed
  finally:
    release.set()


@pytest.mark.parametrize("change", ["model", "video", "units", "geometry", "steering"])
def test_incompatible_or_stale_inputs_hide_old_paths_immediately(scene, monkeypatch, change):
  value, now = scene
  assert value.render_layers().path is not None
  entered, release, _ = block_path(monkeypatch)
  try:
    value.set_gpu_camera(True)
    now[0] += 0.005
    assert value.render_layers().path is not None
    assert entered.wait(3)
    now[0] += 0.02
    update = state()
    if change == "model":
      update["model_valid"] = False
    elif change == "video":
      now[0] += 0.5
    elif change == "units":
      update["is_metric"] = False
    elif change == "geometry":
      update["source_size"] = [1600, 1200]
    elif change == "steering":
      update["hud"]["steering_pressed"] = True
    value.update(update)
    assert value.render_layers().path is None
    assert not value.renderer._async_path_failed
    release.set()
    wait_done(value.renderer._path_worker)
    # A late completion from the old epoch must also stay hidden.
    assert value.render_layers().path is None
  finally:
    release.set()


def test_worker_failure_keeps_camera_live_and_restores_synchronous_paths(scene, monkeypatch, caplog):
  value, now = scene
  value.render_layers()
  def fail(_self, _request):
    raise RuntimeError("path painting failed")
  monkeypatch.setattr(LayeredRenderer, "_paint_path_request", fail)
  value.set_gpu_camera(True)
  value.render_layers()
  wait_done(value.renderer._path_worker)
  now[0] += 0.02
  result = value.render_layers()
  assert result.camera is not None and result.path is None
  assert value.renderer._async_path_failed and value.renderer._path_worker is None
  assert "worker failed; fallback=synchronous" in caplog.text and "path painting failed" in caplog.text
  assert value.render_layers().path is not None


def test_gpu_fallback_and_close_join_and_release_the_path_worker(scene):
  value, now = scene
  value.render_layers()
  value.set_gpu_camera(True)
  value.render_layers()
  worker = value.renderer._path_worker
  wait_done(worker)
  threads = list(worker._executor._threads)
  now[0] += 0.02
  value.set_gpu_camera(False)
  assert value.render().mode == "RGB"
  assert value.renderer._path_worker is None and value.renderer._path_painter is None
  assert not any(thread.is_alive() for thread in threads)
  assert worker._result is None and worker._render is None


def test_parallel_opt_out_keeps_the_existing_synchronous_path(scene):
  value, _now = scene
  value.renderer._parallel_path = False
  value.set_gpu_camera(True)
  assert value.render_layers().path is not None
  assert value.renderer._path_worker is None
