from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import display_controls
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.display_controls import DisplayControls


@pytest.fixture
def controls():
  now = [0.0]
  pygame = SimpleNamespace(FINGERDOWN=1, FINGERMOTION=2, FINGERUP=3)
  controls = DisplayControls(pygame, clock=lambda: now[0])
  return controls, pygame, now


def _touch(controls, event_type, point=(0.5, 0.5), finger_id=7):
  return controls.handle_touch({"type": event_type, "x": point[0], "y": point[1], "finger_id": finger_id})


def _tap(controls, point=(0.5, 0.5), finger_id=7):
  changed = _touch(controls, controls.pygame.FINGERDOWN, point, finger_id)
  _touch(controls, controls.pygame.FINGERUP, point, finger_id)
  return changed


def _target(controls, name, x_fraction=0.5):
  left, top, width, height = controls.layout[name]
  return (left + width * x_fraction) / controls.logical_size[0], (top + height / 2) / controls.logical_size[1]


def test_default_brightness_is_eighty_percent_and_menu_is_hidden(controls):
  state, _, _ = controls
  assert state.brightness == 80
  assert state.brightness_factor == 0.8
  assert state.tint_color == (204, 204, 204)
  assert not state.screen_off
  assert not state.menu_visible
  assert state.render_menu() is None


def test_tap_opens_controls_and_close_or_outside_tap_dismisses_them(controls):
  state, _, _ = controls
  assert _tap(state)
  assert state.menu_visible
  assert _tap(state, _target(state, "close"))
  assert not state.menu_visible
  assert _tap(state)
  assert _tap(state, (0, 0))
  assert not state.menu_visible


def test_waking_touch_restores_previous_brightness_without_opening_menu(controls):
  state, pygame, _ = controls
  state.set_brightness(35)
  _tap(state)
  assert _tap(state, _target(state, "off"))
  assert state.screen_off
  assert state.brightness_factor == 0
  assert state.tint_color == (0, 0, 0)
  assert not state.menu_visible

  assert _touch(state, pygame.FINGERDOWN, _target(state, "off"))
  assert not state.screen_off
  assert state.brightness == 40
  assert not state.menu_visible
  assert not _touch(state, pygame.FINGERMOTION, _target(state, "slider", 0))
  assert not _touch(state, pygame.FINGERUP)
  assert state.brightness == 40
  assert not state.menu_visible
  assert _tap(state)
  assert state.menu_visible


def test_new_touch_down_wakes_screen_even_if_previous_release_was_missing(controls):
  state, pygame, _ = controls
  _tap(state)
  assert _touch(state, pygame.FINGERDOWN, _target(state, "off"), finger_id=7)
  assert state.screen_off
  assert not _touch(state, pygame.FINGERMOTION, _target(state, "slider"), finger_id=7)
  assert state.screen_off
  assert _touch(state, pygame.FINGERDOWN, (0.5, 0.5), finger_id=9)
  assert not state.screen_off
  assert not state.menu_visible
  assert not _touch(state, pygame.FINGERUP, finger_id=7)
  assert not _touch(state, pygame.FINGERMOTION, _target(state, "slider", 0), finger_id=9)
  assert state.brightness == 80
  _touch(state, pygame.FINGERUP, finger_id=9)
  assert _tap(state, finger_id=9)
  assert state.menu_visible


@pytest.mark.parametrize("next_finger", [7, 42])
def test_controller_reconnect_after_off_can_wake_with_reused_or_new_finger_id(controls, next_finger):
  state, pygame, now = controls
  state.set_brightness(35)
  _tap(state)
  _touch(state, pygame.FINGERDOWN, _target(state, "off"), finger_id=7)
  # The touch device disappears before UP, then is detected again much later.
  now[0] += 100
  state.update()
  assert _touch(state, pygame.FINGERDOWN, _target(state, "off"), finger_id=next_finger)
  assert not state.screen_off
  assert not state.menu_visible
  assert state.brightness == 40
  _touch(state, pygame.FINGERUP, finger_id=next_finger)
  assert _tap(state, finger_id=next_finger)
  assert state.menu_visible


def test_screen_off_gesture_motion_and_release_do_not_restore_screen(controls):
  state, pygame, _ = controls
  _tap(state)
  _touch(state, pygame.FINGERDOWN, _target(state, "off"))
  assert not _touch(state, pygame.FINGERMOTION, (0.5, 0.5))
  assert not _touch(state, pygame.FINGERUP, (0.5, 0.5))
  assert state.screen_off


@pytest.mark.parametrize("fraction, expected", [(0, 10), (0.5, 60), (1, 100)])
def test_slider_tap_changes_brightness(controls, fraction, expected):
  state, _, _ = controls
  _tap(state)
  assert _tap(state, _target(state, "slider", fraction))
  assert state.brightness == expected
  assert state.menu_visible


def test_slider_drag_stays_active_outside_hit_box_and_clamps_brightness(controls):
  state, pygame, _ = controls
  _tap(state)
  _touch(state, pygame.FINGERDOWN, _target(state, "slider", 0.5))
  assert _touch(state, pygame.FINGERMOTION, (2, -1))
  assert state.brightness == 100
  assert _touch(state, pygame.FINGERMOTION, (-1, 2))
  assert state.brightness == 10
  _touch(state, pygame.FINGERUP)
  assert not _touch(state, pygame.FINGERMOTION, (1, 0))
  assert state.brightness == 10


def test_only_active_slider_finger_changes_brightness(controls):
  state, pygame, _ = controls
  _tap(state)
  _touch(state, pygame.FINGERDOWN, _target(state, "slider", 0.5), finger_id=7)
  assert not _touch(state, pygame.FINGERMOTION, (1, 0), finger_id=9)
  assert not _touch(state, pygame.FINGERDOWN, _target(state, "off"), finger_id=9)
  assert state.brightness == 60
  assert not state.screen_off


def test_releasing_outside_menu_does_not_activate_another_control(controls):
  state, pygame, _ = controls
  _tap(state)
  _touch(state, pygame.FINGERDOWN, _target(state, "slider", 0.5))
  assert not _touch(state, pygame.FINGERUP, _target(state, "off"))
  assert not state.screen_off
  assert state.menu_visible


def test_idle_menu_hides_but_a_held_slider_does_not(controls):
  state, pygame, now = controls
  _tap(state)
  now[0] = state.menu_timeout - 0.01
  assert not state.update()
  _touch(state, pygame.FINGERDOWN, _target(state, "slider"))
  now[0] += state.menu_timeout * 2
  assert not state.update()
  assert state.menu_visible
  _touch(state, pygame.FINGERUP)
  now[0] += state.menu_timeout
  assert state.update()
  assert not state.menu_visible


@pytest.mark.parametrize("point", [(float("nan"), 0.5), (0.5, float("inf"))])
def test_nonfinite_slider_coordinates_are_ignored(controls, point):
  state, pygame, _ = controls
  _tap(state)
  _touch(state, pygame.FINGERDOWN, _target(state, "slider", 0.5))
  assert not _touch(state, pygame.FINGERMOTION, point)
  assert state.brightness == 60


def test_motion_and_unknown_events_cannot_open_menu(controls):
  state, pygame, _ = controls
  assert not _touch(state, pygame.FINGERMOTION)
  assert not _touch(state, pygame.FINGERUP)
  assert not _touch(state, 999)
  assert not state.menu_visible


@pytest.mark.parametrize("size", [(1920, 480), (960, 240), (480, 1920)])
def test_menu_and_touch_targets_fit_logical_screen(size):
  pygame = SimpleNamespace(FINGERDOWN=1, FINGERMOTION=2, FINGERUP=3)
  state = DisplayControls(pygame, logical_size=size)
  for name in ("panel", "slider", "off", "close"):
    left, top, width, height = state.layout[name]
    assert 0 <= left < left + width <= size[0]
    assert 0 <= top < top + height <= size[1]
  _tap(state)
  _tap(state, _target(state, "slider", 0.5))
  assert state.brightness == 60


def test_resize_invalidates_menu_and_preserves_brightness(controls):
  state, _, _ = controls
  state.set_brightness(40)
  revision = state.revision
  assert state.set_size((960, 240))
  assert state.revision == revision + 1
  assert state.brightness == 40
  assert not state.set_size((960, 240))


@pytest.fixture
def raster_pygame(monkeypatch):
  monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", "1")
  monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
  pygame = pytest.importorskip("pygame")
  pygame.display.init()
  pygame.display.set_mode((100, 100))
  monkeypatch.setattr(display_controls.os.path, "isfile", lambda _path: False)
  monkeypatch.setattr(pygame.font, "match_font", lambda _name: None)
  yield pygame
  pygame.display.quit()


def test_software_dimming_matches_texture_tint_and_off_is_black(raster_pygame):
  pygame = raster_pygame
  state = DisplayControls(pygame)
  raw = pygame.Surface((4, 2))
  raw.fill((255, 100, 50))
  output = raw.copy()
  state.apply_dimming(output)
  assert tuple(output.get_at((0, 0)))[:3] == (204, 80, 40)
  assert tuple(raw.get_at((0, 0)))[:3] == (255, 100, 50)

  state.set_brightness(100)
  output = raw.copy()
  state.apply_dimming(output)
  assert tuple(output.get_at((0, 0)))[:3] == (255, 100, 50)
  state.turn_off()
  state.apply_dimming(output)
  assert not any(pygame.image.tobytes(output, "RGB"))


def test_menu_surface_is_cached_and_only_changed_state_redraws(raster_pygame):
  state = DisplayControls(raster_pygame)
  _tap(state)
  surface, position = state.render_menu()
  assert surface.get_size() == state.layout["panel"][2:]
  assert position == state.layout["panel"][:2]
  assert any(raster_pygame.image.tobytes(surface, "RGB"))
  assert state.render_menu()[0] is surface
  state.set_brightness(35)
  darker, same_position = state.render_menu()
  assert darker is not surface
  assert same_position == position
  assert sum(raster_pygame.image.tobytes(darker, "RGB")) < sum(raster_pygame.image.tobytes(surface, "RGB"))
  assert state.render_menu()[0] is darker
  state.turn_off()
  assert state.render_menu() is None
