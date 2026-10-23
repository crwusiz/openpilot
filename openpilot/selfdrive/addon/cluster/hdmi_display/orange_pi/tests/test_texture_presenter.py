import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.texture_presenter import TexturePresenter


def _fake_video(renderer_error=None):
  calls = []

  class Window:
    def __init__(self, title, **kwargs):
      calls.append(("window", title, kwargs))
      self.size = kwargs["size"]

    def destroy(self):
      calls.append(("destroy",))

  class Renderer:
    def __init__(self, window, **kwargs):
      calls.append(("renderer", kwargs))
      if renderer_error:
        raise renderer_error

    def clear(self):
      calls.append(("clear",))

    def present(self):
      calls.append(("present",))

  class Texture:
    def __init__(self, renderer, size, **kwargs):
      calls.append(("texture", size, kwargs))
      self.color = (255, 255, 255)

    def update(self, surface):
      calls.append(("update", surface))

    def draw(self, **kwargs):
      calls.append(("draw", self.color, kwargs))

    @classmethod
    def from_surface(cls, renderer, surface):
      calls.append(("from_surface", surface))
      return cls(renderer, surface.get_size())

  return SimpleNamespace(Window=Window, Renderer=Renderer, Texture=Texture, WINDOWPOS_CENTERED=0x2FFF0000), calls


def _frame(size=(1920, 480)):
  return SimpleNamespace(get_size=lambda: size)


def _pygame():
  return SimpleNamespace(Rect=lambda *args: args,
                         Surface=Mock(side_effect=lambda size, **kwargs: SimpleNamespace(blit=Mock(), set_alpha=Mock())))


def _controls(brightness=80, screen_off=False, menu=None):
  value = 0 if screen_off else round(brightness * 255 / 100)
  return SimpleNamespace(screen_off=screen_off, tint_color=(value,) * 3, logical_size=(1920, 480), render_menu=lambda: menu)


def test_standalone_window_selects_display_and_accelerated_renderer():
  video, calls = _fake_video()
  pygame = _pygame()
  presenter = TexturePresenter(pygame, size=(480, 1920), display_index=2, rotation=90, video_module=video)
  assert calls[:2] == [
    ("window", "C4 Cluster", {"size": (480, 1920), "position": (0x2FFF0002, 0x2FFF0002), "fullscreen": True, "hidden": False}),
    ("renderer", {"accelerated": 1, "vsync": True}),
  ]
  assert presenter.get_size() == (480, 1920)
  presenter.close()
  presenter.close()
  assert calls.count(("destroy",)) == 1


def test_failed_gpu_initialization_releases_its_window_for_surface_fallback():
  video, calls = _fake_video(RuntimeError("GPU unavailable"))
  with pytest.raises(RuntimeError, match="GPU unavailable"):
    TexturePresenter(SimpleNamespace(), video_module=video)
  assert calls[-1] == ("destroy",)


def test_live_frames_reuse_one_streaming_texture_and_touch_redraw_does_not_upload():
  video, calls = _fake_video()
  pygame = _pygame()
  presenter = TexturePresenter(pygame, video_module=video)
  frame = _frame()
  presenter.present(frame, _controls())
  presenter.present(frame, _controls(brightness=35))
  presenter.redraw(_controls(brightness=20))
  assert len([call for call in calls if call[0] == "texture"]) == 1
  assert len([call for call in calls if call[0] == "update"]) == 2
  assert [call[1] for call in calls if call[0] == "draw"] == [(204,) * 3, (89,) * 3, (51,) * 3]
  assert next(call for call in calls if call[0] == "texture") == ("texture", (1920, 480), {"depth": 32, "streaming": True})
  assert pygame.Surface.call_count == 1
  staging = presenter._upload_surface
  assert [call[1] for call in calls if call[0] == "update"] == [staging, staging]
  assert staging.blit.call_count == 2
  presenter.present(_frame((960, 240)))
  assert len([call for call in calls if call[0] == "texture"]) == 2
  assert pygame.Surface.call_count == 2
  presenter.close()


@pytest.mark.parametrize("rotation, size, expected", [
  (0, (1920, 480), (0, 0, 1920, 480)),
  (90, (480, 1920), (-720, 720, 1920, 480)),
  (180, (1920, 480), (0, 0, 1920, 480)),
  (270, (480, 1920), (-720, 720, 1920, 480)),
])
def test_rotation_and_scaling_are_delegated_to_sdl(rotation, size, expected):
  video, calls = _fake_video()
  presenter = TexturePresenter(_pygame(), size=size, rotation=rotation, video_module=video)
  timings = presenter.present(_frame())
  draw = next(call for call in calls if call[0] == "draw")
  assert draw[2] == {"dstrect": expected, "angle": rotation}
  assert timings["rotate"] == timings["scale"] == 0
  assert timings["upload"] >= 0
  presenter.close()


def test_screen_off_skips_frame_upload_and_clears_renderer():
  video, calls = _fake_video()
  presenter = TexturePresenter(_pygame(), video_module=video)
  presenter.present(_frame())
  calls.clear()
  presenter.present(_frame(), _controls(screen_off=True))
  assert calls == [("clear",), ("present",)]
  presenter.redraw(_controls())
  assert any(call[0] == "draw" for call in calls)
  presenter.clear()
  calls.clear()
  presenter.redraw(_controls())
  assert calls == [("clear",), ("present",)]
  presenter.close()


def test_cached_small_menu_uploads_only_when_surface_changes():
  video, calls = _fake_video()
  presenter = TexturePresenter(_pygame(), size=(480, 1920), rotation=90, video_module=video)
  surface = _frame((400, 120))
  controls = _controls(menu=(surface, (20, 30)))
  presenter.present(_frame(), controls)
  presenter.redraw(controls)
  assert len([call for call in calls if call[0] == "from_surface"]) == 1
  menu_draws = [call for call in calls if call[0] == "draw" and call[1] == (255, 255, 255)]
  assert len(menu_draws) == 2
  assert menu_draws[0][2] == {"dstrect": (190, 160, 400, 120), "angle": 90}
  presenter.redraw(_controls(menu=(_frame((400, 120)), (20, 30))))
  assert len([call for call in calls if call[0] == "from_surface"]) == 2
  presenter.close()


def test_rendering_from_a_different_thread_fails_before_any_sdl_call():
  video, calls = _fake_video()
  presenter = TexturePresenter(_pygame(), video_module=video)
  calls.clear()
  errors = []

  def draw():
    try:
      presenter.present(_frame())
    except RuntimeError as error:
      errors.append(str(error))

  thread = threading.Thread(target=draw)
  thread.start()
  thread.join(timeout=1)
  assert errors == ["SDL texture presentation must stay on its owner thread"]
  assert not calls
  presenter.close()


@pytest.fixture
def raster_pygame(monkeypatch):
  monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", "1")
  monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
  pygame = pytest.importorskip("pygame")
  video = pytest.importorskip("pygame._sdl2.video")
  pygame.display.quit()
  pygame.display.init()
  yield pygame, video
  pygame.display.quit()


@pytest.fixture
def accelerated_pygame(monkeypatch):
  monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", "1")
  # KMSDRM setup tests exercise environment defaults with a fake pygame.
  # Do not inherit their Pi-specific renderer selection on this native host.
  for name in ("SDL_VIDEODRIVER", "SDL_RENDER_DRIVER", "SDL_RENDER_SCALE_QUALITY"):
    monkeypatch.delenv(name, raising=False)
  pygame = pytest.importorskip("pygame")
  video = pytest.importorskip("pygame._sdl2.video")
  pygame.display.quit()
  try:
    pygame.display.init()
  except pygame.error as error:
    pytest.skip(f"No native SDL video driver for accelerated raster check: {error}")
  yield pygame, video
  pygame.display.quit()


@pytest.mark.parametrize("depth", [24, 32])
@pytest.mark.parametrize("rotation, expected_rows", [
  (0, [[0, 1, 2, 3], [4, 5, 6, 7]]),
  (90, [[4, 0], [5, 1], [6, 2], [7, 3]]),
  (180, [[7, 6, 5, 4], [3, 2, 1, 0]]),
  (270, [[3, 7], [2, 6], [1, 5], [0, 4]]),
])
def test_sdl_pixel_rotation_matches_touch_coordinate_system(raster_pygame, rotation, expected_rows, depth):
  pygame, video = raster_pygame
  colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (255, 0, 255), (0, 255, 255), (255, 255, 255), (64, 64, 64)]
  frame = pygame.Surface((4, 2), depth=depth)
  for index, color in enumerate(colors):
    frame.set_at((index % 4, index // 4), color)
  size = len(expected_rows[0]), len(expected_rows)
  presenter = TexturePresenter(pygame, size=size, rotation=rotation, fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  try:
    presenter.present(frame)
    output = presenter.renderer.to_surface()
    actual = [[tuple(output.get_at((x, y)))[:3] for x in range(size[0])] for y in range(size[1])]
    assert actual == [[colors[index] for index in row] for row in expected_rows]
  finally:
    presenter.close()


def test_rgb_frames_reuse_argb_buffer_without_stale_pixels_or_source_mutation(raster_pygame):
  from PIL import Image
  pygame, video = raster_pygame
  presenter = TexturePresenter(pygame, size=(8, 4), fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  staging = None
  try:
    for color in ((210, 60, 15), (30, 90, 220), (0, 0, 0)):
      image = Image.new("RGB", (8, 4), color)
      frame = pygame.image.frombuffer(image.tobytes(), image.size, image.mode)
      assert frame.get_bitsize() == 24
      before = pygame.image.tostring(frame, "RGB")
      presenter.present(frame)
      current = presenter._upload_surface
      assert current.get_masks() == (0xFF0000, 0xFF00, 0xFF, 0xFF000000)
      assert current.get_alpha() is None
      if staging is None:
        staging = current
      assert current is staging
      output = presenter.renderer.to_surface()
      assert pygame.image.tostring(output, "RGB") == before
      assert pygame.image.tostring(frame, "RGB") == before
    presenter.redraw()
    assert presenter._upload_surface is staging
  finally:
    presenter.close()
  assert presenter._upload_surface is None


@pytest.mark.parametrize("rotation, expected", [(0, (0, 0)), (90, (1, 0)), (180, (3, 1)), (270, (0, 3))])
def test_menu_corner_rotates_with_frame_and_texture_brightness_is_applied(raster_pygame, rotation, expected):
  pygame, video = raster_pygame
  frame = pygame.Surface((4, 2))
  frame.fill((255, 0, 0))
  menu = pygame.Surface((1, 1))
  menu.fill((0, 255, 0))
  controls = _controls(menu=(menu, (0, 0)))
  controls.logical_size = (4, 2)
  size = (2, 4) if rotation in (90, 270) else (4, 2)
  presenter = TexturePresenter(pygame, size=size, rotation=rotation, fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  try:
    presenter.present(frame, controls)
    output = presenter.renderer.to_surface()
    assert tuple(output.get_at(expected))[:3] == (0, 255, 0)
    actual = [tuple(output.get_at((x, y)))[:3] for x in range(size[0]) for y in range(size[1])]
    assert actual.count((204, 0, 0)) == 7
  finally:
    presenter.close()


@pytest.mark.parametrize("rotation, expected_rows", [
  (0, [[0, 1, 2, 3], [4, 5, 6, 7]]),
  (90, [[4, 0], [5, 1], [6, 2], [7, 3]]),
  (180, [[7, 6, 5, 4], [3, 2, 1, 0]]),
  (270, [[3, 7], [2, 6], [1, 5], [0, 4]]),
])
@pytest.mark.parametrize("scene_rotated", [False, True])
def test_gpu_camera_layers_preserve_pixel_rotation_and_transparent_hud(raster_pygame, rotation, expected_rows, scene_rotated):
  pygame, video = raster_pygame
  colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (255, 0, 255), (0, 255, 255), (255, 255, 255), (64, 64, 64)]
  # Large solid tiles avoid SDL's software triangle rasterizer's integer UV
  # rounding at tiny 1x1 textures; tile centers exercise the full orientation.
  tile = 16
  camera = pygame.Surface((4 * tile, 2 * tile), depth=24)
  for index, color in enumerate(colors):
    pygame.draw.rect(camera, color, ((index % 4) * tile, (index // 4) * tile, tile, tile))
  overlay = pygame.Surface(camera.get_size(), pygame.SRCALPHA, 32)
  overlay.fill((0, 0, 0, 0))
  pygame.draw.rect(overlay, (20, 30, 40, 255), (0, 0, tile, tile))
  colors[0] = (20, 30, 40)
  size = len(expected_rows[0]) * tile, len(expected_rows) * tile
  presenter = TexturePresenter(pygame, size=size, rotation=rotation, fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  try:
    assert presenter.can_present_layers
    presenter.present_layers(overlay, camera, (0, 0, *camera.get_size()), rotate_180=scene_rotated)
    output = presenter.renderer.to_surface()
    expected = [[colors[index] for index in row] for row in expected_rows]
    if scene_rotated:
      expected = [list(reversed(row)) for row in reversed(expected)]
    actual = [[tuple(output.get_at((x * tile + tile // 2, y * tile + tile // 2)))[:3]
               for x in range(len(expected_rows[0]))] for y in range(len(expected_rows))]
    assert actual == expected
  finally:
    presenter.close()


def test_gpu_camera_fractional_uv_crop_and_alpha_blend(raster_pygame):
  pygame, video = raster_pygame
  camera = pygame.Surface((8, 4), depth=24)
  camera.fill((0, 0, 255))
  pygame.draw.rect(camera, (255, 0, 0), (2, 1, 4, 2))
  overlay = pygame.Surface((6, 4), pygame.SRCALPHA, 32)
  overlay.fill((0, 0, 0, 255))
  pygame.draw.rect(overlay, (0, 0, 0, 0), (1, 1, 4, 2))
  overlay.set_at((1, 1), (0, 255, 0, 128))
  presenter = TexturePresenter(pygame, size=(6, 4), fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  try:
    presenter.present_layers(overlay, camera, (1, 1, 4, 2), (0.25, 0.25, 0.75, 0.75))
    output = presenter.renderer.to_surface()
    assert tuple(output.get_at((4, 2)))[:3] == (255, 0, 0)
    mixed = tuple(output.get_at((1, 1)))[:3]
    assert 126 <= mixed[0] <= 127 and 127 <= mixed[1] <= 128 and mixed[2] == 0
    assert tuple(output.get_at((0, 0)))[:3] == (0, 0, 0)
    native_textures = dict(presenter._layers.textures)
    presenter.redraw(_controls(brightness=50))
    output = presenter.renderer.to_surface()
    assert tuple(output.get_at((4, 2)))[:3] == (128, 0, 0)
    assert presenter._layers.textures == native_textures
    presenter.present_layers(overlay, camera, (1, 1, 4, 2), (0.25, 0.25, 0.75, 0.75))
    assert presenter._layers.textures == native_textures
    presenter.disable_layers()
    assert not presenter.can_present_layers
    # A missing/failing optional layer path retains the ordinary texture path.
    presenter.present(camera)
    assert presenter._texture is not None
  finally:
    presenter.close()


def test_gpu_layers_keep_touch_menu_upright_when_scene_rotates(raster_pygame):
  pygame, video = raster_pygame
  camera = pygame.Surface((4, 2), depth=24)
  camera.fill((255, 0, 0))
  overlay = pygame.Surface((4, 2), pygame.SRCALPHA, 32)
  overlay.fill((0, 0, 0, 0))
  menu = pygame.Surface((1, 1))
  menu.fill((0, 255, 0))
  controls = _controls(menu=(menu, (0, 0)))
  controls.logical_size = (4, 2)
  presenter = TexturePresenter(pygame, size=(4, 2), fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  try:
    presenter.present_layers(overlay, camera, (0, 0, 4, 2), controls=controls, rotate_180=True)
    output = presenter.renderer.to_surface()
    assert tuple(output.get_at((0, 0)))[:3] == (0, 255, 0)
    assert tuple(output.get_at((3, 1)))[:3] == (204, 0, 0)
    presenter.redraw(_controls(screen_off=True))
    assert all(tuple(presenter.renderer.to_surface().get_at((x, y)))[:3] == (0, 0, 0)
               for x in range(4) for y in range(2))
    presenter.redraw(_controls())
    assert tuple(presenter.renderer.to_surface().get_at((0, 0)))[:3] == (204, 0, 0)
  finally:
    presenter.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_accelerated_fractional_crop_matches_pixel_center_reference(accelerated_pygame, rotation):
  import cv2
  import numpy as np
  pygame, video = accelerated_pygame
  size = (6, 8) if rotation in (90, 270) else (8, 6)
  try:
    presenter = TexturePresenter(pygame, size=size, rotation=rotation, fullscreen=False,
                                 accelerated=True, vsync=False, hidden=True, video_module=video)
  except pygame.error as error:
    pytest.skip(f"No accelerated SDL renderer for raster check: {error}")
  try:
    values = (np.arange(16)[None, :] * 10 + np.arange(12)[:, None] * 3).astype(np.uint8)
    pixels = np.repeat(values[:, :, None], 3, axis=2)
    camera = pygame.image.frombuffer(pixels, (16, 12), "RGB")
    overlay = pygame.Surface((8, 6), pygame.SRCALPHA, 32)
    overlay.fill((0, 0, 0, 0))
    uv = (0.137, 0.192, 0.803, 0.877)
    presenter.present_layers(overlay, camera, (0, 0, 8, 6), uv)
    output = pygame.surfarray.array3d(presenter.renderer.to_surface()).transpose(1, 0, 2)
    scale_x, scale_y = (uv[2] - uv[0]) * 16 / 8, (uv[3] - uv[1]) * 12 / 6
    inverse = np.array([[scale_x, 0, uv[0] * 16 + scale_x * 0.5 - 0.5],
                        [0, scale_y, uv[1] * 12 + scale_y * 0.5 - 0.5]], dtype=np.float32)
    expected = cv2.warpAffine(pixels, inverse, (8, 6), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)
    expected = np.rot90(expected, k=-(rotation // 90))
    # OpenCV quantizes interpolation weights to 1/32; GPU filters and framebuffer
    # conversion round differently. Bounds detect an integer-rounded UV crop.
    difference = np.abs(output.astype(np.int16) - expected.astype(np.int16))
    assert difference.max() <= 2
    assert difference.mean() <= 1
  finally:
    presenter.close()


@pytest.mark.parametrize("bad_uv", [(0, 0, 0, 1), (0.8, 0, 0.2, 1), (-0.01, 0, 1, 1), (0, 0, 1, float("nan"))])
def test_invalid_gpu_crop_does_not_replace_retained_frame(raster_pygame, bad_uv):
  pygame, video = raster_pygame
  camera = pygame.Surface((8, 4), depth=24)
  overlay = pygame.Surface((8, 4), pygame.SRCALPHA, 32)
  presenter = TexturePresenter(pygame, size=(8, 4), fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  try:
    presenter.present_layers(overlay, camera, (0, 0, 8, 4))
    retained = presenter._layered_frame
    with pytest.raises(ValueError, match="UV bounds"):
      presenter.present_layers(overlay, camera, (0, 0, 8, 4), bad_uv)
    assert presenter._layered_frame == retained
  finally:
    presenter.close()


def test_accelerated_premultiplied_path_blends_before_hud_and_brightness(accelerated_pygame):
  import numpy as np
  pygame, video = accelerated_pygame
  try:
    presenter = TexturePresenter(pygame, size=(32, 16), fullscreen=False,
                                 accelerated=True, vsync=False, hidden=True, video_module=video)
  except pygame.error as error:
    pytest.skip(f"No accelerated SDL renderer for raster check: {error}")
  try:
    camera = pygame.Surface((32, 16), depth=24)
    camera.fill((100, 80, 60))
    path = pygame.Surface((32, 16), pygame.SRCALPHA, 32)
    # Second region contains two already-composited semitransparent polygons.
    pygame.draw.rect(path, (100, 10, 0, 128), (0, 0, 16, 16))
    pygame.draw.rect(path, (50, 5, 100, 192), (16, 0, 16, 16))
    overlay = pygame.Surface((32, 16), pygame.SRCALPHA, 32)
    overlay.fill((0, 0, 0, 0))
    pygame.draw.rect(overlay, (20, 220, 40, 128), (0, 0, 8, 8))
    presenter.present_layers(overlay, camera, (0, 0, 32, 16), controls=_controls(brightness=50), path_surface=path)
    output = presenter.renderer.to_surface()
    for point, premul, alpha, hud in (((8, 8), (100, 10, 0), 128, False),
                                      ((24, 8), (50, 5, 100), 192, False),
                                      ((4, 4), (100, 10, 0), 128, True)):
      expected = np.array(premul) + np.array([100, 80, 60]) * (1 - alpha / 255)
      if hud:
        expected = np.array([20, 220, 40]) * (128 / 255) + expected * (1 - 128 / 255)
      expected *= 128 / 255
      assert np.abs(np.array(tuple(output.get_at(point))[:3]) - expected).max() <= 2
    assert "path" in presenter._layers.textures
  finally:
    presenter.close()


def test_unsupported_premultiplied_blend_can_disable_layers_and_keep_fullframe(raster_pygame, monkeypatch):
  pygame, video = raster_pygame
  camera = pygame.Surface((8, 4), depth=24)
  overlay = pygame.Surface((8, 4), pygame.SRCALPHA, 32)
  presenter = TexturePresenter(pygame, size=(8, 4), fullscreen=False,
                               accelerated=False, vsync=False, hidden=True, video_module=video)
  try:
    assert presenter.can_present_layers
    original = presenter._layers.sdl.SDL_SetTextureBlendMode

    def set_blend(texture, mode):
      return -1 if mode == presenter._layers._premultiplied_blend else original(texture, mode)

    monkeypatch.setattr(presenter._layers.sdl, "SDL_SetTextureBlendMode", set_blend)
    with pytest.raises(RuntimeError):
      presenter.present_layers(overlay, camera, (0, 0, 8, 4), path_surface=overlay)
    presenter.disable_layers()
    assert not presenter.can_present_layers
    presenter.present(camera)
    assert presenter._texture is not None
  finally:
    presenter.close()


@pytest.mark.parametrize("scene_rotation", [False, True])
def test_accelerated_real_scene_layers_match_retained_software_fallback(accelerated_pygame, scene_rotation):
  from pathlib import Path
  import numpy as np
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_scene import H264Scene

  pygame, video = accelerated_pygame
  try:
    presenter = TexturePresenter(pygame, fullscreen=False, accelerated=True, vsync=False, hidden=True, video_module=video)
  except pygame.error as error:
    pytest.skip(f"No accelerated SDL renderer for raster check: {error}")
  scene = H264Scene(Path(__file__).resolve().parents[5] / "assets", clock=lambda: 10.0)
  try:
    xs = list(range(5, 81, 5))
    ys = [0.0008 * x * x for x in xs]
    def lane(offset):
      return [xs, [y + offset for y in ys], [1.22] * len(xs)]
    snapshot = {
      "model_valid": True, "source_size": [1344, 760], "rotate_180": scene_rotation,
      "hud": {"v_ego": 22.0, "enabled": True, "lat_active": True, "engageable": True,
              "steering_angle": 12.0, "gps_satellites": 12, "wifi_strength": 4,
              "tpms": [35, 36, 35, 36], "distance_level": 3},
      "path": {"path_x": xs, "path_y": ys, "path_z": [0.0] * len(xs),
               "calib_transform": [[672, -910, 0], [380, 0, 910], [1, 0, 0]],
               "lane_lines": [lane(-3.6), lane(-1.8), lane(1.8), lane(3.6)],
               "lane_line_probs": [0.5, 0.9, 0.9, 0.5],
               "road_edges": [lane(-4.2), lane(4.2)], "road_edge_stds": [0.1, 0.2],
               "leads": [{"present": True, "d_rel": 22.0, "d_camera": 22.0, "y_rel": 0.2, "v_rel": -1.5}]},
    }
    scene.update(snapshot)
    pixels = np.empty((720, 1280, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.linspace(30, 180, 1280).astype(np.uint8)[None, :]
    pixels[:, :, 1] = np.linspace(30, 180, 720).astype(np.uint8)[:, None]
    pixels[:, :, 2] = 80
    scene.set_video(pixels)
    layers = scene.render_layers()
    assert layers.path is not None and np.count_nonzero(layers.path[:, :, 3]) > 1000
    overlay = pygame.image.frombuffer(layers.overlay.tobytes(), layers.overlay.size, "RGBA")
    camera = pygame.image.frombuffer(layers.camera, layers.camera.shape[1::-1], "RGB")
    path = pygame.image.frombuffer(layers.path, layers.path.shape[1::-1], "RGBA")
    presenter.present_layers(overlay, camera, layers.camera_rect, layers.camera_uv,
                             rotate_180=layers.rotate_180, path_surface=path)
    actual = pygame.surfarray.array3d(presenter.renderer.to_surface()).transpose(1, 0, 2)
    expected = np.asarray(layers.to_image())
    difference = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
    assert difference.max() <= 3
    assert difference.mean() <= 0.6
  finally:
    scene.close()
    presenter.close()
