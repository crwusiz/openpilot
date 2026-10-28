"""CPU HUD rasterization and camera layers for the Pi's SDL GPU compositor."""

from dataclasses import dataclass
from collections import OrderedDict
from copy import copy, deepcopy
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import logging
import os
import time
from time import thread_time

import cv2
import numpy as np
from PIL import Image

try:
  from openpilot.selfdrive.addon.cluster.cluster_renderer import CLIP_MARGIN, GRADIENT_BANDS, ClusterRenderer, ColorBlendWorkspace, TextGlyph
  from openpilot.selfdrive.addon.cluster.cluster_render_support import Colors, colors_alpha
except ImportError:
  from cluster_renderer import CLIP_MARGIN, GRADIENT_BANDS, ClusterRenderer, ColorBlendWorkspace, TextGlyph
  from cluster_render_support import Colors, colors_alpha


try:
  from .path_worker import LatestPathWorker, PathRequest
except ImportError:
  from path_worker import LatestPathWorker, PathRequest


PATH_MAX_AGE_SECONDS = 0.5


@lru_cache(maxsize=128)
def _gradient_palette(y0, height, image_height, colors, stops):
  """Cache only the eight band positions and inks, never image-sized buffers."""
  band_height = max(1, (height + GRADIENT_BANDS - 1) // GRADIENT_BANDS)
  starts = np.arange(0, height, band_height)
  ends = np.minimum(starts + band_height, height)
  positions = 1.0 - (y0 + (starts + ends - 1) * 0.5) / max(image_height - 1, 1)
  color_array = np.asarray(colors, dtype=np.float32)
  inks = np.column_stack([np.interp(positions, stops, color_array[:, channel]) for channel in range(4)])
  # Use Python int's truncation just like the common renderer's scalar path.
  return tuple((int(start), int(end), tuple(map(int, ink)))
               for start, end, ink in zip(starts, ends, inks, strict=True))


@dataclass
class SceneLayers:
  overlay: Image.Image
  camera: np.ndarray | None
  camera_rect: tuple
  camera_uv: tuple
  rotate_180: bool = False
  path: np.ndarray | None = None

  def to_image(self):
    """Same retained layers on a software-only or failed GPU backend."""
    image = Image.new("RGBA", self.overlay.size, (0, 0, 0, 255))
    if self.camera is not None:
      x, y, width, height = self.camera_rect
      u0, v0, u1, v1 = self.camera_uv
      source_h, source_w = self.camera.shape[:2]
      scale_x, scale_y = (u1 - u0) * source_w / width, (v1 - v0) * source_h / height
      inverse = np.array([[scale_x, 0, u0 * source_w + scale_x * 0.5 - 0.5],
                          [0, scale_y, v0 * source_h + scale_y * 0.5 - 0.5]], dtype=np.float32)
      panel = cv2.warpAffine(self.camera, inverse, (width, height),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
      image.paste(Image.fromarray(panel), (x, y))
    if self.path is not None:
      height, width = self.path.shape[:2]
      path = Image.frombytes("RGBa", (width, height), self.path.tobytes()).convert("RGBA")
      image.alpha_composite(path, self.camera_rect[:2])
    image = Image.alpha_composite(image, self.overlay).convert("RGB")
    return image.transpose(Image.Transpose.ROTATE_180) if self.rotate_180 else image


class LayeredRenderer(ClusterRenderer):
  """Keep the common layout/projection; rasterize paths without camera pixels."""

  def __init__(self, config):
    super().__init__(config)
    self._straight_alpha_hud = True
    # Only the path worker uses these glyphs/font. A separate FreeType face is
    # important: a separate dictionary alone still races on the shared font.
    self._lead_font = None
    self._lead_text_cache = OrderedDict()
    self._lead_text_bytes = 0
    self._lead_elapsed = 0.0
    self._path_executor = None
    self._path_frame = None
    self._path_worker = self._path_painter = None
    self._path_context = (False, None, None, time.monotonic)
    self._path_signature = None
    self._path_epoch = 0
    self._sync_path = None
    self._async_path_failed = False
    try:
      cores = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
      cores = os.cpu_count() or 1
    self._parallel_path = cores >= 4 and os.environ.get("CLUSTER_PARALLEL_PATH", "1") != "0"
    logging.getLogger("cluster_receiver").info(
      "[CLUSTER_SCENE_WORKERS] camera_workers=1 | path_workers=%d | hud=owner | gpu=owner | queue=bounded | "
      + "text_cache=isolated | lane_projection=batched",
      int(self._parallel_path))

  def close(self):
    self._stop_path_pipeline()
    executor, self._path_executor = self._path_executor, None
    if executor is not None:
      executor.shutdown(wait=True, cancel_futures=True)
    self._path_frame = None
    self._lead_text_cache.clear()
    self._lead_text_bytes = 0
    self._lead_font = None
    self._sync_path = None

  def configure_path_pipeline(self, asynchronous, received_at, valid_until, clock=time.monotonic):
    enabled = bool(asynchronous) and self._parallel_path and not self._async_path_failed
    if not enabled and self._path_worker is not None:
      self._stop_path_pipeline()
    self._path_context = enabled, received_at, valid_until, clock

  def _stop_path_pipeline(self):
    worker, self._path_worker = self._path_worker, None
    if worker is not None:
      worker.close()
    painter, self._path_painter = self._path_painter, None
    if painter is not None:
      self._lead_vehicles, self._lead_info = painter._lead_vehicles, painter._lead_info
      self._last_render_at = painter._last_render_at
      painter.close()

  def _start_path_pipeline(self):
    # Share immutable assets/layout only. Mutable path buffers, fonts, caches,
    # configuration, transform and lead filters belong to one CPU worker.
    painter = copy(self)
    painter.config = copy(self.config)
    painter._path_worker = painter._path_painter = painter._path_executor = None
    painter._path_frame = None
    painter._color_blender = ColorBlendWorkspace(self.camera_w)
    painter._source_to_panel = self._source_to_panel.copy()
    painter._lead_vehicles, painter._lead_info = deepcopy((self._lead_vehicles, self._lead_info))
    painter._lead_font = None
    painter._lead_text_cache = OrderedDict()
    painter._lead_text_bytes = 0
    painter._sync_path = None
    painter._painter_epoch = self._path_epoch
    self._path_painter = painter
    self._path_worker = LatestPathWorker(painter._paint_path_request)
    if self._sync_path is not None:
      self._path_worker.seed(*self._sync_path)
      self._sync_path = None
    # The previous synchronous frame has already joined this idle executor.
    if self._path_executor is not None:
      self._path_executor.shutdown(wait=True, cancel_futures=True)
      self._path_executor = None
    logging.getLogger("cluster_receiver").info(
      "[CLUSTER_PATH_PIPELINE] mode=latest_completed | max_age_ms=%.0f | expiry=source_deadline | "
      + "style=retain_until_ready | workers=1 | queued_frames=0 | sdl=owner", PATH_MAX_AGE_SECONDS * 1000)

  def _paint_path_request(self, request):
    if request.epoch != self._painter_epoch:
      self._reset_leads()
      self._last_render_at = None
      self._painter_epoch = request.epoch
    self.config.is_metric = request.is_metric
    self.config.speed_unit = "km/h" if request.is_metric else "mph"
    self._source_to_panel = request.transform.copy()
    self._update_filter_intervals(time.monotonic())
    pixels, elapsed, cpu_elapsed = self._render_path_layer(request.path, request.hud)
    return pixels, {"path": elapsed, "path_cpu": cpu_elapsed, "path_labels": self._lead_elapsed}

  def _lead_glyph(self, label):
    glyph = self._lead_text_cache.get(label)
    if glyph is None:
      if self._lead_font is None:
        self._lead_font = self.font_unit.font_variant()
      glyph = TextGlyph(label, self._lead_font, 2, anchor="ms")
      if glyph.byte_size <= 2 * 1024 * 1024:
        self._lead_text_cache[label] = glyph
        self._lead_text_bytes += glyph.byte_size
        while len(self._lead_text_cache) > 64 or self._lead_text_bytes > 2 * 1024 * 1024:
          _, old = self._lead_text_cache.popitem(last=False)
          self._lead_text_bytes -= old.byte_size
    else:
      self._lead_text_cache.move_to_end(label)
    return glyph

  def _render_path_layer(self, path, hud):
    started, cpu_started = time.monotonic(), thread_time()
    self._lead_elapsed = 0.0
    if not path.get("path_x") or path.get("calib_transform") is None:
      self._reset_leads()
      return None, time.monotonic() - started, thread_time() - cpu_started
    if self._path_frame is None:
      self._path_frame = np.zeros((*self.base_canvas.shape[:2], 4), dtype=np.uint8)
      self._path_blender = ColorBlendWorkspace(self.camera_w, channels=4)
    region = self._path_frame[self.camera_y:self.camera_y + self.camera_h, self.camera_x:self.camera_x + self.camera_w]
    region[:] = 0
    rgb_blender, self._color_blender = self._color_blender, self._path_blender
    try:
      self._draw_model_path(self._path_frame, path, hud)
    finally:
      self._color_blender = rgb_blender
    # The worker reuses its canvas next frame. Retained GPU/software fallback
    # layers must own their pixels until presentation has finished with them.
    pixels = region.copy() if cv2.countNonZero(cv2.extractChannel(region, 3)) else None
    return pixels, time.monotonic() - started, thread_time() - cpu_started

  def _draw_lane_lines(self, camera_region, path_data, calib_transform):
    # Project all lane/edge boundaries together instead of entering NumPy's
    # small matrix/validation operations twice per line. Keep their original
    # order, clipping, colors and integer raster coordinates.
    points, draws = [], []
    groups = ((path_data.get("lane_lines") or [], path_data.get("lane_line_probs") or [], False),
              (path_data.get("road_edges") or [], path_data.get("road_edge_stds") or [], True))
    for lines, confidence, edge in groups:
      for index, line in enumerate(lines):
        if len(line) != 3:
          continue
        alpha = (1.0 - float(confidence[index]) if edge else float(confidence[index])) if index < len(confidence) else 0.0
        count = min(map(len, line))
        if alpha <= 0.0 or count < 2:
          continue
        raw = np.column_stack([values[:count] for values in line]).astype(np.float32)
        raw = raw[(raw[:, 0] >= 0.0) & (raw[:, 0] <= 100.0)]
        if len(raw) < 2:
          continue
        width = 0.025 if edge else 0.025 * alpha
        points.extend((raw + np.array([0.0, -width, 0.0], dtype=np.float32),
                       raw + np.array([0.0, width, 0.0], dtype=np.float32)))
        draws.append((len(raw), colors_alpha(Colors.RED, 100) if edge else Colors.WHITE,
                      float(np.clip(alpha, 0.0, 1.0 if edge else 0.7))))
    if not points:
      return
    pixels, visible = self._project_points(np.concatenate(points), calib_transform, CLIP_MARGIN)
    offset = np.array([self.camera_x, self.camera_y], dtype=np.int32)
    start = 0
    for count, color, alpha in draws:
      middle, end = start + count, start + 2 * count
      valid = visible[start:middle] & visible[middle:end]
      if np.count_nonzero(valid) >= 2:
        polygon = np.vstack((pixels[start:middle][valid], pixels[middle:end][valid][::-1])).astype(np.int32)
        self._fill_polygon_alpha(camera_region, polygon - offset, color, alpha, blender=self._color_blender)
      start = end

  def _poll_path(self, request, now):
    try:
      result = self._path_worker.poll(request, now)
      return result, self._path_worker.last_reason
    except Exception:
      logging.getLogger("cluster_receiver").exception("[CLUSTER_PATH_PIPELINE] worker failed; fallback=synchronous")
      self._stop_path_pipeline()
      self._async_path_failed = True
      return None, "failed"

  def render_overlay(self, transform, models, has_camera):
    started = time.monotonic()
    self._update_filter_intervals(started)
    self._source_to_panel = transform.copy()
    self._source_to_panel[0, 2] += self.camera_x
    self._source_to_panel[1, 2] += self.camera_y
    if not hasattr(self, "_overlay_base"):
      self._overlay_base = np.empty((*self.base_canvas.shape[:2], 4), dtype=np.uint8)
      self._overlay_base[:, :, :3] = self.base_canvas
      self._overlay_base[:, :, 3] = 255
      self._overlay_camera_base = self._overlay_base.copy()
      self._overlay_camera_base[self.camera_y:self.camera_y + self.camera_h, self.camera_x:self.camera_x + self.camera_w] = 0
    frame = (self._overlay_camera_base if has_camera else self._overlay_base).copy()
    copied_at = time.monotonic()
    model_valid, hud, path = models.get_render_data()
    snapshot_at = time.monotonic()
    path_pixels = None
    path_future = None
    path_elapsed = path_cpu = 0.0
    path_age = None
    lead_elapsed = 0.0
    asynchronous, received_at, valid_until, clock = self._path_context
    if not asynchronous and self._path_worker is not None:
      self._stop_path_pipeline()
    road_visible = self._get_border_color(hud) != Colors.Border.DISENGAGED
    # Brake/blinker/steering colors do not change lane geometry. Keep the
    # compatible layer until the next style is ready instead of blanking all
    # lanes on every transient input change. Real visibility changes still
    # invalidate immediately, as do units, projection and input freshness.
    key = tuple(self._source_to_panel.ravel()), bool(self.config.is_metric), road_visible
    eligible = bool(has_camera and model_valid)
    signature = eligible, key
    invalidated = signature != self._path_signature
    if invalidated:
      self._path_epoch += 1
      self._path_signature = signature
      self._sync_path = None
      if self._path_worker is not None:
        self._path_worker.invalidate()
    received_at = clock() if received_at is None else received_at
    deadline = received_at + PATH_MAX_AGE_SECONDS if valid_until is None else min(valid_until, received_at + PATH_MAX_AGE_SECONDS)
    request = PathRequest(path, hud, self._source_to_panel.copy(), bool(self.config.is_metric), key,
                          self._path_epoch, deadline, received_at)
    path_reason = "invalid"
    early_reason = None
    result = new_result = None
    if asynchronous and eligible:
      if self._path_worker is None:
        self._start_path_pipeline()
      # Start the latest path before HUD rasterization. The independent CPU
      # painter can overlap this frame instead of losing the entire HUD time.
      result, path_reason = self._poll_path(request, clock())
      early_reason = path_reason
      if path_reason == "ready":
        new_result = result
      path_elapsed = path_cpu = lead_elapsed = None
    elif eligible:
      if self._parallel_path and path.get("path_x") and path.get("calib_transform") is not None:
        if self._path_executor is None:
          self._path_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cluster-path-layer")
        path_future = self._path_executor.submit(self._render_path_layer, path, hud)
      else:
        path_pixels, path_elapsed, path_cpu = self._render_path_layer(path, hud)
    else:
      self._reset_leads()
      self._lead_elapsed = 0.0
    hud_started = time.monotonic()
    hud_cpu_started = thread_time()
    try:
      image = Image.fromarray(frame)
      self._draw_hud(image, hud, has_camera)
    finally:
      hud_elapsed = time.monotonic() - hud_started
      hud_cpu = thread_time() - hud_cpu_started
      wait_started = time.monotonic()
      # Even a HUD exception must join the path worker before the next frame
      # or session cleanup can change filters, fonts, configuration or buffers.
      if path_future is not None:
        path_pixels, path_elapsed, path_cpu = path_future.result()
      path_wait = time.monotonic() - wait_started if path_future is not None else 0.0
    if asynchronous and eligible:
      # Collect after HUD work so a path that completed during it is usable
      # immediately. The duplicate-input guard prevents this second poll from
      # scheduling the same immutable snapshot again. Neither poll joins.
      if self._path_worker is not None:
        result, path_reason = self._poll_path(request, clock())
        if path_reason == "ready":
          new_result = result
        elif path_reason == "pending" and early_reason in ("expired", "incompatible"):
          # The second poll must not erase why this frame lost its retained
          # path merely because the replacement is now waiting to finish.
          path_reason = early_reason
      if result is not None:
        path_pixels, timings = result.value
        path_age = max(0.0, clock() - result.request.received_at)
        # An early completion becomes "retained" at the second poll. Count
        # its work once in this frame by identity, not only the final reason.
        if result is new_result:
          path_elapsed, path_cpu, lead_elapsed = (timings[name] for name in ("path", "path_cpu", "path_labels"))
    elif not asynchronous:
      lead_elapsed = self._lead_elapsed
      if eligible:
        self._sync_path = request, (path_pixels, {"path": path_elapsed, "path_cpu": path_cpu, "path_labels": lead_elapsed})
    self.last_frame_timings = {"camera_copy": copied_at - started, "snapshot": snapshot_at - copied_at,
                               "path": path_elapsed, "path_cpu": path_cpu, "hud": hud_elapsed, "hud_cpu": hud_cpu,
                               "path_wait": path_wait, "path_labels": lead_elapsed, "path_age": path_age,
                               "path_async": int(asynchronous), "path_visible": int(path_pixels is not None),
                               "path_ready": int(path_reason in ("ready", "retained")),
                               "path_expected": int(eligible and road_visible and bool(path.get("path_x"))),
                               "path_pending": int(path_reason == "pending"), "path_expired": int(path_reason == "expired"),
                               "path_incompatible": int(path_reason == "incompatible"),
                               "path_invalid": int(not eligible), "path_reset": int(invalidated)}
    return image, path_pixels

  @staticmethod
  def _fill_polygon_alpha(image, polygon, color, alpha, blender=None):
    if image.shape[2] == 4 and (blender is None or blender._shape[2] != 4):
      blender = ColorBlendWorkspace(image.shape[1], channels=4)
    return ClusterRenderer._fill_polygon_alpha(image, polygon, color, alpha, blender)

  @staticmethod
  def _fill_polygon_gradient(image, polygon, colors, stops, blender=None):
    if image.shape[2] != 4:
      return ClusterRenderer._fill_polygon_gradient(image, polygon, colors, stops, blender)
    if blender is None or blender._shape[2] != 4:
      blender = ColorBlendWorkspace(image.shape[1], channels=4)
    x, y, width, height = cv2.boundingRect(polygon)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + width, image.shape[1]), min(y + height, image.shape[0])
    if x0 >= x1 or y0 >= y1:
      return
    roi = image[y0:y1, x0:x1]
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [polygon - np.array([x0, y0], dtype=np.int32)], 255)
    palette = _gradient_palette(y0, roi.shape[0], image.shape[0], tuple(map(tuple, colors)), tuple(stops))
    for band_y0, band_y1, color in palette:
      band_mask = mask[band_y0:band_y1]
      band_x, mask_y, band_w, band_h = cv2.boundingRect(band_mask)
      if band_w == 0 or band_h == 0:
        continue
      alpha = color[3] / 255.0
      if alpha <= 0.0:
        continue
      band = roi[band_y0 + mask_y:band_y0 + mask_y + band_h, band_x:band_x + band_w]
      band_mask = band_mask[mask_y:mask_y + band_h, band_x:band_x + band_w]
      cv2.copyTo(blender.blend(band, color[:3], alpha), band_mask, band)

  def _draw_lead_indicator(self, camera_region, hud_data):
    if camera_region.shape[2] != 4:
      return super()._draw_lead_indicator(camera_region, hud_data)
    started = time.monotonic()
    offset = np.array([self.camera_x, self.camera_y], dtype=np.float32)
    for lead, info in zip(self._lead_vehicles, self._lead_info, strict=True):
      opacity = lead.fade_filter.x
      if lead.bar.size == 0 or int(255 * opacity) == 0:
        continue
      self._fill_polygon_alpha(camera_region, (lead.bar - offset).astype(np.int32), Colors.RED, opacity, self._color_blender)
      x, y = map(int, lead.bar[[0, 3]].mean(axis=0) - offset)
      speed = max(0.0, float(hud_data.get("v_ego", 0) or 0) + float(info.get("v_rel", 0) or 0))
      metric = getattr(self.config, "is_metric", True)
      labels = (f"{float(info.get('d_rel', 0)):.0f} m", f"{speed * (3.6 if metric else 2.236936):.0f} {'km/h' if metric else 'mph'}")
      for label, baseline in zip(labels, (y - 13, y + 27), strict=True):
        glyph = self._lead_glyph(label)
        sprite = glyph.colored(Colors.WHITE, Colors.BLACK).copy()
        sprite.putalpha(sprite.getchannel("A").point(lambda value, opacity=opacity: round(value * opacity)))
        left, top = x - glyph.anchor_x, baseline - glyph.anchor_y
        x0, y0 = max(0, left), max(0, top)
        x1, y1 = min(camera_region.shape[1], left + sprite.width), min(camera_region.shape[0], top + sprite.height)
        if x1 > x0 and y1 > y0:
          roi = camera_region[y0:y1, x0:x1]
          sprite = sprite.crop((x0 - left, y0 - top, x1 - left, y1 - top))
          mask = sprite.getchannel("A")
          sprite.putalpha(255)
          image = Image.fromarray(roi)
          image.paste(sprite, (0, 0), mask)
          roi[:] = np.asarray(image)
    self._lead_elapsed = time.monotonic() - started
