import ctypes
import subprocess
import threading
from types import SimpleNamespace
import weakref

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import hdmi_display
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.hdmi_display import HdmiDisplay


def _fake_pygame(events):
  return SimpleNamespace(
    QUIT=1,
    KEYDOWN=2,
    K_ESCAPE=27,
    FINGERDOWN=3,
    FINGERMOTION=4,
    FINGERUP=5,
    event=SimpleNamespace(get=lambda: events),
  )


def test_touch_events_are_forwarded_to_handler():
  event = SimpleNamespace(type=3, finger_id=7, x=0.25, y=0.75)
  touches = []
  display = HdmiDisplay(pygame_module=_fake_pygame([event]), touch_handler=touches.append)

  assert display.pump_events()
  assert display.last_touch == {
    "type": 3,
    "finger_id": 7,
    "x": 0.25,
    "y": 0.75,
  }
  assert touches == [display.last_touch]


@pytest.mark.parametrize("rotation, expected", [
  (0, [(0, 0), (1, 0), (0, 1), (1, 1)]),
  (90, [(0, 1), (0, 0), (1, 1), (1, 0)]),
  (180, [(1, 1), (0, 1), (1, 0), (0, 0)]),
  (270, [(1, 0), (1, 1), (0, 0), (0, 1)]),
])
def test_panel_corners_map_back_to_cluster_coordinates(rotation, expected):
  corners = [(0, 0), (1, 0), (0, 1), (1, 1)]
  events = [SimpleNamespace(type=3, finger_id=index, x=x, y=y)
            for index, (x, y) in enumerate(corners)]
  touches = []
  display = HdmiDisplay(rotation=rotation, pygame_module=_fake_pygame(events), touch_handler=touches.append)

  assert display.pump_events()
  assert [(touch["x"], touch["y"]) for touch in touches] == expected
  assert [touch["finger_id"] for touch in touches] == [0, 1, 2, 3]


@pytest.mark.parametrize("touch_rotation, expected", [
  (0, (0.25, 0.75)),
  (90, (0.25, 0.25)),
  (180, (0.75, 0.25)),
  (270, (0.75, 0.75)),
])
def test_explicit_touch_correction_overrides_frame_rotation(touch_rotation, expected):
  events = [SimpleNamespace(type=event_type, finger_id=7, x=0.25, y=0.75) for event_type in (3, 4, 5)]
  touches = []
  display = HdmiDisplay(rotation=90, touch_rotation=touch_rotation,
                        pygame_module=_fake_pygame(events), touch_handler=touches.append)

  assert display.pump_events()
  assert [touch["type"] for touch in touches] == [3, 4, 5]
  assert all((touch["x"], touch["y"]) == expected for touch in touches)


def test_touch_logging_reports_raw_and_corrected_coordinates(caplog):
  event = SimpleNamespace(type=3, finger_id=7, x=0.25, y=0.75)
  display = HdmiDisplay(rotation=90, log_touch=True, pygame_module=_fake_pygame([event]))

  with caplog.at_level("INFO", logger="cluster_receiver.hdmi"):
    assert display.pump_events()

  assert "raw=(0.2500, 0.7500) cluster=(0.7500, 0.7500)" in caplog.text


@pytest.mark.parametrize("options", [{"rotation": 45}, {"touch_rotation": -90}])
def test_invalid_rotation_is_rejected(options):
  with pytest.raises(ValueError):
    HdmiDisplay(**options)


def _window_pygame(driver, calls, set_mode_error=None):
  screen = SimpleNamespace(fill=lambda color: None, get_size=lambda: (480, 1920))

  def set_mode(size, flags, **kwargs):
    calls.append(("set_mode", size, flags, kwargs))
    if set_mode_error:
      raise RuntimeError(set_mode_error)
    return screen

  return SimpleNamespace(
    DOUBLEBUF=1, FULLSCREEN=2, OPENGL=4,
    GL_CONTEXT_PROFILE_MASK=10, GL_CONTEXT_PROFILE_ES=11,
    GL_CONTEXT_MAJOR_VERSION=12, GL_CONTEXT_MINOR_VERSION=13, GL_DEPTH_SIZE=14, GL_STENCIL_SIZE=15,
    display=SimpleNamespace(init=lambda: calls.append(("init",)), get_driver=lambda: driver,
                            gl_set_attribute=lambda *args: calls.append(("gl_attribute", *args)),
                            set_mode=set_mode, set_caption=lambda caption: None, flip=lambda: None,
                            quit=lambda: calls.append(("quit",))),
    mouse=SimpleNamespace(set_visible=lambda visible: None),
  )


@pytest.mark.parametrize("driver", ["kmsdrm", "KMSDRM"])
def test_kmsdrm_requests_es2_before_creating_a_blittable_surface(monkeypatch, driver):
  monkeypatch.delenv("SDL_RENDER_DRIVER", raising=False)
  calls = []
  pygame = _window_pygame(driver, calls)
  display = HdmiDisplay(width=480, height=1920, pygame_module=pygame)

  assert display.open()
  assert calls == [
    ("init",),
    ("gl_attribute", pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_ES),
    ("gl_attribute", pygame.GL_CONTEXT_MAJOR_VERSION, 2),
    ("gl_attribute", pygame.GL_CONTEXT_MINOR_VERSION, 0),
    ("gl_attribute", pygame.GL_DEPTH_SIZE, 0),
    ("gl_attribute", pygame.GL_STENCIL_SIZE, 0),
    ("set_mode", (480, 1920), pygame.DOUBLEBUF | pygame.FULLSCREEN, {"display": 0, "vsync": 1}),
  ]
  # OPENGL would change pygame's Surface API and prevent the JPEG blit path.
  assert not calls[-1][2] & pygame.OPENGL
  assert hdmi_display.os.environ["SDL_RENDER_DRIVER"] == "opengles2"
  display.close()


@pytest.mark.parametrize("driver", ["x11", "wayland", "dummy"])
def test_other_video_backends_keep_surface_settings(monkeypatch, driver):
  monkeypatch.setenv("SDL_RENDER_DRIVER", "software")
  calls = []
  pygame = _window_pygame(driver, calls)
  display = HdmiDisplay(pygame_module=pygame)

  assert display.open()
  assert not any(call[0] == "gl_attribute" for call in calls)
  assert hdmi_display.os.environ["SDL_RENDER_DRIVER"] == "software"
  display.close()


def test_gbm_failure_points_to_diagnostics_and_releases_display(monkeypatch, caplog):
  monkeypatch.setenv("SDL_RENDER_DRIVER", "opengles2")
  calls = []
  pygame = _window_pygame("kmsdrm", calls, "Can't window GBM/EGL surfaces on window creation.")
  display = HdmiDisplay(pygame_module=pygame)

  with caplog.at_level("INFO", logger="cluster_receiver.hdmi"):
    assert not display.open()
  assert calls[-1] == ("quit",)
  assert display.screen is None
  assert not display.connected
  assert "GBM/EGL window creation failed after SDL video initialization" in caplog.text
  assert "scripts/diagnose.sh --egl" in caplog.text


def test_kmsdrm_initialization_failure_reports_package_and_cleans_up(monkeypatch, caplog):
  monkeypatch.setenv("SDL_VIDEODRIVER", "kmsdrm")
  monkeypatch.setattr(hdmi_display, "get_sdl_video_drivers", lambda _pygame: None)
  closed = []

  def fail_init():
    raise RuntimeError("kmsdrm not available")

  pygame = SimpleNamespace(__file__="/opt/cluster-receiver/.venv/pygame/__init__.py",
                            display=SimpleNamespace(init=fail_init, quit=lambda: closed.append(True)))
  display = HdmiDisplay(pygame_module=pygame)
  with caplog.at_level("INFO", logger="cluster_receiver.hdmi"):
    assert not display.open()
  assert not display.connected
  assert display.screen is None
  assert closed == [True]
  assert pygame.__file__ in caplog.text
  assert "SDL_VIDEODRIVER=kmsdrm" in caplog.text
  assert "Unable to enumerate this pygame's SDL video drivers." in caplog.text
  assert "collect the KMSDRM diagnostics before reinstalling packages" in caplog.text


@pytest.mark.parametrize("drivers, expected_message", [
  (["x11", "wayland", "dummy"], "This SDL build has no KMSDRM backend."),
  (["x11", "KmSdRm", "dummy"],
   "KMSDRM is compiled in; inspect DRM cards, connected outputs, libraries, and DRM master ownership."),
])
def test_kmsdrm_initialization_failure_distinguishes_compiled_backend(monkeypatch, caplog, drivers, expected_message):
  monkeypatch.setenv("SDL_VIDEODRIVER", "kmsdrm")
  monkeypatch.setattr(hdmi_display, "get_sdl_video_drivers", lambda _pygame: drivers)

  def fail_init():
    raise RuntimeError("kmsdrm not available")

  pygame = SimpleNamespace(display=SimpleNamespace(init=fail_init, quit=lambda: None))
  display = HdmiDisplay(pygame_module=pygame)
  with caplog.at_level("INFO", logger="cluster_receiver.hdmi"):
    assert not display.open()

  assert "Compiled SDL video drivers:" in caplog.text
  assert all(driver in caplog.text for driver in drivers)
  assert expected_message in caplog.text
  assert "collect the KMSDRM diagnostics before reinstalling packages" in caplog.text


def test_video_driver_enumeration_uses_pygame_linked_sdl_without_display_init(monkeypatch):
  drivers = [b"x11", b"KMSDRM", b"dummy"]
  loaded_paths = []

  def get_num_drivers():
    return len(drivers)

  def get_driver(index):
    return drivers[index]

  library = SimpleNamespace(SDL_GetNumVideoDrivers=get_num_drivers, SDL_GetVideoDriver=get_driver)

  def load_library(path):
    loaded_paths.append(path)
    return library

  monkeypatch.setattr(hdmi_display.ctypes, "CDLL", load_library)
  pygame = SimpleNamespace(base=SimpleNamespace(__file__="/usr/lib/python3/dist-packages/pygame/base.so"),
                            display=SimpleNamespace(init=lambda: pytest.fail("enumeration must not initialize display")))

  assert hdmi_display.get_sdl_video_drivers(pygame) == ["x11", "KMSDRM", "dummy"]
  assert loaded_paths == [pygame.base.__file__]
  assert get_num_drivers.argtypes == []
  assert get_num_drivers.restype is ctypes.c_int
  assert get_driver.argtypes == [ctypes.c_int]
  assert get_driver.restype is ctypes.c_char_p


@pytest.mark.parametrize("error", [OSError("library unavailable"), AttributeError("SDL_GetVideoDriver unavailable")])
def test_video_driver_enumeration_returns_unknown_for_unavailable_library(monkeypatch, error):
  def load_library(_path):
    raise error

  monkeypatch.setattr(hdmi_display.ctypes, "CDLL", load_library)
  pygame = SimpleNamespace(base=SimpleNamespace(__file__="/usr/lib/python3/dist-packages/pygame/base.so"))

  assert hdmi_display.get_sdl_video_drivers(pygame) is None


def test_video_driver_enumeration_returns_unknown_when_pygame_base_is_unavailable():
  assert hdmi_display.get_sdl_video_drivers(SimpleNamespace()) is None


@pytest.fixture
def raster_pygame(monkeypatch):
  monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", "1")
  monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
  monkeypatch.setattr(hdmi_display, "get_network_status", lambda _interface: None)
  pygame = pytest.importorskip("pygame")
  pygame.display.quit()
  yield pygame
  pygame.display.quit()


@pytest.mark.parametrize("rotation, expected_rows", [
  (0, [[0, 1, 2, 3], [4, 5, 6, 7]]),
  (90, [[4, 0], [5, 1], [6, 2], [7, 3]]),
  (180, [[7, 6, 5, 4], [3, 2, 1, 0]]),
  (270, [[3, 7], [2, 6], [1, 5], [0, 4]]),
])
def test_frame_rotation_preserves_pixels_and_touch_positions(raster_pygame, monkeypatch, rotation, expected_rows):
  pygame = raster_pygame
  colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (255, 0, 255), (0, 255, 255), (255, 255, 255), (64, 64, 64)]
  frame = pygame.Surface((4, 2))
  for index, color in enumerate(colors):
    frame.set_at((index % 4, index // 4), color)
  # Keep image decoding outside this orientation test; all transforms and blits
  # use real pygame pixels, so an incorrect rotation or scale order is visible.
  monkeypatch.setattr(pygame.image, "load", lambda *_args: frame)
  monkeypatch.setattr(pygame.transform, "smoothscale", lambda *_args: pytest.fail("matching native mode must not scale"))
  width, height = len(expected_rows[0]), len(expected_rows)
  touches = []
  display = HdmiDisplay(width=width, height=height, rotation=rotation, fullscreen=False,
                        pygame_module=pygame, touch_handler=touches.append, brightness=100, renderer_mode="surface")
  try:
    assert display.open()
    assert display.send_jpeg(b"decoded-frame-fixture")
    actual = [[tuple(display.screen.get_at((x, y)))[:3] for x in range(width)] for y in range(height)]
    assert actual == [[colors[index] for index in row] for row in expected_rows]

    # Each displayed corner identifies an original pixel. A touch on that
    # corner must map back to the same corner in the unrotated C4 frame.
    for x, y in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]:
      source_index = colors.index(actual[y][x])
      pygame.event.post(pygame.event.Event(pygame.FINGERDOWN, finger_id=7,
                                          x=x / (width - 1), y=y / (height - 1)))
      assert display.pump_events()
      assert (touches[-1]["x"], touches[-1]["y"]) == (
        (source_index % 4) / 3, source_index // 4,
      )
  finally:
    display.close()


def test_quit_event_stops_further_frames():
  display = HdmiDisplay(pygame_module=_fake_pygame([SimpleNamespace(type=1)]))
  display.connected = True

  assert not display.pump_events()
  assert display.close_requested
  assert not display.connected


def test_clear_removes_stale_frame_without_closing_display():
  fills = []
  flips = []
  display = HdmiDisplay(pygame_module=SimpleNamespace(display=SimpleNamespace(flip=lambda: flips.append(True))))
  display.screen = SimpleNamespace(fill=fills.append)
  display.connected = True

  display.clear()

  assert fills == [(0, 0, 0)]
  assert flips == [True]
  assert display.connected


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_waiting_text_matches_landscape_orientation(raster_pygame, monkeypatch, rotation):
  pygame = raster_pygame
  monkeypatch.setattr(hdmi_display.os.path, "isfile", lambda path: False)
  monkeypatch.setattr(pygame.font, "match_font", lambda name: None)
  logical_size = (384, 96)
  reference = HdmiDisplay(*logical_size, fullscreen=False, pygame_module=pygame)
  assert reference.open()
  assert reference.show_waiting()
  expected = pygame.image.tobytes(reference.screen, "RGB")
  assert any(expected), "waiting text must be visible on the black background"
  reference.close()

  size = logical_size[::-1] if rotation in (90, 270) else logical_size
  display = HdmiDisplay(*size, rotation=rotation, fullscreen=False, pygame_module=pygame)
  try:
    assert display.open()
    assert display.show_waiting()
    unrotated = pygame.transform.rotate(display.screen, rotation)
    assert unrotated.get_size() == logical_size
    assert pygame.image.tobytes(unrotated, "RGB") == expected
  finally:
    display.close()


def test_waiting_screen_is_replaced_by_live_frame_and_returns_on_disconnect(raster_pygame, monkeypatch):
  pygame = raster_pygame
  monkeypatch.setattr(hdmi_display.os.path, "isfile", lambda path: False)
  monkeypatch.setattr(pygame.font, "match_font", lambda name: None)
  display = HdmiDisplay(384, 96, pygame_module=pygame, fullscreen=False, brightness=100, renderer_mode="surface")
  flips = []
  monkeypatch.setattr(pygame.display, "flip", lambda: flips.append(True))
  live_frame = pygame.Surface((384, 96))
  live_frame.fill((255, 0, 0))
  monkeypatch.setattr(pygame.image, "load", lambda *_args: live_frame)
  try:
    assert display.open()
    assert display.show_waiting()
    waiting_pixels = pygame.image.tobytes(display.screen, "RGB")
    flip_count = len(flips)
    assert display.show_waiting()
    assert len(flips) == flip_count, "an unchanged waiting screen should not redraw on every retry"

    assert display.send_jpeg(b"live-frame")
    assert display.screen.get_at((0, 0))[:3] == (255, 0, 0)
    assert display.show_waiting()
    assert pygame.image.tobytes(display.screen, "RGB") == waiting_pixels

    display.clear()
    assert not any(pygame.image.tobytes(display.screen, "RGB"))
    assert display.show_waiting()
    assert pygame.image.tobytes(display.screen, "RGB") == waiting_pixels
  finally:
    display.close()


def test_waiting_is_not_drawn_on_a_closed_display():
  display = HdmiDisplay()
  assert not display.show_waiting()
  display.screen = object()
  display.close_requested = True
  assert not display.show_waiting()


@pytest.mark.parametrize("ssid", ["Android", "차량: Wi-Fi\\SSID", "wlan1"])
def test_network_status_reads_actual_ssid_and_interface_ip_without_rescanning(monkeypatch, ssid):
  commands = []

  def run(command, **kwargs):
    commands.append(command)
    assert "wlan1" in command
    assert kwargs["timeout"] == 1.0
    assert kwargs["encoding"] == "utf-8"
    if "show" in command:
      return SimpleNamespace(stdout="GENERAL.STATE:100 (connected)\nGENERAL.TYPE:wifi\nIP4.ADDRESS[1]:192.168.43.27/24\n")
    return SimpleNamespace(stdout=f":Other hotspot\n*:{ssid}\n")

  monkeypatch.setattr(hdmi_display.subprocess, "run", run)
  assert hdmi_display.get_network_status("wlan1") == (ssid, "192.168.43.27")
  assert commands[-1][-2:] == ["--rescan", "no"]
  assert commands[-1][commands[-1].index("--escape") + 1] == "no"


@pytest.mark.parametrize("details", [
  "GENERAL.STATE:30 (disconnected)\nIP4.ADDRESS[1]:192.168.43.27/24\n",
  "GENERAL.STATE:70 (connecting)\nIP4.ADDRESS[1]:192.168.43.27/24\n",
  "GENERAL.STATE:100 (connected)\nIP6.ADDRESS[1]:2001:db8::1/64\n",
  "GENERAL.STATE:100 (connected)\nIP4.ADDRESS[1]:169.254.10.1/16\n",
  "GENERAL.STATE:100 (connected)\nIP4.ADDRESS[1]:invalid\n",
])
def test_network_status_is_offline_until_connected_with_usable_ipv4(monkeypatch, details):
  monkeypatch.setattr(hdmi_display, "_run_nmcli", lambda *_args: details)
  assert hdmi_display.get_network_status("wlan0") is None


@pytest.mark.parametrize("error", [
  FileNotFoundError("nmcli unavailable"),
  subprocess.TimeoutExpired("nmcli", 1.0),
  subprocess.CalledProcessError(8, "nmcli"),
])
def test_network_query_failure_keeps_offline_status(monkeypatch, error):
  def fail(*_args):
    raise error

  monkeypatch.setattr(hdmi_display, "_run_nmcli", fail)
  assert hdmi_display.get_network_status("wlan0") is None


def test_wired_network_status_uses_the_selected_interface(monkeypatch):
  monkeypatch.setattr(hdmi_display, "_run_nmcli", lambda *_args: (
    "GENERAL.STATE:100 (connected)\nGENERAL.TYPE:ethernet\nIP4.ADDRESS[1]:192.168.0.27/24\n"
  ))
  assert hdmi_display.get_network_status("eth0") == (None, "192.168.0.27")


def test_waiting_screen_refreshes_network_without_blocking_or_redrawing_unchanged_status(raster_pygame, monkeypatch):
  pygame = raster_pygame
  monkeypatch.setattr(hdmi_display.os.path, "isfile", lambda path: False)
  monkeypatch.setattr(pygame.font, "match_font", lambda name: None)
  now = [0.0]
  monkeypatch.setattr(hdmi_display.time, "monotonic", lambda: now[0])
  network = [("Android", "192.168.43.27")]
  query_started = threading.Event()
  query_release = threading.Event()
  rendered = []
  flips = []
  original_font = pygame.font.Font

  def font(path, size):
    actual_font = original_font(path, size)

    def render(text, antialias, color):
      rendered.append(text)
      return actual_font.render(text, antialias, color)

    return SimpleNamespace(render=render)

  def get_status(interface):
    assert interface == "wlan1"
    query_started.set()
    query_release.wait(timeout=1.0)
    return network[0]

  monkeypatch.setattr(pygame.font, "Font", font)
  monkeypatch.setattr(pygame.display, "flip", lambda: flips.append(True))
  monkeypatch.setattr(hdmi_display, "get_network_status", get_status)
  display = HdmiDisplay(384, 96, pygame_module=pygame, fullscreen=False, network_interface="wlan1")
  try:
    assert display.open()
    assert display.show_waiting()
    assert query_started.wait(timeout=1.0)
    # The waiting screen is already visible while the network query is blocked.
    assert rendered[-1] == "Network Offline"
    assert display.pump_events()
    query_release.set()
    display._network_future.result(timeout=1.0)
    assert display.pump_events()
    assert rendered[-2:] == ["SSID: Android", "IP: 192.168.43.27"]
    flip_count = len(flips)
    assert display.pump_events()
    assert len(flips) == flip_count

    for status in [("New hotspot", "192.168.0.28"), None, ("Android", "192.168.43.29")]:
      network[0] = status
      now[0] += hdmi_display.NETWORK_REFRESH_SECONDS
      assert display.pump_events()
      display._network_future.result(timeout=1.0)
      assert display.pump_events()
      if status is None:
        assert rendered[-1] == "Network Offline"
      else:
        assert rendered[-2:] == [f"SSID: {status[0]}", f"IP: {status[1]}"]
      assert len(flips) == flip_count + 1
      flip_count = len(flips)
  finally:
    query_release.set()
    display.close()


def test_failed_live_frame_flip_restores_waiting_instead_of_skipping_redraw(raster_pygame, monkeypatch):
  pygame = raster_pygame
  monkeypatch.setattr(hdmi_display.os.path, "isfile", lambda path: False)
  monkeypatch.setattr(pygame.font, "match_font", lambda name: None)
  display = HdmiDisplay(384, 96, pygame_module=pygame, fullscreen=False)
  live_frame = pygame.Surface((384, 96))
  live_frame.fill((255, 0, 0))
  monkeypatch.setattr(pygame.image, "load", lambda *_args: live_frame)
  try:
    assert display.open()
    assert display.show_waiting()
    waiting_pixels = pygame.image.tobytes(display.screen, "RGB")
    original_flip = pygame.display.flip

    def fail_flip():
      raise pygame.error("page flip failed")

    monkeypatch.setattr(pygame.display, "flip", fail_flip)
    assert not display.send_jpeg(b"live-frame")
    monkeypatch.setattr(pygame.display, "flip", original_flip)
    assert display.show_waiting()
    assert pygame.image.tobytes(display.screen, "RGB") == waiting_pixels
  finally:
    display.close()


def _control_target(display, name, fraction=0.5):
  left, top, width, height = display.controls.layout[name]
  logical_width, logical_height = display.controls.logical_size
  return (left + width * fraction) / logical_width, (top + height / 2) / logical_height


def _post_touch(display, event_type, point=(0.5, 0.5), finger_id=7):
  x, y = point
  # Convert a logical cluster position to the panel's default raw coordinates.
  if display.rotation == 90:
    x, y = 1 - y, x
  elif display.rotation == 180:
    x, y = 1 - x, 1 - y
  elif display.rotation == 270:
    x, y = y, 1 - x
  pygame = display._pygame
  pygame.event.post(pygame.event.Event(event_type, finger_id=finger_id, x=x, y=y))


def _tap_display(display, point=(0.5, 0.5)):
  _post_touch(display, display._pygame.FINGERDOWN, point)
  _post_touch(display, display._pygame.FINGERUP, point)
  assert display.pump_events()


def _raster_display(pygame, rotation=0, renderer_mode="surface"):
  size = (96, 384) if rotation in (90, 270) else (384, 96)
  display = HdmiDisplay(*size, rotation=rotation, pygame_module=pygame, fullscreen=False, renderer_mode=renderer_mode)
  assert display.open()
  return display


def test_default_brightness_dims_received_frame_once(raster_pygame, monkeypatch):
  pygame = raster_pygame
  frame = pygame.Surface((384, 96))
  frame.fill((255, 100, 50))
  monkeypatch.setattr(pygame.image, "load", lambda *_args: frame)
  display = _raster_display(pygame)
  try:
    assert display.send_jpeg(b"first")
    assert display.last_frame_presented
    assert display.screen.get_at((0, 0))[:3] == (204, 80, 40)
    _tap_display(display)
    assert display.controls.menu_visible
    assert not display.last_frame_presented, "local menu redraw is not another received frame"
    _tap_display(display, _control_target(display, "slider", 0.5))
    assert display.controls.brightness == 60
    _tap_display(display, _control_target(display, "close"))
    assert display.screen.get_at((0, 0))[:3] == (153, 60, 30)
    for _ in range(3):
      assert display.pump_events()
    assert display.screen.get_at((0, 0))[:3] == (153, 60, 30)
    assert frame.get_at((0, 0))[:3] == (255, 100, 50)
  finally:
    display.close()


def test_screen_off_state_is_readable_from_receiver_thread_without_sdl(raster_pygame):
  display = _raster_display(raster_pygame)
  state = []
  try:
    display.controls.turn_off()
    thread = threading.Thread(target=lambda: state.append(display.screen_off))
    thread.start()
    thread.join(timeout=1)
    assert state == [True]
  finally:
    display.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_off_skips_decoding_and_transforms_then_wakes_newest_jpeg_without_menu(raster_pygame, monkeypatch, rotation):
  pygame = raster_pygame
  decoded = []
  transforms = []
  original_rotate = pygame.transform.rotate

  def load(stream, _name):
    jpeg = stream.getvalue()
    decoded.append(jpeg)
    frame = pygame.Surface((384, 96))
    frame.fill((0, 0, 255) if jpeg == b"latest" else (255, 0, 0))
    return frame

  def rotate(frame, angle):
    transforms.append(angle)
    return original_rotate(frame, angle)

  monkeypatch.setattr(pygame.image, "load", load)
  monkeypatch.setattr(pygame.transform, "rotate", rotate)
  display = _raster_display(pygame, rotation)
  try:
    assert display.send_jpeg(b"visible")
    _tap_display(display)
    _tap_display(display, _control_target(display, "slider", 0.5))
    _tap_display(display, _control_target(display, "off"))
    assert display.controls.screen_off
    assert not any(pygame.image.tobytes(display.screen, "RGB"))
    assert display._latest_frame is None
    decoded.clear()
    transforms.clear()
    assert display.send_jpeg(b"old")
    assert display.send_jpeg(b"latest")
    assert not display.last_frame_presented
    assert not decoded
    assert not transforms
    assert not any(display.last_frame_timings.values())

    _post_touch(display, pygame.FINGERDOWN)
    _post_touch(display, pygame.FINGERMOTION, _control_target(display, "off"))
    _post_touch(display, pygame.FINGERUP)
    assert display.pump_events()
    assert decoded == [b"latest"]
    assert not display.controls.screen_off
    assert display.controls.brightness == 60
    assert not display.controls.menu_visible
    assert not display.last_frame_presented
    assert display.screen.get_at((0, 0))[:3] == (0, 0, 153)
  finally:
    display.close()


def test_controls_open_and_auto_hide_without_incoming_frames(raster_pygame, monkeypatch):
  pygame = raster_pygame
  display = _raster_display(pygame)
  now = [0.0]
  display.controls.clock = lambda: now[0]
  frame = pygame.Surface((384, 96))
  frame.fill((255, 0, 0))
  monkeypatch.setattr(pygame.image, "load", lambda *_args: frame)
  try:
    assert display.send_jpeg(b"visible")
    initial = pygame.image.tobytes(display.screen, "RGB")
    _tap_display(display)
    assert pygame.image.tobytes(display.screen, "RGB") != initial
    now[0] += display.controls.menu_timeout
    assert display.pump_events()
    assert not display.controls.menu_visible
    assert pygame.image.tobytes(display.screen, "RGB") == initial
  finally:
    display.close()


def test_waiting_screen_controls_wake_without_any_network_frame(raster_pygame):
  pygame = raster_pygame
  display = _raster_display(pygame)
  try:
    assert display.show_waiting()
    _tap_display(display)
    assert display.controls.menu_visible
    _tap_display(display, _control_target(display, "off"))
    assert not any(pygame.image.tobytes(display.screen, "RGB"))
    assert display.show_waiting()
    assert not any(pygame.image.tobytes(display.screen, "RGB"))
    _tap_display(display)
    assert not display.controls.screen_off
    assert not display.controls.menu_visible
    assert display._showing_waiting
    assert any(pygame.image.tobytes(display.screen, "RGB"))
  finally:
    display.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_desktop_mouse_uses_touch_rotation_and_synthetic_mouse_is_ignored(raster_pygame, rotation):
  pygame = raster_pygame
  display = _raster_display(pygame, rotation)

  def click(point, synthetic=False):
    x, y = point
    if rotation == 90:
      x, y = 1 - y, x
    elif rotation == 180:
      x, y = 1 - x, 1 - y
    elif rotation == 270:
      x, y = y, 1 - x
    width, height = display.screen.get_size()
    pos = round(x * width), round(y * height)
    for event_type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
      pygame.event.post(pygame.event.Event(event_type, button=1, pos=pos, touch=synthetic))
    assert display.pump_events()

  try:
    assert display.show_waiting()
    click((0.5, 0.5), synthetic=True)
    assert not display.controls.menu_visible
    click((0.5, 0.5))
    assert display.controls.menu_visible
    assert display.last_touch["finger_id"] == -1
    click(_control_target(display, "off"))
    assert display.controls.screen_off
    click((0.5, 0.5), synthetic=True)
    assert display.controls.screen_off
    click((0.5, 0.5))
    assert not display.controls.screen_off
    assert not display.controls.menu_visible
  finally:
    display.close()


def test_auto_gpu_failure_logs_and_uses_usable_surface(raster_pygame, monkeypatch, caplog):
  def unavailable(*_args, **_kwargs):
    raise ImportError("old distro pygame lacks _sdl2.video")

  monkeypatch.setattr(hdmi_display, "TexturePresenter", unavailable)
  display = _raster_display(raster_pygame, renderer_mode="auto")
  try:
    assert display._presenter is None
    assert isinstance(display.screen, raster_pygame.Surface)
    assert "falling back to Surface" in caplog.text
    assert "old distro pygame" in caplog.text
    assert display.show_waiting()
    _tap_display(display)
    assert display.controls.menu_visible
  finally:
    display.close()


def test_gpu_path_avoids_surface_conversion_and_cpu_rotation(raster_pygame, monkeypatch):
  pygame = raster_pygame
  video = pytest.importorskip("pygame._sdl2.video")
  original_presenter = hdmi_display.TexturePresenter

  def software_texture(pygame_module, **kwargs):
    return original_presenter(pygame_module, **kwargs, video_module=video, accelerated=False, vsync=False, hidden=True)

  frame = pygame.Surface((384, 96))
  frame.fill((255, 0, 0))
  monkeypatch.setattr(hdmi_display, "TexturePresenter", software_texture)
  monkeypatch.setattr(pygame.image, "load", lambda *_args: frame)
  monkeypatch.setattr(pygame.transform, "rotate", lambda *_args: pytest.fail("GPU must not CPU rotate"))
  monkeypatch.setattr(pygame.transform, "smoothscale", lambda *_args: pytest.fail("GPU must not CPU scale"))
  display = _raster_display(pygame, rotation=90, renderer_mode="auto")
  try:
    assert display._presenter is not None
    assert pygame.display.get_surface() is None, "standalone SDL window must not own another pygame renderer"
    assert display.send_jpeg(b"gpu-frame")
    assert display.last_frame_presented
    assert display.last_frame_timings["rotate"] == display.last_frame_timings["scale"] == 0
    output = display._presenter.renderer.to_surface()
    assert output.get_at((0, 0))[:3] == (204, 0, 0)
    _tap_display(display)
    assert display.controls.menu_visible
    _tap_display(display, _control_target(display, "off"))
    assert not any(pygame.image.tobytes(display._presenter.renderer.to_surface(), "RGB"))
    assert display.send_jpeg(b"off-frame")
    assert not display.last_frame_presented
    _tap_display(display)
    assert not display.controls.menu_visible
    assert display._presenter.renderer.to_surface().get_at((0, 0))[:3] == (204, 0, 0)
  finally:
    display.close()


def _failing_texture_factory(pygame, monkeypatch):
  video = pytest.importorskip("pygame._sdl2.video")
  original_presenter = hdmi_display.TexturePresenter
  failure = [None]
  presenters = []

  class Texture:
    def __init__(self, *args, **kwargs):
      if failure[0] == "create":
        raise pygame.error("GPU texture allocation failed")
      self._texture = video.Texture(*args, **kwargs)

    def update(self, frame):
      if failure[0] == "update":
        raise pygame.error("GPU texture upload failed")
      self._texture.update(frame)

    def draw(self, **kwargs):
      self._texture.draw(**kwargs)

    @property
    def color(self):
      return self._texture.color

    @color.setter
    def color(self, value):
      self._texture.color = value

    from_surface = staticmethod(video.Texture.from_surface)

  module = SimpleNamespace(Window=video.Window, Renderer=video.Renderer, Texture=Texture,
                           WINDOWPOS_CENTERED=video.WINDOWPOS_CENTERED)

  def factory(pygame_module, **kwargs):
    presenter = original_presenter(pygame_module, **kwargs, video_module=module, accelerated=False, vsync=False, hidden=True)
    presenters.append(presenter)
    return presenter

  monkeypatch.setattr(hdmi_display, "TexturePresenter", factory)
  return failure, presenters


@pytest.mark.parametrize("stage", ["create", "update"])
def test_initial_waiting_texture_failure_releases_gpu_and_displays_surface(raster_pygame, monkeypatch, caplog, stage):
  pygame = raster_pygame
  failure, presenters = _failing_texture_factory(pygame, monkeypatch)
  display = _raster_display(pygame, rotation=90, renderer_mode="auto")
  try:
    controls = display.controls
    controls.set_brightness(35)
    failure[0] = stage
    assert display.show_waiting()
    assert display._presenter is None
    assert isinstance(display.screen, pygame.Surface)
    assert presenters[0].window is None
    assert presenters[0].renderer is None
    assert display.controls is controls
    assert controls.brightness == 40
    assert display.rotation == 90
    assert display._showing_waiting
    assert any(pygame.image.tobytes(display.screen, "RGB"))
    assert "surface: texture presentation failed" in caplog.text
    assert "GPU texture" in caplog.text
    assert display.show_waiting()
    assert len(presenters) == 1, "failed GPU backend must not be retried per frame"
  finally:
    display.close()


@pytest.mark.parametrize("stage", ["create", "update"])
def test_live_texture_failure_preserves_rotation_menu_brightness_and_off_wake(raster_pygame, monkeypatch, stage):
  pygame = raster_pygame
  failure, presenters = _failing_texture_factory(pygame, monkeypatch)
  original_load = pygame.image.load

  def load(stream, _name=None):
    if _name is None:
      return original_load(stream)
    # Resizing the input causes another texture creation, allowing either the
    # new allocation or its upload to fail after the renderer initially works.
    jpeg = stream.getvalue()
    frame = pygame.Surface((192, 48) if jpeg == b"new" else (384, 96))
    frame.fill((0, 0, 255) if jpeg == b"off-latest" else (255, 0, 0))
    return frame

  monkeypatch.setattr(pygame.image, "load", load)
  display = _raster_display(pygame, rotation=90, renderer_mode="auto")
  try:
    assert display.send_jpeg(b"initial")
    controls = display.controls
    controls.set_brightness(35)
    _tap_display(display)
    assert controls.menu_visible
    failure[0] = stage
    assert display.send_jpeg(b"new")
    assert display.last_frame_presented
    assert display._presenter is None
    assert presenters[0].window is None
    assert presenters[0].renderer is None
    assert display.controls is controls
    assert controls.menu_visible
    assert controls.brightness == 40
    assert display.screen.get_size() == (96, 384)
    assert display.screen.get_at((0, 0))[:3] == (102, 0, 0)
    _tap_display(display, _control_target(display, "off"))
    assert display.screen_off
    assert not any(pygame.image.tobytes(display.screen, "RGB"))
    assert display.send_jpeg(b"off-latest")
    _tap_display(display)
    assert not display.screen_off
    assert not controls.menu_visible
    assert controls.brightness == 40
    assert display.screen.get_at((0, 0))[:3] == (0, 0, 102)
    assert len(presenters) == 1
  finally:
    display.close()


def test_fallback_releases_failed_texture_traceback_before_destroying_backend(raster_pygame, monkeypatch):
  textures = []
  closed = []

  class Texture:
    def update(self):
      raise RuntimeError("texture update failed")

  class Presenter:
    def __init__(self, _pygame, size, **_kwargs):
      self.size = size
      self.texture = None

    def get_size(self):
      return self.size

    def present(self, _frame, _controls):
      texture = Texture()
      textures.append(weakref.ref(texture))
      self.texture = texture
      texture.update()

    def close(self):
      self.texture = None
      assert textures[0]() is None, "failed update frames must not retain native textures when closing renderer"
      closed.append(True)

  monkeypatch.setattr(hdmi_display, "TexturePresenter", Presenter)
  display = _raster_display(raster_pygame, renderer_mode="auto")
  try:
    assert display.show_waiting()
    assert display._presenter is None
    assert closed == [True]
  finally:
    display.close()
