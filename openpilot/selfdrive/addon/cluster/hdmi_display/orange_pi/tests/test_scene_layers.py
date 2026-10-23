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
  scene.update(_state())
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
