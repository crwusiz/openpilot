import importlib.util
from collections import OrderedDict
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont


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


@pytest.mark.parametrize("distance", [0.0, -10.0, -87.5])
def test_speed_limit_selector_keeps_rear_camera_during_hold(renderer_module, distance):
  data = {
    "nda_state": 1, "nav_limit_speed": 110.0,
    "cam_limit_speed": 70.0, "cam_limit_speed_left_dist": distance, "cam_limit_active": True,
  }

  assert renderer_module.ClusterRenderer._active_speed_limit(data) == (70.0, distance)


def test_speed_limit_selector_honors_camera_expiry_with_positive_distance(renderer_module):
  data = {
    "nda_state": 1, "nav_limit_speed": 110.0,
    "cam_limit_speed": 70.0, "cam_limit_speed_left_dist": 200.0, "cam_limit_active": False,
  }

  assert renderer_module.ClusterRenderer._active_speed_limit(data) == (110.0, 0.0)


def test_speed_limit_selector_keeps_section_priority_during_rear_hold(renderer_module):
  data = {
    "nda_state": 1, "nav_limit_speed": 110.0,
    "cam_limit_speed": 70.0, "cam_limit_speed_left_dist": -10.0, "cam_limit_active": True,
    "section_limit_speed": 60.0, "section_left_dist": 1000.0,
  }

  assert renderer_module.ClusterRenderer._active_speed_limit(data) == (60.0, 1000.0)


def test_speed_limit_selector_supports_legacy_camera_snapshot(renderer_module):
  data = {"nda_state": 1, "cam_limit_speed": 70.0, "cam_limit_speed_left_dist": 200.0}

  assert renderer_module.ClusterRenderer._active_speed_limit(data) == (70.0, 200.0)


def test_smoothing_advances_during_transport_backpressure(renderer_module):
  renderer = renderer_module.ClusterRenderer.__new__(renderer_module.ClusterRenderer)
  renderer.config = SimpleNamespace(fps=60)
  renderer._last_render_at = None
  renderer._lead_vehicles = [renderer_module.LeadVehicle(60)]
  lead = renderer._lead_vehicles[0]
  renderer._update_filter_intervals(10.0)
  lead.d_filter.update(10.0)

  # Only five frames are rendered per second while the socket is blocked.
  renderer._update_filter_intervals(10.2)

  assert lead.d_filter.update(20.0) == pytest.approx(15.0)
  assert lead.fade_filter.update(1.0) == pytest.approx(2 / 3)


def test_smoothing_recovers_to_normal_frame_interval(renderer_module):
  renderer = renderer_module.ClusterRenderer.__new__(renderer_module.ClusterRenderer)
  renderer.config = SimpleNamespace(fps=20)
  renderer._last_render_at = None
  renderer._lead_vehicles = [renderer_module.LeadVehicle(20)]
  renderer._update_filter_intervals(0.0)
  renderer._update_filter_intervals(2.0)
  assert renderer._lead_vehicles[0].fade_filter.update(1.0) > 0.9

  renderer._update_filter_intervals(2.05)

  assert renderer._lead_vehicles[0].d_filter.alpha == pytest.approx(0.2)


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
@pytest.mark.parametrize("reuse", [False, True])
def test_polygon_alpha_matches_previous_pixels(renderer_module, color, alpha, polygon, reuse):
  original = np.random.default_rng(10).integers(0, 256, (64, 72, 3), dtype=np.uint8)
  polygon = np.array(polygon, dtype=np.int32)
  expected, actual = original.copy(), original.copy()
  _old_alpha_blend(expected, polygon, color, alpha)
  blender = renderer_module.ColorBlendWorkspace(72) if reuse else None
  renderer_module.ClusterRenderer._fill_polygon_alpha(actual, polygon, color, alpha, blender=blender)
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
@pytest.mark.parametrize("reuse", [False, True])
def test_gradient_preserves_previous_bands_and_pixels(renderer_module, shape, polygon, reuse):
  actual = np.random.default_rng(13).integers(0, 256, (*shape, 3), dtype=np.uint8)
  expected = actual.copy()
  polygon = np.array(polygon, dtype=np.int32)
  colors = [(255, 0, 0, 0), (20, 180, 99, 180), (70, 210, 110, 220)]
  stops = [0.0, 0.5, 1.0]
  _old_gradient_blend(expected, polygon, colors, stops, renderer_module.GRADIENT_BANDS)
  blender = renderer_module.ColorBlendWorkspace(shape[1]) if reuse else None
  renderer_module.ClusterRenderer._fill_polygon_gradient(actual, polygon, colors, stops, blender=blender)
  np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("color", [(255, 255, 255), (10, 221, 101, 100)])
@pytest.mark.parametrize("polygon", [
  [[70, 0], [79, 0], [1159, 459], [1150, 459]],
  [[620, 0], [628, 0], [719, 459], [711, 459]],
  [[-400, 0], [200, -200], [500, 420], [-200, 510]],
  [[-300, 10], [10, -300], [-300, -300]],
])
@pytest.mark.parametrize("reuse", [False, True])
def test_large_clipped_alpha_strips_match_previous_pixels(renderer_module, color, polygon, reuse):
  original = np.random.default_rng(17).integers(0, 256, (460, 1288, 3), dtype=np.uint8)
  polygon = np.array(polygon, dtype=np.int32)
  expected, actual = original.copy(), original.copy()
  _old_alpha_blend(expected, polygon, color, 0.7)
  blender = renderer_module.ColorBlendWorkspace(1288) if reuse else None
  renderer_module.ClusterRenderer._fill_polygon_alpha(actual, polygon, color, 0.7, blender=blender)
  np.testing.assert_array_equal(actual, expected)


def _previous_lane_lines(renderer, region, data, calib):
  lane_probs = data.get("lane_line_probs") or []
  for index, line in enumerate(data.get("lane_lines") or []):
    if len(line) != 3:
      continue
    probability = float(lane_probs[index]) if index < len(lane_probs) else 0.0
    polygon = renderer._map_line_to_polygon(*line, 0.025 * probability, 0.0, calib)
    if polygon is not None:
      renderer._fill_polygon_alpha(region, polygon, (255, 255, 255, 255), np.clip(probability, 0.0, 0.7),
                                   blender=renderer._color_blender)
  road_stds = data.get("road_edge_stds") or []
  for index, edge in enumerate(data.get("road_edges") or []):
    if len(edge) != 3:
      continue
    confidence = 1.0 - float(road_stds[index]) if index < len(road_stds) else 0.0
    polygon = renderer._map_line_to_polygon(*edge, 0.025, 0.0, calib)
    if polygon is not None:
      renderer._fill_polygon_alpha(region, polygon, (201, 34, 49, 100), np.clip(confidence, 0.0, 1.0),
                                   blender=renderer._color_blender)


@pytest.mark.parametrize("probabilities,edge_stds,visible_count", [
  ([0.8, 0.5, 0.2, 0.9], [0.1, 0.5], 6),
  (None, None, 0),
  ([], [], 0),
  ([-0.5, 0.0, 0.0, -1.0], [1.0, 1.1], 0),
  ([1.8, 0.3, -0.2, 0.0], [-0.1, 1.5], 3),
  ([0.6], [0.2], 2),
])
def test_transparent_lane_geometry_is_skipped_without_changing_pixels(renderer_module, probabilities, edge_stds, visible_count):
  renderer_module.Colors.WHITE = (255, 255, 255, 255)
  renderer_module.Colors.RED = (201, 34, 49, 255)
  renderer_module.colors_alpha = lambda color, alpha: (*color[:3], alpha)
  renderer = renderer_module.ClusterRenderer.__new__(renderer_module.ClusterRenderer)
  renderer.camera_x = renderer.camera_y = 0
  renderer.camera_w, renderer.camera_h = 72, 64
  renderer._source_to_panel = np.eye(3, dtype=np.float32)
  renderer._color_blender = renderer_module.ColorBlendWorkspace(72)
  distances = np.linspace(3.0, 40.0, 33)
  heights = np.linspace(1.5, 0.4, 33)
  def line(offset):
    return [distances.tolist(), np.full_like(distances, offset).tolist(), heights.tolist()]

  data = {
    "lane_lines": [line(offset) for offset in (-3.6, -1.2, 1.2, 3.6)] + [[[], []]],
    "lane_line_probs": probabilities,
    "road_edges": [line(-4.5), line(4.5), [[], []]],
    "road_edge_stds": edge_stds,
  }
  calibration = np.array([[36, -40, 0], [20, 0, 40], [1, 0, 0]], dtype=np.float32)
  original = np.random.default_rng(29).integers(0, 256, (64, 72, 3), dtype=np.uint8)
  expected, actual = original.copy(), original.copy()
  _previous_lane_lines(renderer, expected, data, calibration)
  renderer._map_line_to_polygon = Mock(wraps=renderer._map_line_to_polygon)

  renderer._draw_lane_lines(actual, data, calibration)

  np.testing.assert_array_equal(actual, expected)
  assert renderer._map_line_to_polygon.call_count == visible_count
  assert bool(np.any(actual != original)) == bool(visible_count)


def test_lookup_cache_is_bounded_and_immutable(renderer_module):
  for value in range(512):
    table = renderer_module._alpha_blend_lut((20, 180, 99), value / 511)
    assert not table.flags.writeable
  assert renderer_module._alpha_blend_lut.cache_info().currsize == 128


def test_color_blend_workspace_reuses_output_and_bounds_color_memory(renderer_module):
  blender = renderer_module.ColorBlendWorkspace(72)
  image = np.random.default_rng(19).integers(0, 256, (37, 63, 3), dtype=np.uint8)
  original = image.copy()
  output = blender.blend(image, (20, 180, 99), 0.5)
  saved = output.copy()
  assert np.shares_memory(output, blender._output)
  for value in range(32):
    output = blender.blend(image, (value, 180, 99), 0.317)
    assert np.shares_memory(output, blender._output)
    expected = cv2.addWeighted(np.full_like(image, (value, 180, 99)), 0.317, image, 0.683, 0)
    np.testing.assert_array_equal(output, expected)
  np.testing.assert_array_equal(image, original)
  np.testing.assert_array_equal(saved, cv2.addWeighted(np.full_like(image, (20, 180, 99)), 0.5, image, 0.5, 0))
  assert len(blender._colors) == renderer_module.GRADIENT_BANDS + 4


@pytest.mark.parametrize("preprocessed", [False, True])
def test_retained_render_frames_survive_camera_changes_and_stalls(renderer_module, preprocessed):
  renderer = renderer_module.ClusterRenderer.__new__(renderer_module.ClusterRenderer)
  renderer.base_canvas = np.random.default_rng(19).integers(0, 256, (10, 16, 3), dtype=np.uint8)
  renderer._frame = renderer.base_canvas.copy()
  renderer.camera_x, renderer.camera_y, renderer.camera_w, renderer.camera_h = 4, 2, 8, 6
  renderer._update_filter_intervals = Mock()
  renderer._reset_leads = Mock()
  region = np.s_[2:8, 4:12]

  def draw_path(frame, _path, _hud):
    frame[2, 4] = (255, 0, 0)
    return frame

  def draw_hud(image, hud, _has_camera):
    image.putpixel((0, 0), (hud["value"], 100, 200))

  renderer._draw_model_path = draw_path
  renderer._draw_hud = draw_hud
  models = Mock()
  camera = Mock()
  camera.get_source_to_panel_transform.return_value = np.eye(3)
  retained = []
  for value, model_valid in ((10, True), (20, False), (None, False), (30, True)):
    # Full-size inputs exercise the compatibility crop path as well as the
    # usual preprocessed camera. Holding every result simulates a slow encoder.
    shape = (6, 8, 3) if preprocessed else (12, 24, 3)
    source = None if value is None else np.full(shape, value, dtype=np.uint8)
    camera.get_frame.return_value = source
    models.get_render_data.return_value = (model_valid, {"value": value or 0}, {})
    expected = renderer.base_canvas.copy()
    if source is not None:
      expected[region] = value
      if model_valid:
        expected[2, 4] = (255, 0, 0)
    expected[0, 0] = (value or 0, 100, 200)
    retained.append((renderer.render(camera, models), expected))
    if source is not None:
      assert np.all(source == value)

  for image, expected in retained:
    np.testing.assert_array_equal(np.asarray(image), expected)
  # Neither the path nor the HUD may leak into the cached panel background.
  np.testing.assert_array_equal(renderer.base_canvas, np.random.default_rng(19).integers(0, 256, (10, 16, 3), dtype=np.uint8))


def test_render_reports_path_cpu_separately_from_waits_and_hud(renderer_module, monkeypatch):
  renderer = renderer_module.ClusterRenderer.__new__(renderer_module.ClusterRenderer)
  renderer.base_canvas = np.zeros((10, 16, 3), dtype=np.uint8)
  renderer._frame = renderer.base_canvas.copy()
  renderer.camera_x, renderer.camera_y, renderer.camera_w, renderer.camera_h = 4, 2, 8, 6
  renderer._update_filter_intervals = Mock()
  clock = {"wall": 10.0, "cpu": 1.0}
  monkeypatch.setattr(renderer_module, "time", SimpleNamespace(monotonic=lambda: clock["wall"]))
  monkeypatch.setattr(renderer_module, "thread_time", lambda: clock["cpu"])
  def draw_path(frame, _path, _hud):
    clock["wall"] += 0.020
    clock["cpu"] += 0.003
    return frame
  def draw_hud(_image, _hud, _has_camera):
    clock["wall"] += 0.010
    clock["cpu"] += 0.005

  renderer._draw_model_path = draw_path
  renderer._draw_hud = draw_hud
  camera = Mock()
  camera.get_frame.return_value = np.ones((6, 8, 3), dtype=np.uint8)
  camera.get_source_to_panel_transform.return_value = np.eye(3)
  models = Mock()
  models.get_render_data.return_value = (True, {}, {})

  renderer.render(camera, models)

  assert renderer.last_frame_timings["path"] == pytest.approx(0.020)
  assert renderer.last_frame_timings["path_cpu"] == pytest.approx(0.003)
  assert renderer.last_frame_timings["hud"] == pytest.approx(0.010)


@pytest.mark.parametrize("x", [-30, 4, 80, 156, 195])
@pytest.mark.parametrize("opacity", [0.1, 0.5, 1.0])
@pytest.mark.parametrize("metric", [False, True])
def test_lead_labels_use_cluster_font_and_preserve_fading_at_camera_edges(renderer_module, monkeypatch, x, opacity, metric):
  renderer = renderer_module.ClusterRenderer.__new__(renderer_module.ClusterRenderer)
  renderer.config = SimpleNamespace(is_metric=metric)
  renderer.camera_x = renderer.camera_y = 0
  renderer.font_unit = ImageFont.truetype(str(Path(__file__).parents[3] / "assets/fonts/NotoSansKR-Bold.ttf"), 20)
  renderer._text_cache, renderer._text_cache_bytes = OrderedDict(), 0
  renderer._lead_vehicles = [SimpleNamespace(
    bar=np.array([[x - 10, 45], [x - 10, 55], [x + 10, 55], [x + 10, 45]]),
    fade_filter=SimpleNamespace(x=opacity),
  )]
  renderer._lead_info = [{"d_rel": 23, "v_rel": -2}]
  renderer._fill_polygon_alpha = Mock()
  monkeypatch.setattr(renderer_module, "Colors", SimpleNamespace(RED=(255, 0, 0), WHITE=(255, 255, 255), BLACK=(0, 0, 0)))
  camera = np.random.default_rng(42).integers(0, 256, (100, 160, 3), dtype=np.uint8)
  expected = camera.copy()
  speed_label = "29 km/h" if metric else "18 mph"
  for label, baseline_y in (("23 m", 32), (speed_label, 72)):
    left, top, right, bottom = renderer.font_unit.getbbox(label, anchor="ms", stroke_width=2)
    x0, y0 = max(0, x + left), max(0, baseline_y + top)
    x1, y1 = min(160, x + right), min(100, baseline_y + bottom)
    if x0 >= x1 or y0 >= y1:
      continue
    roi = expected[y0:y1, x0:x1]
    overlay = Image.fromarray(roi)
    ImageDraw.Draw(overlay).text((x - x0, baseline_y - y0), label, font=renderer.font_unit,
                                fill=(255, 255, 255), anchor="ms", stroke_width=2, stroke_fill=(0, 0, 0))
    cv2.addWeighted(np.asarray(overlay), opacity, roi, 1.0 - opacity, 0, dst=roi)
  labels = []
  original_text = ImageDraw.ImageDraw.text

  def draw_text(draw, position, label, **kwargs):
    assert kwargs["font"] is renderer.font_unit
    labels.append(label)
    return original_text(draw, position, label, **kwargs)

  monkeypatch.setattr(ImageDraw.ImageDraw, "text", draw_text)
  renderer._draw_lead_indicator(camera, {"v_ego": 10})
  np.testing.assert_array_equal(camera, expected)
  assert labels == ["23 m", "23 m", speed_label, speed_label]
  renderer._draw_lead_indicator(camera, {"v_ego": 10})
  assert labels == ["23 m", "23 m", speed_label, speed_label]


def _previous_centered(draw, cx, cy, value, font, color, stroke_width, stroke_fill):
  fill = tuple(color) if isinstance(color, (tuple, list)) else color
  if draw.mode == "RGBA" and isinstance(fill, tuple) and len(fill) == 4:
    fill = (*fill[:3], 255)
  stroke = tuple(stroke_fill) if isinstance(stroke_fill, (tuple, list)) else stroke_fill
  probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
  bbox = probe.textbbox((0, 0), str(value), font=font, anchor="mm", stroke_width=stroke_width)
  padding = max(2, int(stroke_width) + 1)
  size = (max(1, bbox[2] - bbox[0] + padding * 2), max(1, bbox[3] - bbox[1] + padding * 2))
  anchor_x, anchor_y = padding - bbox[0], padding - bbox[1]
  sprite = Image.new("RGBA", size)
  ImageDraw.Draw(sprite).text((anchor_x, anchor_y), str(value), font=font, fill=fill, anchor="mm",
                              stroke_width=stroke_width, stroke_fill=stroke)
  draw._image.paste(sprite, (int(cx) - anchor_x, int(cy) - anchor_y), sprite)


@pytest.fixture(params=["large_bold", "regular", "cjk", "default", "bitmap"])
def text_font(request):
  if request.param == "default":
    return ImageFont.load_default()
  if request.param == "bitmap":
    if not hasattr(ImageFont, "load_default_imagefont"):
      pytest.skip("Pillow has no standalone bitmap fallback font")
    return ImageFont.load_default_imagefont()
  name, size = {
    "large_bold": ("NotoSansKR-Bold.ttf", 92), "regular": ("NotoSansKR-Bold.ttf", 24),
    "cjk": ("NotoSansKR-Bold.ttf", 42),
  }[request.param]
  return ImageFont.truetype(str(Path(__file__).parents[3] / "assets" / "fonts" / name), size)


def _text_renderer(module):
  renderer = module.ClusterRenderer.__new__(module.ClusterRenderer)
  renderer._text_cache = OrderedDict()
  renderer._text_cache_bytes = 0
  return renderer


@pytest.mark.parametrize("mode,draw_mode", [("RGB", "RGB"), ("RGB", "RGBA"), ("RGBA", "RGBA")])
@pytest.mark.parametrize("fill,stroke,width", [
  ((100, 240, 135), None, 0), ((100, 240, 135), (0, 0, 0), 2),
  ((200, 100, 30, 60), (20, 200, 120, 170), 3),
  ((0, 0, 0, 70), (0, 0, 0, 70), 2), ("white", "#ffffff", 1),
  ([50, 70, 80, 105], [20, 70, 40, 10], 2),
])
@pytest.mark.parametrize("position", [(210.7, 95.4), (8.9, 1.3)])
def test_recolored_text_keeps_previous_pixels(renderer_module, text_font, mode, draw_mode, fill, stroke, width, position):
  renderer = _text_renderer(renderer_module)
  # Nonuniform backgrounds expose blending differences, including RGBA alpha.
  channels = 4 if mode == "RGBA" else 3
  pixels = np.random.default_rng(17).integers(0, 256, (190, 420, channels), dtype=np.uint8)
  texts = ("128", "jÁg", "--", "주행", "") if isinstance(text_font, ImageFont.FreeTypeFont) else ("128", "jÁg", "--", "")
  for text in texts:
    expected, actual = Image.fromarray(pixels), Image.fromarray(pixels)
    _previous_centered(ImageDraw.Draw(expected, draw_mode), *position, text, text_font, fill, width, stroke)
    renderer._centered(ImageDraw.Draw(actual, draw_mode), *position, text, text_font, fill, width, stroke)
    np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected))


def test_color_changes_reuse_glyph_masks(renderer_module, monkeypatch):
  renderer = _text_renderer(renderer_module)
  font_path = str(Path(__file__).parents[3] / "assets" / "fonts" / "NotoSansKR-Bold.ttf")
  font, reference_font = ImageFont.truetype(font_path, 92), ImageFont.truetype(font_path, 92)
  colors = [(255, value, value) for value in range(60, 256)]
  image = Image.new("RGB", (420, 190))
  expected = image.copy()
  draw = ImageDraw.Draw(image)
  renderer._centered(draw, 210, 95, 128, font, colors[0], 2, (0, 0, 0))
  _previous_centered(ImageDraw.Draw(expected), 210, 95, 128, reference_font, colors[0], 2, (0, 0, 0))
  glyph = next(iter(renderer._text_cache.values()))
  initial_masks = (glyph.fill_mask, glyph.stroke_mask, glyph.sprite)
  raster = Mock(side_effect=AssertionError("cached glyph must not be rasterized again"))
  monkeypatch.setattr(font, "getmask2", raster)
  for color in colors[1:]:
    renderer._centered(draw, 210, 95, 128, font, color, 2, (0, 0, 0))
    _previous_centered(ImageDraw.Draw(expected), 210, 95, 128, reference_font, color, 2, (0, 0, 0))
    np.testing.assert_array_equal(np.asarray(image), np.asarray(expected))
  assert len(renderer._text_cache) == 1
  assert (glyph.fill_mask, glyph.stroke_mask, glyph.sprite) == initial_masks
  raster.assert_not_called()


def test_text_cache_bounds_bytes_and_retains_recent_glyphs(renderer_module, monkeypatch):
  renderer = _text_renderer(renderer_module)
  monkeypatch.setattr(renderer_module, "TEXT_CACHE_MAX_BYTES", 2048)
  monkeypatch.setattr(renderer_module, "TEXT_CACHE_MAX_ENTRIES", 4)
  font = ImageFont.load_default()
  draw = ImageDraw.Draw(Image.new("RGB", (320, 160)))
  for value in range(30):
    renderer._centered(draw, 160, 80, value, font, (255, 255, 255))
    assert renderer._text_cache_bytes == sum(g.byte_size for g in renderer._text_cache.values())
    assert renderer._text_cache_bytes <= 2048
    assert len(renderer._text_cache) <= 4
  assert next(reversed(renderer._text_cache)) == (id(font), "29", 0)
  # An unusually wide string is drawn transiently without evicting everything.
  keys = tuple(renderer._text_cache)
  renderer._centered(draw, 160, 80, "W" * 300, font, (0, 255, 0))
  assert tuple(renderer._text_cache) == keys
