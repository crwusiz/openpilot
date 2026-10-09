import threading
from types import SimpleNamespace

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


def _controls(brightness=80, screen_off=False, menu=None):
  value = 0 if screen_off else round(brightness * 255 / 100)
  return SimpleNamespace(screen_off=screen_off, tint_color=(value,) * 3, logical_size=(1920, 480), render_menu=lambda: menu)


def test_standalone_window_selects_display_and_accelerated_renderer():
  video, calls = _fake_video()
  pygame = SimpleNamespace(Rect=lambda *args: args)
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
  presenter = TexturePresenter(SimpleNamespace(Rect=lambda *args: args), video_module=video)
  frame = _frame()
  presenter.present(frame, _controls())
  presenter.present(frame, _controls(brightness=35))
  presenter.redraw(_controls(brightness=20))
  assert len([call for call in calls if call[0] == "texture"]) == 1
  assert len([call for call in calls if call[0] == "update"]) == 2
  assert [call[1] for call in calls if call[0] == "draw"] == [(204,) * 3, (89,) * 3, (51,) * 3]
  assert next(call for call in calls if call[0] == "texture") == ("texture", (1920, 480), {"streaming": True})
  presenter.present(_frame((960, 240)))
  assert len([call for call in calls if call[0] == "texture"]) == 2
  presenter.close()


@pytest.mark.parametrize("rotation, size, expected", [
  (0, (1920, 480), (0, 0, 1920, 480)),
  (90, (480, 1920), (-720, 720, 1920, 480)),
  (180, (1920, 480), (0, 0, 1920, 480)),
  (270, (480, 1920), (-720, 720, 1920, 480)),
])
def test_rotation_and_scaling_are_delegated_to_sdl(rotation, size, expected):
  video, calls = _fake_video()
  presenter = TexturePresenter(SimpleNamespace(Rect=lambda *args: args), size=size, rotation=rotation, video_module=video)
  timings = presenter.present(_frame())
  draw = next(call for call in calls if call[0] == "draw")
  assert draw[2] == {"dstrect": expected, "angle": rotation}
  assert timings["rotate"] == timings["scale"] == 0
  assert timings["upload"] >= 0
  presenter.close()


def test_screen_off_skips_frame_upload_and_clears_renderer():
  video, calls = _fake_video()
  presenter = TexturePresenter(SimpleNamespace(Rect=lambda *args: args), video_module=video)
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
  presenter = TexturePresenter(SimpleNamespace(Rect=lambda *args: args), size=(480, 1920), rotation=90, video_module=video)
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
  presenter = TexturePresenter(SimpleNamespace(Rect=lambda *args: args), video_module=video)
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
  pygame.display.init()
  yield pygame, video
  pygame.display.quit()


@pytest.mark.parametrize("rotation, expected_rows", [
  (0, [[0, 1, 2, 3], [4, 5, 6, 7]]),
  (90, [[4, 0], [5, 1], [6, 2], [7, 3]]),
  (180, [[7, 6, 5, 4], [3, 2, 1, 0]]),
  (270, [[3, 7], [2, 6], [1, 5], [0, 4]]),
])
def test_sdl_pixel_rotation_matches_touch_coordinate_system(raster_pygame, rotation, expected_rows):
  pygame, video = raster_pygame
  colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (255, 0, 255), (0, 255, 255), (255, 255, 255), (64, 64, 64)]
  frame = pygame.Surface((4, 2))
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
