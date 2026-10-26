from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer, ColorBlendWorkspace, STOCK_PATH_GRADIENT
from openpilot.selfdrive.addon.cluster.cluster_render_support import Colors
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.scene_layers import LayeredRenderer, _gradient_palette


PALETTES = [
  (STOCK_PATH_GRADIENT, [0.0, 0.5, 1.0]),
  (Colors.STEERING_PRESSED, [0.0, 0.5, 1.0]),
  ([(231, 7, 83, 0), (11, 193, 247, 255), (93, 19, 117, 37), (255, 233, 1, 189)], [0, 0.17, 0.61, 1]),
]


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
