from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer, ColorBlendWorkspace, LeadVehicle, StopPoint, STOCK_PATH_GRADIENT
from openpilot.selfdrive.addon.cluster.cluster_render_support import Colors
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.scene_layers import LayeredRenderer, _gradient_palette


PALETTES = [
  (STOCK_PATH_GRADIENT, [0.0, 0.5, 1.0]),
  (Colors.STEERING_PRESSED, [0.0, 0.5, 1.0]),
  ([(231, 7, 83, 0), (11, 193, 247, 255), (93, 19, 117, 37), (255, 233, 1, 189)], [0, 0.17, 0.61, 1]),
]


def _make_path_renderer(renderer_class):
  renderer = renderer_class.__new__(renderer_class)
  renderer.camera_x, renderer.camera_y = 4, 5
  renderer.camera_w, renderer.camera_h = 160, 80
  renderer.camera_height = 1.0
  renderer._source_to_panel = np.eye(3, dtype=np.float32)
  renderer._color_blender = ColorBlendWorkspace(renderer.camera_w)
  renderer._lead_vehicles = [LeadVehicle(20), LeadVehicle(20)]
  renderer._lead_info = [{}, {}]
  renderer._stop_point = StopPoint(20)
  if isinstance(renderer, LayeredRenderer):
    renderer.base_canvas = np.zeros((85, 164, 3), dtype=np.uint8)
    renderer._path_frame = None
  return renderer


def _render_path(renderer, path, hud):
  if isinstance(renderer, LayeredRenderer):
    pixels, _, _ = renderer._render_path_layer(path, hud)
    assert pixels is not None
    return pixels
  frame = np.full((85, 164, 3), 128, dtype=np.uint8)
  return renderer._draw_model_path(frame, path, hud)[renderer.camera_y:, renderer.camera_x:]


def _render_path_samples(renderer_class, acceleration, hud_updates):
  renderer = _make_path_renderer(renderer_class)
  # The first two points are behind the car. The valid path projects to the
  # center column at rows 76, 66, ..., 16 with the camera offset removed.
  path = {
    "path_x": [-10, -5, 0, 10, 20, 30, 40, 50, 60],
    "path_y": [0.0] * 9,
    "path_z": [0.0] * 9,
    "calib_transform": [[0, -10, 81], [-1, 0, 81], [0, 0, 1]],
  }
  if acceleration is not None:
    path["acceleration_x"] = acceleration
  hud = {"enabled": True, "lat_active": True, **hud_updates}
  return _render_path(renderer, path, hud)[[68, 35], 77]


@pytest.mark.parametrize("renderer_class", [ClusterRenderer, LayeredRenderer], ids=["usb", "hdmi"])
@pytest.mark.parametrize("acceleration,hud_updates,expected_channels", [
  pytest.param([2.0] * 9, {}, (1, 1), id="accelerating"),
  pytest.param([-2.0] * 9, {}, (0, 0), id="decelerating"),
  pytest.param([-2.0] * 6 + [2.0] * 3, {}, (0, 1), id="clipped-mixed-acceleration"),
  pytest.param([-2.0] * 9, {"steering_pressed": True}, (2, 2), id="steering-torque"),
  pytest.param([-2.0] * 9, {"enabled": False, "cruise_available": True}, (1, 1), id="cruise-ready"),
  pytest.param([-2.0] * 9, {"enabled": False, "cruise_available": True, "steering_pressed": True}, (2, 2), id="cruise-ready-torque"),
])
def test_path_pixels_reflect_acceleration_and_steering(renderer_class, acceleration, hud_updates, expected_channels):
  pixels = _render_path_samples(renderer_class, acceleration, hud_updates)
  for rgb, channel in zip(pixels[:, :3], expected_channels, strict=True):
    assert rgb[channel] > max(np.delete(rgb, channel)), rgb


@pytest.mark.parametrize("renderer_class", [ClusterRenderer, LayeredRenderer], ids=["usb", "hdmi"])
@pytest.mark.parametrize("acceleration,hud_updates,value,alpha", [
  pytest.param([-2.0] * 9, {"enabled": False, "steering_pressed": True}, 255, 90, id="disengaged-with-torque"),
  pytest.param(None, {}, 255, 30, id="legacy-state"),
  pytest.param([-2.0], {}, 255, 30, id="insufficient-acceleration"),
])
def test_path_uses_solid_color_when_disengaged_or_gradient_unavailable(renderer_class, acceleration, hud_updates, value, alpha):
  pixels = _render_path_samples(renderer_class, acceleration, hud_updates)
  if renderer_class is LayeredRenderer:
    expected = (round(value * alpha / 255),) * 3 + (alpha,)
  else:
    expected = (round(value * alpha / 255 + 128 * (1 - alpha / 255)),) * 3
  np.testing.assert_array_equal(pixels, [expected, expected])


@pytest.mark.parametrize("renderer_class", [ClusterRenderer, LayeredRenderer], ids=["usb", "hdmi"])
@pytest.mark.parametrize("has_velocity,longitudinal_control,lead_distance,shows_stop", [
  pytest.param(True, True, None, True, id="planned-stop"),
  pytest.param(True, False, None, False, id="stock-longitudinal"),
  pytest.param(True, True, 25.0, False, id="nearby-lead"),
  pytest.param(False, True, None, False, id="legacy-state"),
])
def test_planned_stop_is_rendered_with_longitudinal_control_and_no_nearby_lead(
    renderer_class, has_velocity, longitudinal_control, lead_distance, shows_stop):
  renderer = _make_path_renderer(renderer_class)
  path = {
    "path_x": [0.0, 10.0, 20.0, 30.0, 40.0],
    "path_y": [0.0] * 5,
    "path_z": [0.0] * 5,
    "calib_transform": [[0, -10, 81], [-1, 0, 81], [0, 0, 1]],
    "model_frame_id": 17,
  }
  if has_velocity:
    path["velocity_x"] = [4.0, 2.0, 0.0, 0.0, 0.0]
  if lead_distance is not None:
    path["leads"] = [{"present": True, "d_camera": lead_distance}]
  hud = {"v_ego": 0.0, "enabled": True, "lat_active": True, "longitudinal_control": longitudinal_control}

  initial = _render_path(renderer, path, hud)
  for _ in range(60):
    pixels = _render_path(renderer, path, hud)

  if shows_stop:
    assert renderer._stop_point.d == pytest.approx(15.0)
    # The center of the stop bar is about 15 m ahead, inside the path at row 61.
    assert np.all(pixels[61, 77] > initial[61, 77])
    assert np.all(pixels[61, 77, :3] == pixels[61, 77, 0])
    np.testing.assert_array_equal(pixels[45, 77], initial[45, 77])

    # Camera frames may repeat the same model frame, while the car keeps moving.
    moving_hud = {**hud, "v_ego": 6.0}
    _render_path(renderer, path, moving_hud)
    _render_path(renderer, path, moving_hud)
    assert renderer._stop_point.d == pytest.approx(15.0 - 2 * 6.0 / 20)
    _render_path(renderer, {**path, "model_frame_id": 18}, hud)
    assert 15.0 - 2 * 6.0 / 20 < renderer._stop_point.d <= 15.0
  else:
    np.testing.assert_array_equal(pixels, initial)


def _polygon(kind, width, height):
  if kind == "triangle":
    points = [(width // 2, height // 4), (width - 1, height - 1), (0, height - 1)]
  elif kind == "clipped":
    points = [(-width, -height), (width // 2, 0), (width * 2, height // 2), (width // 3, height * 2)]
  elif kind == "thin":
    points = [(0, 0), (3, 0), (width - 1, height - 1), (width - 4, height - 1)]
  elif kind == "concave":
    points = [(-3, height // 3), (width, -7), (width // 2, height // 2),
              (width + 7, height), (width // 3, height - 1)]
  else:
    points = [(-width, 0), (-2, height // 2), (-width, height)]
  return np.asarray(points, dtype=np.int32)


@pytest.mark.parametrize("width,height", [(13, 1), (137, 96), (1288, 460)])
@pytest.mark.parametrize("kind", ["triangle", "clipped", "thin", "concave", "outside"])
@pytest.mark.parametrize("colors,stops", PALETTES)
@pytest.mark.parametrize("blender_channels", [None, 3, 4])
def test_rgba_palette_preserves_common_gradient_pixels(width, height, kind, colors, stops, blender_channels):
  expected = np.random.default_rng(136).integers(0, 256, (height, width, 4), dtype=np.uint8)
  actual = expected.copy()
  polygon = _polygon(kind, width, height)
  ClusterRenderer._fill_polygon_gradient(expected, polygon, colors, stops, ColorBlendWorkspace(width, channels=4))
  blender = None if blender_channels is None else ColorBlendWorkspace(width, channels=blender_channels)
  LayeredRenderer._fill_polygon_gradient(actual, polygon, colors, stops, blender)
  np.testing.assert_array_equal(actual, expected)


def test_rgba_palette_preserves_noncontiguous_camera_regions_and_overlap():
  expected = np.random.default_rng(45).integers(0, 256, (480, 1920, 4), dtype=np.uint8)
  actual = expected.copy()
  expected_region, actual_region = expected[10:470, 316:1604], actual[10:470, 316:1604]
  for index, (colors, stops) in enumerate(PALETTES):
    polygon = _polygon("concave" if index % 2 else "clipped", 1288, 460)
    ClusterRenderer._fill_polygon_gradient(expected_region, polygon, colors, stops, ColorBlendWorkspace(1288, channels=4))
    LayeredRenderer._fill_polygon_gradient(actual_region, polygon, colors, stops)
  np.testing.assert_array_equal(actual, expected)


def test_rgb_gradient_keeps_common_renderer_and_supplied_workspace(monkeypatch):
  expected = Mock(return_value=object())
  monkeypatch.setattr(ClusterRenderer, "_fill_polygon_gradient", expected)
  image = np.zeros((96, 137, 3), dtype=np.uint8)
  polygon = _polygon("clipped", 137, 96)
  blender = ColorBlendWorkspace(137)
  colors, stops = PALETTES[0]
  assert LayeredRenderer._fill_polygon_gradient(image, polygon, colors, stops, blender) is expected.return_value
  expected.assert_called_once_with(image, polygon, colors, stops, blender)


def test_palette_cache_bounds_memory_to_immutable_band_geometry_and_inks():
  _gradient_palette.cache_clear()
  colors, stops = tuple(map(tuple, PALETTES[0][0])), tuple(PALETTES[0][1])
  first = _gradient_palette(10, 460, 1080, colors, stops)
  assert _gradient_palette(10, 460, 1080, colors, stops) is first
  assert _gradient_palette.cache_info().hits == 1
  for y0 in range(200):
    result = _gradient_palette(y0, 460, 1080, colors, stops)
    assert isinstance(result, tuple) and len(result) <= 8
    for start, end, ink in result:
      assert type(start) is type(end) is int
      assert isinstance(ink, tuple) and all(type(channel) is int for channel in ink)
  info = _gradient_palette.cache_info()
  assert info.maxsize == info.currsize == 128
  assert _gradient_palette(10, 460, 1080, colors, stops) == first
  _gradient_palette.cache_clear()
