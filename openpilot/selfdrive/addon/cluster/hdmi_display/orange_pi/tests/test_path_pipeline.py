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
  if worker._future is not None:
    assert worker._future.done()
  else:
    assert worker.completed > 0


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
    now[0] += 0.03 if source_age_ms else 0.51
    value.update(state())
    video(value, 70)
    result = value.render_layers()
    assert result.path is None
    assert result.camera is not None and value.last_status["model_valid"]
    assert not value.renderer._async_path_failed
  finally:
    release.set()


@pytest.mark.parametrize("change", ["model", "video", "units", "geometry", "engagement"])
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
    elif change == "engagement":
      update["hud"].update(enabled=False, lat_active=False)
    value.update(update)
    assert value.render_layers().path is None
    assert not value.renderer._async_path_failed
    release.set()
    wait_done(value.renderer._path_worker)
    # A late completion from the old epoch must also stay hidden.
    assert value.render_layers().path is None
  finally:
    release.set()


@pytest.mark.parametrize("changes", [{"brake_pressed": True}, {"left_blinker": True},
                                      {"steering_pressed": True}, {"pre_enabled_or_overriding": True}])
def test_style_changes_keep_lanes_visible_while_a_new_style_is_pending(scene, monkeypatch, changes):
  value, now = scene
  initial = state()
  initial["hud"]["lat_active"] = False
  value.update(initial)
  first = value.render_layers()
  assert first.path is not None
  entered, release, _ = block_path(monkeypatch)
  try:
    value.set_gpu_camera(True)
    now[0] += 0.01
    value.render_layers()
    assert entered.wait(3)
    previous_key = value.renderer._path_signature
    for elapsed in (0.04, 0.12, 0.20, 0.28):
      now[0] = 10 + elapsed
      update = state()
      update["hud"].update(lat_active=False, **changes)
      value.update(update)
      video(value, 60)
      current = value.render_layers()
      np.testing.assert_array_equal(current.path, first.path)
      assert value.renderer._path_signature == previous_key
      assert value.last_frame_timings["path_visible"] == 1
      assert value.last_frame_timings["path_ready"] == 1
      assert current.overlay.getpixel((0, 0)) == tuple(value.renderer._get_border_color(update["hud"]))
      assert value.last_frame_timings["path_reset"] == 0
      assert value.last_frame_timings["path_wait"] == 0
    # The completed old-style job remains usable until the new style is ready.
    release.set()
    wait_done(value.renderer._path_worker)
    assert value.render_layers().path is not None
    wait_done(value.renderer._path_worker)
    assert value.render_layers().path is not None
  finally:
    release.set()


def test_normal_delivery_gaps_do_not_blank_lanes_before_the_original_source_expiry(scene, monkeypatch):
  value, now = scene
  first = value.render_layers()
  entered, release, _ = block_path(monkeypatch)
  try:
    value.set_gpu_camera(True)
    now[0] += 0.01
    value.render_layers()
    assert entered.wait(3)
    for elapsed in (0.08, 0.16, 0.24, 0.32, 0.48):
      now[0] = 10 + elapsed
      value.update(state())
      video(value, 75)
      current = value.render_layers()
      np.testing.assert_array_equal(current.path, first.path)
      assert value.last_status["model_valid"]
      assert value.last_frame_timings["path_age"] == pytest.approx(elapsed)
    now[0] = 10.51
    value.update(state())
    video(value, 80)
    expired = value.render_layers()
    assert expired.camera is not None and expired.path is None
    assert value.last_status["model_valid"]
    assert value.last_frame_timings["path_expired"] == 1
    assert value.last_frame_timings["path_visible"] == 0
  finally:
    release.set()


@pytest.mark.parametrize("phase", ["early", "late"])
def test_worker_failure_keeps_camera_live_and_restores_synchronous_paths(scene, monkeypatch, caplog, phase):
  value, now = scene
  value.render_layers()
  value.update(state(hud={"v_ego": 21, "enabled": True, "lat_active": True}))
  entered, release = threading.Event(), threading.Event()
  def fail(_self, _request):
    entered.set()
    assert release.wait(3)
    raise RuntimeError("path painting failed")
  monkeypatch.setattr(LayeredRenderer, "_paint_path_request", fail)
  draw_hud = value.renderer._draw_hud
  if phase == "late":
    def hud(*args):
      assert entered.wait(3)
      release.set()
      wait_done(value.renderer._path_worker)
      return draw_hud(*args)
    monkeypatch.setattr(value.renderer, "_draw_hud", hud)
  try:
    value.set_gpu_camera(True)
    result = value.render_layers()
    if phase == "early":
      assert entered.wait(3)
      release.set()
      wait_done(value.renderer._path_worker)
      now[0] += 0.02
      result = value.render_layers()
    assert result.camera is not None and result.path is None
    assert value.renderer._async_path_failed and value.renderer._path_worker is None
    assert "worker failed; fallback=synchronous" in caplog.text and "path painting failed" in caplog.text
    monkeypatch.setattr(value.renderer, "_draw_hud", draw_hud)
    assert value.render_layers().path is not None
  finally:
    release.set()


def test_path_starts_during_hud_and_its_completion_is_used_in_the_same_frame(scene, monkeypatch):
  value, _now = scene
  value.render_layers()
  value.update(state(hud={"v_ego": 21, "enabled": True, "lat_active": True}))
  entered, in_hud = threading.Event(), threading.Event()
  owner = threading.get_ident()
  paint, draw_hud = LayeredRenderer._paint_path_request, value.renderer._draw_hud
  calls = []

  def path(painter, request):
    assert threading.get_ident() != owner
    calls.append(request)
    entered.set()
    assert in_hud.wait(3)
    return paint(painter, request)

  def hud(*args):
    assert threading.get_ident() == owner
    assert entered.wait(3), "the path must start before this frame's HUD"
    in_hud.set()
    wait_done(value.renderer._path_worker)
    return draw_hud(*args)

  monkeypatch.setattr(LayeredRenderer, "_paint_path_request", path)
  monkeypatch.setattr(value.renderer, "_draw_hud", hud)
  try:
    value.set_gpu_camera(True)
    result = value.render_layers()
    worker = value.renderer._path_worker
    assert result.path is not None
    assert worker.submitted == worker.completed == len(calls) == 1
    assert worker._future is None
    assert value.last_frame_timings["path"] is not None and value.last_frame_timings["path_wait"] == 0
    value.render_layers()
    assert worker.submitted == worker.completed == len(calls) == 1
    assert value.last_frame_timings["path"] is None
  finally:
    in_hud.set()


def test_early_completion_is_counted_once_when_the_late_poll_retains_it(scene, monkeypatch):
  value, _now = scene
  value.render_layers()
  entered, release, requests = block_path(monkeypatch)
  try:
    value.set_gpu_camera(True)
    value.render_layers()
    assert entered.wait(3)
    release.set()
    worker = value.renderer._path_worker
    wait_done(worker)
    result = value.render_layers()
    assert result.path is not None
    assert worker.last_reason == "retained"
    assert worker.submitted == worker.completed == len(requests) == 1
    assert value.last_frame_timings["path"] is not None
    value.render_layers()
    assert value.last_frame_timings["path"] is None
    assert worker.submitted == worker.completed == len(requests) == 1
  finally:
    release.set()


def test_late_poll_hides_a_retained_path_that_expires_during_hud(scene, monkeypatch):
  value, now = scene
  value.render_layers()
  entered, release, _requests = block_path(monkeypatch)
  try:
    value.set_gpu_camera(True)
    value.render_layers()
    assert entered.wait(3)
    release.set()
    wait_done(value.renderer._path_worker)
    release.clear()
    now[0] = 10.48
    value.update(state(hud={"v_ego": 21, "enabled": True, "lat_active": True}))
    video(value, 80)
    draw_hud = value.renderer._draw_hud
    def hud(*args):
      now[0] = 10.51
      return draw_hud(*args)
    monkeypatch.setattr(value.renderer, "_draw_hud", hud)
    result = value.render_layers()
    assert result.camera is not None and result.path is None
    assert value.last_status["model_valid"]
    assert value.last_frame_timings["path_expired"] == 1
    assert value.last_frame_timings["path"] is None
  finally:
    release.set()


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
