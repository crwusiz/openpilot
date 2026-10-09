import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest


@pytest.fixture
def renderer_module(monkeypatch):
  # Pixel compositing can be checked on a PC without target Params bindings.
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=Mock()))
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.cluster_config",
                      SimpleNamespace(Colors=SimpleNamespace(), colors_alpha=Mock()))
  spec = importlib.util.spec_from_file_location("cluster_renderer_under_test", Path(__file__).parents[1] / "cluster_renderer.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _old_alpha_blend(image, polygon, color, alpha):
  alpha = float(np.clip(alpha, 0.0, 1.0))
  if len(color) == 4:
    alpha *= color[3] / 255.0
    color = color[:3]
  if alpha <= 0.0:
    return
  x, y, width, height = cv2.boundingRect(polygon)
  x0, y0 = max(x, 0), max(y, 0)
  x1, y1 = min(x + width, image.shape[1]), min(y + height, image.shape[0])
  if x0 >= x1 or y0 >= y1:
    return
  roi = image[y0:y1, x0:x1]
  mask = np.zeros(roi.shape[:2], dtype=np.uint8)
  cv2.fillPoly(mask, [polygon - np.array([x0, y0], dtype=np.int32)], 255)
  blended = cv2.addWeighted(np.full_like(roi, color), alpha, roi, 1.0 - alpha, 0)
  cv2.copyTo(blended, mask, roi)


@pytest.mark.parametrize("color", [(255, 255, 255), (255, 0, 0), (10, 221, 101, 100)])
@pytest.mark.parametrize("alpha", [-0.1, 0.0, 0.317, 0.7, 1.0, 1.2])
@pytest.mark.parametrize("polygon", [
  [[8, 9], [55, 11], [33, 52], [16, 35]],
  [[-5, -10], [32, 10], [90, 90], [20, 40]],
  [[100, 100], [110, 100], [110, 110]],
])
def test_polygon_alpha_matches_previous_pixels(renderer_module, color, alpha, polygon):
  original = np.random.default_rng(10).integers(0, 256, (64, 72, 3), dtype=np.uint8)
  polygon = np.array(polygon, dtype=np.int32)
  expected, actual = original.copy(), original.copy()
  _old_alpha_blend(expected, polygon, color, alpha)
  renderer_module.ClusterRenderer._fill_polygon_alpha(actual, polygon, color, alpha)
  np.testing.assert_array_equal(actual, expected)


def test_alpha_lookup_matches_all_rgb_values(renderer_module):
  values = np.arange(256, dtype=np.uint8).reshape(256, 1, 1).repeat(3, axis=2)
  for color in ((255, 255, 255), (255, 0, 0), (0, 230, 105), (10, 15, 19)):
    for alpha in (0.01, 0.125, 0.5, 0.7, 100 / 255, 1.0):
      expected = cv2.addWeighted(np.full_like(values, color), alpha, values, 1 - alpha, 0)
      actual = cv2.LUT(values, renderer_module._alpha_blend_lut(color, alpha))
      np.testing.assert_array_equal(actual, expected)


def _old_gradient_blend(expected, polygon, colors, stops, gradient_bands):
  x, y, width, height = cv2.boundingRect(polygon)
  x0, y0 = max(x, 0), max(y, 0)
  x1, y1 = min(x + width, expected.shape[1]), min(y + height, expected.shape[0])
  if x0 >= x1 or y0 >= y1:
    return
  roi = expected[y0:y1, x0:x1]
  mask = np.zeros(roi.shape[:2], dtype=np.uint8)
  cv2.fillPoly(mask, [polygon - np.array([x0, y0], dtype=np.int32)], 255)
  band_height = max(1, (roi.shape[0] + gradient_bands - 1) // gradient_bands)
  color_array = np.asarray(colors, dtype=np.float32)
  for band_y0 in range(0, roi.shape[0], band_height):
    band_y1 = min(band_y0 + band_height, roi.shape[0])
    midpoint_y = y0 + (band_y0 + band_y1 - 1) * 0.5
    gradient_position = 1.0 - midpoint_y / max(expected.shape[0] - 1, 1)
    color = tuple(int(np.interp(gradient_position, stops, color_array[:, channel])) for channel in range(4))
    alpha = color[3] / 255.0
    band = roi[band_y0:band_y1]
    blended = cv2.addWeighted(np.full_like(band, color[:3]), alpha, band, 1 - alpha, 0)
    cv2.copyTo(blended, mask[band_y0:band_y1], band)


@pytest.mark.parametrize("shape,polygon", [
  ((64, 72), [[25, 0], [41, 0], [65, 62], [0, 62]]),
  ((64, 72), [[-10, 2], [35, 15], [90, 80], [-15, 50]]),
  ((460, 1288), [[620, 0], [667, 0], [1138, 459], [150, 459]]),
  ((460, 1288), [[70, 0], [79, 0], [1159, 459], [1150, 459]]),
  ((460, 1288), [[-400, 0], [200, -200], [500, 420], [-200, 510]]),
  ((460, 1288), [[-300, 10], [10, -300], [-300, -300]]),
  ((1, 72), [[-10, -5], [60, -5], [60, 5], [-10, 5]]),
])
def test_gradient_preserves_previous_bands_and_pixels(renderer_module, shape, polygon):
  actual = np.random.default_rng(13).integers(0, 256, (*shape, 3), dtype=np.uint8)
  expected = actual.copy()
  polygon = np.array(polygon, dtype=np.int32)
  colors = [(255, 0, 0, 0), (20, 180, 99, 180), (70, 210, 110, 220)]
  stops = [0.0, 0.5, 1.0]
  _old_gradient_blend(expected, polygon, colors, stops, renderer_module.GRADIENT_BANDS)
  renderer_module.ClusterRenderer._fill_polygon_gradient(actual, polygon, colors, stops)
  np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("color", [(255, 255, 255), (10, 221, 101, 100)])
@pytest.mark.parametrize("polygon", [
  [[70, 0], [79, 0], [1159, 459], [1150, 459]],
  [[620, 0], [628, 0], [719, 459], [711, 459]],
  [[-400, 0], [200, -200], [500, 420], [-200, 510]],
  [[-300, 10], [10, -300], [-300, -300]],
])
def test_large_clipped_alpha_strips_match_previous_pixels(renderer_module, color, polygon):
  original = np.random.default_rng(17).integers(0, 256, (460, 1288, 3), dtype=np.uint8)
  polygon = np.array(polygon, dtype=np.int32)
  expected, actual = original.copy(), original.copy()
  _old_alpha_blend(expected, polygon, color, 0.7)
  renderer_module.ClusterRenderer._fill_polygon_alpha(actual, polygon, color, 0.7)
  np.testing.assert_array_equal(actual, expected)


def test_lookup_cache_is_bounded_and_immutable(renderer_module):
  for value in range(512):
    table = renderer_module._alpha_blend_lut((20, 180, 99), value / 511)
    assert not table.flags.writeable
  assert renderer_module._alpha_blend_lut.cache_info().currsize == 128
