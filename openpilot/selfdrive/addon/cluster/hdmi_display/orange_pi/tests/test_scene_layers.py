from pathlib import Path
import threading
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_scene import H264Scene, source_panel_geometry
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.scene_layers import LayeredRenderer


ASSETS = Path(__file__).resolve().parents[5] / "assets"


def _straight_path(pixels):
  height, width = pixels.shape[:2]
  return Image.frombytes("RGBa", (width, height), pixels.tobytes()).convert("RGBA")


def _state(**updates):
  state = {"source_size": [1344, 760], "model_valid": True, "hud": {"v_ego": 20, "enabled": True, "lat_active": True},
           "path": {"path_x": [], "path_y": [], "path_z": [], "calib_transform": None}, "camera_contrast": 1.08}
  state.update(updates)
  return state


def _driving_state(**updates):
  xs = list(range(5, 81, 5))
  ys = [0.0008 * x * x for x in xs]
  def lane(offset):
    return [xs, [y + offset for y in ys], [1.22] * len(xs)]
  return _state(path={"path_x": xs, "path_y": ys, "path_z": [0.0] * len(xs),
                     "calib_transform": [[672, -910, 0], [380, 0, 910], [1, 0, 0]],
                     "lane_lines": [lane(-3.6), lane(-1.8), lane(1.8), lane(3.6)],
                     "lane_line_probs": [0.5, 0.9, 0.9, 0.5],
                     "road_edges": [lane(-4.2), lane(4.2)], "road_edge_stds": [0.1, 0.2],
                     "leads": [{"present": True, "d_rel": 22, "d_camera": 22, "y_rel": 0.2, "v_rel": -1.5}]}, **updates)


@pytest.mark.parametrize("raw_size,decoded_size", [((1344, 760), (1280, 720)), ((1600, 1200), (960, 720))])
@pytest.mark.parametrize("rotate", [False, True])
def test_gpu_layers_keep_raw_projection_crop_layout_and_software_fallback(raw_size, decoded_size, rotate):
  scene = H264Scene(ASSETS, clock=lambda: 10)
  state = _state(source_size=list(raw_size), rotate_180=rotate)
  width, height = decoded_size
  rgb = np.empty((height, width, 3), dtype=np.uint8)
  rgb[:, :, 0] = np.linspace(50, 200, height).astype(np.uint8)[:, None]
  rgb[:, :, 1] = np.linspace(50, 200, width).astype(np.uint8)[None, :]
  rgb[:, :, 2] = 100
  scene.update(state)
  scene.set_video(rgb)
  expected = np.asarray(scene.render()).astype(int)
  layers = scene.render_layers()
  actual = np.asarray(layers.to_image()).astype(int)
  # Contrast is applied before GPU sampling rather than after CPU sampling.
  # The crop/projection stays identical; interpolation rounding differs <=2.
  assert np.abs(actual - expected).max() <= 2
  _, transform = source_panel_geometry(raw_size, (scene.renderer.camera_w, scene.renderer.camera_h))
  transform[0, 2] += scene.renderer.camera_x
  transform[1, 2] += scene.renderer.camera_y
  np.testing.assert_array_equal(scene.renderer._source_to_panel, transform)
  assert layers.overlay.mode == "RGBA" and layers.rotate_180 == rotate
  assert layers.overlay.getpixel((900, 250))[3] == 0
  np.testing.assert_array_equal(rgb[:, :, 2], 100)


def test_layer_camera_cache_owns_pixels_and_rebuilds_for_contrast_and_source_changes():
  scene = H264Scene(ASSETS, clock=lambda: 10)
  scene.update(_state())
  source = np.full((720, 1280, 3), 50, np.uint8)
  scene.set_video(source)
  first = scene.render_layers()
  assert scene.render_layers().camera is first.camera
  source.fill(250)
  assert first.camera[0, 0, 0] < 60
  scene.update(_state(camera_contrast=1.5))
  changed = scene.render_layers()
  assert changed.camera is not first.camera
  assert changed.camera[0, 0, 0] < first.camera[0, 0, 0]
  scene.set_video(source)
  assert scene.render_layers().camera[0, 0, 0] == 255
  scene.close()
  assert scene._layer_pixels is None and scene.renderer is None


def test_camera_preparation_overlaps_cpu_hud_work_and_closes_its_only_worker(monkeypatch):
  scene = H264Scene(ASSETS, clock=lambda: 10)
  scene.update(_state())
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  owner = threading.get_ident()
  camera_entered, hud_entered = threading.Event(), threading.Event()
  prepare, render = scene._prepare_camera_layer, scene.renderer.render_overlay

  def camera(*args):
    assert threading.get_ident() != owner
    camera_entered.set()
    assert hud_entered.wait(2), "camera preparation must overlap HUD work"
    return prepare(*args)

  def hud(*args):
    assert threading.get_ident() == owner
    hud_entered.set()
    assert camera_entered.wait(2)
    return render(*args)

  monkeypatch.setattr(scene, "_prepare_camera_layer", camera)
  monkeypatch.setattr(scene.renderer, "render_overlay", hud)
  try:
    assert scene.render_layers().camera is not None
    threads = list(scene._camera_executor._threads)
    assert len(threads) == 1
  finally:
    scene.close()
  assert not any(thread.is_alive() for thread in threads)


def test_expired_video_and_model_never_keep_gpu_camera_or_path_visible(monkeypatch):
  scene = H264Scene(ASSETS, clock=lambda: 10)
  scene.update(_driving_state())
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  draw_path = Mock(wraps=scene.renderer._draw_model_path)
  monkeypatch.setattr(scene.renderer, "_draw_model_path", draw_path)
  assert scene.render_layers().camera is not None
  assert draw_path.call_count == 1
  scene.update(_state(model_age_ms=501), received_at=10.1)
  scene.render_layers(now=10.1)
  assert draw_path.call_count == 1
  scene.update(_state(), received_at=10.6)
  expired = scene.render_layers(now=10.6)
  assert expired.camera is None and not scene.last_status["video_fresh"]
  assert not scene.last_status["model_valid"] and draw_path.call_count == 1
  assert expired.overlay.getpixel((900, 250))[3] == 255
  scene.close()


def test_camera_path_and_hud_overlap_without_queuing_old_frames(monkeypatch):
  scene = H264Scene(ASSETS, clock=lambda: 10)
  scene.renderer._parallel_path = True
  scene.update(_driving_state())
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  owner = threading.get_ident()
  rendezvous = threading.Barrier(3, timeout=3)
  workers = set()
  prepare, path, hud = scene._prepare_camera_layer, scene.renderer._render_path_layer, scene.renderer._draw_hud

  def camera_job(*args):
    workers.add(threading.get_ident())
    rendezvous.wait()
    return prepare(*args)

  def path_job(*args):
    workers.add(threading.get_ident())
    rendezvous.wait()
    return path(*args)

  def hud_job(*args):
    assert threading.get_ident() == owner
    rendezvous.wait()
    return hud(*args)

  monkeypatch.setattr(scene, "_prepare_camera_layer", camera_job)
  monkeypatch.setattr(scene.renderer, "_render_path_layer", path_job)
  monkeypatch.setattr(scene.renderer, "_draw_hud", hud_job)
  try:
    result = scene.render_layers()
    assert result.camera is not None and result.path is not None
    assert len(workers) == 2 and owner not in workers
    assert scene.last_frame_timings["path_wait"] >= 0
    threads = [*scene._camera_executor._threads, *scene.renderer._path_executor._threads]
    assert len(threads) == 2
  finally:
    scene.close()
  assert not any(thread.is_alive() for thread in threads)


@pytest.mark.parametrize("failure", ["hud", "path"])
def test_failed_frame_joins_all_workers_before_returning(monkeypatch, failure):
  scene = H264Scene(ASSETS, clock=lambda: 10)
  scene.renderer._parallel_path = True
  scene.update(_driving_state())
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  rendezvous = threading.Barrier(3, timeout=3)
  camera_done, path_done = threading.Event(), threading.Event()
  prepare = scene._prepare_camera_layer

  def camera_job(*args):
    rendezvous.wait()
    result = prepare(*args)
    camera_done.set()
    return result

  def path_job(*args):
    rendezvous.wait()
    path_done.set()
    if failure == "path":
      raise RuntimeError("path failed")
    return None, 0.0, 0.0

  def hud_job(*args):
    rendezvous.wait()
    if failure == "hud":
      raise RuntimeError("hud failed")

  monkeypatch.setattr(scene, "_prepare_camera_layer", camera_job)
  monkeypatch.setattr(scene.renderer, "_render_path_layer", path_job)
  monkeypatch.setattr(scene.renderer, "_draw_hud", hud_job)
  try:
    with pytest.raises(RuntimeError, match=f"{failure} failed"):
      scene.render_layers()
    assert camera_done.is_set() and path_done.is_set()
  finally:
    scene.close()


@pytest.mark.parametrize("rotate", [False, True])
def test_parallel_layers_match_serial_pixels_filters_and_retained_frames(monkeypatch, rotate):
  import openpilot.selfdrive.addon.cluster.cluster_renderer as common_renderer
  clock = [10.0]
  monkeypatch.setattr(common_renderer.time, "monotonic", lambda: clock[0])
  serial, parallel = [H264Scene(ASSETS, clock=lambda: clock[0]) for _ in range(2)]
  serial.renderer._parallel_path, parallel.renderer._parallel_path = False, True
  retained = []
  try:
    for i in range(8):
      clock[0] += 0.05
      state = _driving_state(rotate_180=rotate, is_metric=i % 2 == 0,
                             model_age_ms=501 if i == 4 else 0)
      state["hud"].update(steering_angle=i * 12.0, v_ego=20 + i, left_blinker=True,
                          left_blindspot=True, gps_satellites=12, wifi_strength=4)
      state["path"]["leads"][0]["d_rel"] += i
      rgb = np.full((720, 1280, 3), 60 + i, np.uint8)
      layers = []
      for scene in (serial, parallel):
        scene.update(state)
        scene.set_video(rgb)
        layers.append(scene.render_layers())
      expected, actual = layers
      np.testing.assert_array_equal(np.asarray(actual.overlay), np.asarray(expected.overlay))
      if expected.path is not None:
        np.testing.assert_array_equal(actual.path, expected.path)
        retained.append((actual.path, actual.path.copy()))
      else:
        assert actual.path is None
      assert parallel.last_status == serial.last_status
      np.testing.assert_array_equal(np.asarray(actual.to_image()), np.asarray(expected.to_image()))
    for original, copy in retained:
      np.testing.assert_array_equal(original, copy)
  finally:
    serial.close()
    parallel.close()


def test_empty_path_skips_large_transparent_canvas_and_worker():
  scene = H264Scene(ASSETS, clock=lambda: 10)
  scene.renderer._parallel_path = True
  try:
    scene.update(_state())
    scene.set_video(np.zeros((720, 1280, 3), np.uint8))
    assert scene.render_layers().path is None
    assert scene.renderer._path_executor is None and scene.renderer._path_frame is None
  finally:
    scene.close()


@pytest.mark.parametrize("cores,override,expected", [(8, None, True), (4, None, True), (2, None, False), (8, "0", False)])
def test_path_parallelism_respects_available_cores_and_opt_out(monkeypatch, cores, override, expected):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import scene_layers
  monkeypatch.setattr(scene_layers.os, "sched_getaffinity", lambda _pid: set(range(cores)), raising=False)
  if override is None:
    monkeypatch.delenv("CLUSTER_PARALLEL_PATH", raising=False)
  else:
    monkeypatch.setenv("CLUSTER_PARALLEL_PATH", override)
  scene = H264Scene(ASSETS)
  assert scene.renderer._parallel_path is expected
  scene.close()


@pytest.mark.parametrize("color,alpha", [((201, 34, 49, 100), 0.6), ((255, 255, 255, 255), 0.7), ((10, 230, 80), 0.4)])
def test_transparent_lane_and_edge_polygons_match_existing_alpha(color, alpha):
  base = np.full((190, 240, 3), 80, np.uint8)
  polygon = np.array([[-20, 180], [20, 5], [170, 35], [230, 150]], dtype=np.int32)
  expected = base.copy()
  ClusterRenderer._fill_polygon_alpha(expected, polygon, color, alpha)
  overlay = np.zeros((190, 240, 4), np.uint8)
  LayeredRenderer._fill_polygon_alpha(overlay, polygon, color, alpha)
  actual = Image.alpha_composite(Image.fromarray(base).convert("RGBA"), _straight_path(overlay)).convert("RGB")
  assert np.abs(np.asarray(actual).astype(int) - expected.astype(int)).max() <= 1
  # Pixels outside a polygon stay transparent rather than hiding the camera.
  assert overlay[0, 239, 3] == 0


def test_overlapping_gradient_path_and_lanes_blend_in_order():
  polygon = np.array([[5, 15], [35, 5], [65, 50], [15, 60]], dtype=np.int32)
  colors = [(13, 248, 122, 102), (114, 255, 92, 89), (114, 255, 92, 0)]
  base = np.full((64, 72, 3), 80, np.uint8)
  expected = base.copy()
  ClusterRenderer._fill_polygon_alpha(expected, polygon, (255, 255, 255), 0.4)
  ClusterRenderer._fill_polygon_gradient(expected, polygon, colors, [0, 0.5, 1])
  overlay = np.zeros((64, 72, 4), np.uint8)
  LayeredRenderer._fill_polygon_alpha(overlay, polygon, (255, 255, 255), 0.4)
  LayeredRenderer._fill_polygon_gradient(overlay, polygon, colors, [0, 0.5, 1])
  actual = Image.alpha_composite(Image.fromarray(base).convert("RGBA"), _straight_path(overlay)).convert("RGB")
  assert np.abs(np.asarray(actual).astype(int) - expected.astype(int)).max() <= 2
