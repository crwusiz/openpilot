from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from PIL import Image

from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer, REQUIRED_ICON_NAMES
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_scene import H264Scene, source_panel_geometry


ASSETS = Path(__file__).resolve().parents[5] / "assets"
CLUSTER = Path(__file__).resolve().parents[3]


def snapshot(source_size=(1344, 760), **overrides):
  state = {
    "model_valid": True, "hud": {"v_ego": 20.0, "accel": 0.0, "enabled": True, "lat_active": True},
    "path": {"path_x": [], "path_y": [], "path_z": [], "calib_transform": None},
    "source_size": None if source_size is None else list(source_size), "is_metric": True, "rotate_180": False,
  }
  state.update(overrides)
  return state


@pytest.fixture
def scene():
  return H264Scene(ASSETS, clock=lambda: 10.0)


def test_required_assets_keep_original_font_sizes_and_all_hud_icons(scene):
  assert scene.renderer.font_current_speed.size == 92
  assert scene.renderer.font_speed.size == 58
  assert scene.renderer.font_value.size == 42
  assert scene.renderer.font_unit.size == 20
  for name in ("speed", "current_speed", "value", "unit", "current_unit", "small", "label", "warning"):
    assert Path(getattr(scene.renderer, f"font_{name}").path).name == "NotoSansKR-Bold.ttf"
  assert set(scene.renderer.icons) == set(REQUIRED_ICON_NAMES)
  manifest = Path(__file__).resolve().parents[1] / "receiver_assets.txt"
  assert set(manifest.read_text().splitlines()) == {
    "assets/fonts/NotoSansKR-Bold.ttf",
    *(f"assets/icons/{name}.png" for name in REQUIRED_ICON_NAMES),
  }


@pytest.mark.parametrize("relative", ["fonts/NotoSansKR-Bold.ttf", "icons/steer_green.png", "icons/wifi_strength_full.png"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_incomplete_assets_reject_h264_instead_of_showing_tiny_fonts_or_missing_icons(tmp_path, relative, corrupt):
  assets = tmp_path / "assets"
  for name in ("fonts/NotoSansKR-Bold.ttf", *(f"icons/{name}.png" for name in REQUIRED_ICON_NAMES)):
    destination = assets / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / name, destination)
  path = assets / relative
  if corrupt:
    path.write_bytes(b"broken asset")
  else:
    path.unlink()
  with pytest.raises(RuntimeError, match="Failed to load cluster") as error:
    H264Scene(assets)
  assert path.name in str(error.value)
  assert str(assets) in str(error.value)
  assert "service-account directory permissions" in str(error.value)

  # Once a deployment repairs the bundle, the next connection loads real assets.
  shutil.copyfile(ASSETS / relative, path)
  repaired = H264Scene(assets)
  assert repaired.renderer.font_current_speed.size == 92
  assert set(repaired.renderer.icons) == set(REQUIRED_ICON_NAMES)


def test_raw_sensor_crop_matches_existing_even_nv12_geometry():
  crop, transform = source_panel_geometry((1344, 760), (1288, 460))
  assert crop == (0, 140, 1344, 480)
  expected = np.array([[1288 / 1344, 0, 0], [0, 460 / 480, -140 * 460 / 480], [0, 0, 1]], dtype=np.float32)
  np.testing.assert_array_equal(transform, expected)
  crop, transform = source_panel_geometry((1920, 480), (600, 800))
  assert crop == (780, 0, 360, 480)
  assert transform[0, 2] == pytest.approx(-780 * 600 / 360)


@pytest.mark.parametrize("raw_size,decoded_size", [((1344, 760), (1280, 720)), ((1600, 1200), (1280, 720))])
def test_scaled_camera_uses_raw_crop_and_raw_projection(scene, raw_size, decoded_size):
  raw_w, raw_h = raw_size
  raw = np.zeros((raw_h, raw_w, 3), dtype=np.uint8)
  raw[:, :, 0] = np.round(np.linspace(0, 255, raw_h))[:, None]
  raw[:, :, 1] = np.round(np.linspace(0, 255, raw_w))[None, :]
  raw[:, :, 2] = 100
  decoded = cv2.resize(raw, decoded_size, interpolation=cv2.INTER_LINEAR)
  scene.update(snapshot(raw_size))
  scene.set_video(decoded)
  scene.render()

  (x, y, width, height), expected_transform = source_panel_geometry(raw_size, (1288, 460))
  expected = cv2.resize(raw[y:y + height, x:x + width], (1288, 460), interpolation=cv2.INTER_LINEAR)
  cv2.LUT(expected, scene._contrast_lut, dst=expected)
  # Encoding/resizing has its own sampling grid. A smooth source distinguishes
  # the original sensor crop from a crop recalculated at decoded aspect ratio.
  assert np.abs(scene._panel.astype(int) - expected.astype(int)).max() <= 3
  np.testing.assert_array_equal(scene.renderer._source_to_panel, expected_transform + np.array([
    [0, 0, scene.renderer.camera_x], [0, 0, scene.renderer.camera_y], [0, 0, 0],
  ], dtype=np.float32))


def test_scene_retains_the_same_c4_hud_layout_and_pixel_result(scene):
  raw = np.random.default_rng(22).integers(0, 256, (760, 1344, 3), dtype=np.uint8)
  state = snapshot()
  state["hud"].update(steering_angle=12.0, cruise_speed=100.0, set_speed=100.0, is_cruise_set=True,
                      tpms=[35, 36, 35, 36], gps_satellites=12, wifi_strength=4, distance_level=3)
  scene.update(state)
  scene.set_video(raw)
  image = scene.render()

  expected_renderer = ClusterRenderer(scene.config)
  cropped = raw[140:620]
  panel = cv2.resize(cropped, (1288, 460), interpolation=cv2.INTER_LINEAR)
  cv2.LUT(panel, scene._contrast_lut, dst=panel)
  _, transform = source_panel_geometry((1344, 760), (1288, 460))
  transform[0, 2] += expected_renderer.camera_x
  transform[1, 2] += expected_renderer.camera_y
  camera = SimpleNamespace(get_frame=lambda: panel, get_source_to_panel_transform=lambda *_args: transform.copy())
  models = SimpleNamespace(get_render_data=lambda: (True, state["hud"], state["path"]))
  expected = expected_renderer.render(camera, models)

  assert image.mode == "RGB" and image.size == (1920, 480)
  assert "steer_green" in scene.renderer.icons
  np.testing.assert_array_equal(np.asarray(image), np.asarray(expected))


def _speed_values(scene, monkeypatch):
  values = []
  original = scene.renderer._centered
  def centered(draw, cx, cy, value, font, *args, **kwargs):
    if font is scene.renderer.font_current_speed:
      values.append(value)
    return original(draw, cx, cy, value, font, *args, **kwargs)
  monkeypatch.setattr(scene.renderer, "_centered", centered)
  return values


def test_metadata_and_video_have_independent_freshness_and_recover(scene, monkeypatch):
  speeds = _speed_values(scene, monkeypatch)
  scene.update(snapshot(), received_at=10.0)
  scene.set_video(np.full((720, 1280, 3), 50, np.uint8), received_at=10.0)
  scene.render(now=10.0)
  assert scene.last_status == {"state_fresh": True, "video_fresh": True, "model_valid": True}
  assert speeds[-1] == 72

  # New video cannot refresh a metadata timestamp or keep old speed/path alive.
  scene.set_video(np.full((720, 1280, 3), 60, np.uint8), received_at=10.6)
  scene.render(now=10.6)
  assert scene.last_status == {"state_fresh": False, "video_fresh": True, "model_valid": False}
  assert speeds[-1] == "--"
  scene.update(snapshot(), received_at=10.7)
  scene.render(now=10.7)
  assert scene.last_status["model_valid"] and speeds[-1] == 72

  # New metadata cannot keep a frozen camera image or its path on screen.
  scene.update(snapshot(), received_at=11.2)
  camera = Mock(wraps=scene.renderer._draw_hud)
  monkeypatch.setattr(scene.renderer, "_draw_hud", camera)
  scene.render(now=11.2)
  assert scene.last_status == {"state_fresh": True, "video_fresh": False, "model_valid": False}
  assert camera.call_args.args[2] is False
  assert speeds[-1] == 72


def test_source_model_staleness_hides_path_while_hud_keeps_updating(scene):
  scene.update(snapshot(health={"model_age_ms": 501.0}))
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  scene.render()
  assert scene.last_status == {"state_fresh": True, "video_fresh": True, "model_valid": False}


@pytest.mark.parametrize("location", ["top", "health"])
@pytest.mark.parametrize("field", ["model_age_ms", "car_state_age_ms"])
@pytest.mark.parametrize("age", [None, 501.0])
def test_source_input_staleness_is_not_hidden_by_fresh_network_snapshots(scene, monkeypatch, location, field, age):
  speeds = _speed_values(scene, monkeypatch)
  state = snapshot()
  if location == "top":
    state[field] = age
  else:
    state["health"] = {field: age}
  scene.update(state)
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  scene.render()
  assert scene.last_status["video_fresh"] and not scene.last_status["model_valid"]
  assert scene.last_status["state_fresh"] is (field != "car_state_age_ms")
  assert speeds[-1] == ("--" if field == "car_state_age_ms" else 72)


@pytest.mark.parametrize("field", ["model_age_ms", "car_state_age_ms"])
def test_source_input_age_keeps_advancing_after_its_metadata_arrival(scene, field):
  scene.update(snapshot(**{field: 450.0}))
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  scene.render(now=10.04)
  assert scene.last_status["model_valid"]
  scene.render(now=10.06)
  assert scene.last_status["video_fresh"] and not scene.last_status["model_valid"]
  # Receiving a fresh snapshot with a fresh source age restores both displays.
  scene.update(snapshot(**{field: 10.0}), received_at=10.07)
  scene.render(now=10.07)
  assert scene.last_status["model_valid"] and scene.last_status["state_fresh"]


def test_top_level_source_age_takes_precedence_over_legacy_health(scene):
  scene.update(snapshot(model_age_ms=0.0, health={"model_age_ms": 600.0}))
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  scene.render()
  assert scene.last_status["model_valid"]


def test_sender_camera_contrast_updates_cached_panel_without_a_new_video(scene):
  scene.update(snapshot())
  scene.set_video(np.full((720, 1280, 3), 100, np.uint8))
  scene.render()
  original_panel = scene._panel
  assert original_panel.min() == original_panel.max() == 97
  scene.update(snapshot(camera_contrast=1.0))
  scene.render()
  assert scene._panel is not original_panel
  assert scene._panel.min() == scene._panel.max() == 100
  panel, lut = scene._panel, scene._contrast_lut
  scene.render()
  assert scene._panel is panel and scene._contrast_lut is lut
  scene.update(snapshot(camera_contrast=0.5))
  scene.render()
  assert scene._panel.min() == scene._panel.max() == 113
  scene.update(snapshot(camera_contrast=3.0))
  scene.render()
  assert scene._panel.min() == scene._panel.max() == 97


def test_scene_does_not_render_unknown_raw_geometry(scene):
  scene.update(snapshot(source_size=None))
  scene.set_video(np.zeros((720, 1280, 3), np.uint8))
  scene.render()
  assert scene.last_status["model_valid"] is False
  assert scene._panel is None


def test_scene_owns_input_pixels_and_state_and_caches_only_current_video(scene, monkeypatch):
  speeds = _speed_values(scene, monkeypatch)
  state = snapshot()
  video = np.full((720, 1280, 3), 50, np.uint8)
  scene.update(state)
  scene.set_video(video)
  state["hud"]["v_ego"] = 100.0
  video[:] = 255
  first = scene.render()
  assert speeds[-1] == 72 and scene._panel.max() < 100
  old_panel = scene._panel
  scene.render()
  assert scene._panel is old_panel
  retained = np.asarray(first).copy()

  # Multiple decoder updates may arrive between renders, even at the same
  # fake timestamp. Reusing old buffer identities must never freeze the panel.
  scene.set_video(np.full((720, 1280, 3), 100, np.uint8))
  scene.set_video(np.full((720, 1280, 3), 150, np.uint8))
  second = scene.render()
  assert scene._panel is not old_panel and scene._panel.min() > 100
  np.testing.assert_array_equal(np.asarray(first), retained)
  assert not np.array_equal(np.asarray(first), np.asarray(second))


@pytest.mark.parametrize("contiguous", [False, True])
def test_scene_can_receive_exclusive_rgb_ownership_without_a_second_frame_copy(scene, contiguous):
  video = np.full((720, 1280, 3), 50, np.uint8)
  if not contiguous:
    video = video[:, ::-1]
  scene.update(snapshot())
  scene.set_video(video, received_at=10.0, take_ownership=True)
  assert scene._video.flags.c_contiguous
  assert np.shares_memory(scene._video, video) is contiguous
  first = scene.render()
  retained = np.asarray(first).copy()
  scene.set_video(np.full((720, 1280, 3), 150, np.uint8), received_at=10.1, take_ownership=True)
  scene.render(now=10.1)
  np.testing.assert_array_equal(np.asarray(first), retained)
  assert scene._video_received_at == 10.1
  assert scene.last_frame_timings["camera_panel"] >= 0


def test_units_and_sender_rotation_match_the_shared_renderer(scene, monkeypatch):
  speeds = _speed_values(scene, monkeypatch)
  scene.set_video(np.full((720, 1280, 3), 80, np.uint8))
  state = snapshot(model_valid=False, is_metric=False)
  scene.update(state)
  image = scene.render()
  assert scene.config.speed_unit == "mph" and speeds[-1] == 45
  state["rotate_180"] = True
  scene.update(state)
  rotated = scene.render()
  np.testing.assert_array_equal(np.asarray(rotated), np.asarray(image.transpose(Image.Transpose.ROTATE_180)))


@pytest.mark.parametrize("bad_state", [
  {"source_size": [0, 760]}, {"source_size": [1344, 9000]},
  {"hud": {"v_ego": float("nan")}}, {"hud": {"tpms": [1, 2]}},
  {"path": {"path_x": [1] * 65}}, {"path": {"calib_transform": [[1, 2], [3, 4]]}},
])
def test_invalid_scene_is_rejected_before_replacing_valid_state(scene, bad_state):
  state = snapshot()
  scene.update(state)
  original = scene._snapshot
  state.update(deepcopy(bad_state))
  with pytest.raises(ValueError):
    scene.update(state)
  assert scene._snapshot is original


def test_renderer_and_scene_run_from_standalone_pi_bundle_without_openpilot(tmp_path):
  for source in (CLUSTER / "cluster_renderer.py", CLUSTER / "cluster_render_support.py",
                 CLUSTER / "hdmi_display/orange_pi/h264_scene.py", CLUSTER / "hdmi_display/orange_pi/h264_protocol.py",
                 CLUSTER / "hdmi_display/orange_pi/scene_layers.py", CLUSTER / "hdmi_display/orange_pi/path_worker.py"):
    shutil.copyfile(source, tmp_path / source.name)
  code = """
import sys
sys.path.insert(0, sys.argv[1])
sys.modules['openpilot'] = None
import numpy as np
from h264_scene import H264Scene
from cluster_renderer import FirstOrderFilter, Colors
assert FirstOrderFilter.__module__ == 'cluster_render_support'
assert Colors.__module__ == 'cluster_render_support'
scene=H264Scene(sys.argv[2], clock=lambda: 10.0)
scene.update({'model_valid':False,'hud':{},'path':{},'source_size':[1344,760]})
scene.set_video(np.zeros((720,1280,3),np.uint8))
image=scene.render()
assert image.size==(1920,480) and image.mode=='RGB'
assert scene.last_status['state_fresh'] and scene.last_status['video_fresh']
"""
  result = subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path), str(ASSETS)],
                          check=False, capture_output=True, text=True, timeout=20)
  assert result.returncode == 0, result.stderr
