import ctypes
import subprocess
import threading
from types import SimpleNamespace

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
                        pygame_module=pygame, touch_handler=touches.append)
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
  display = HdmiDisplay(384, 96, pygame_module=pygame, fullscreen=False)
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
