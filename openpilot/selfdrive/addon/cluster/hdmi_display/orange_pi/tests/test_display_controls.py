import math
from types import SimpleNamespace

import pytest

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
  return _touch(controls, controls.pygame.FINGERUP, point, finger_id) or changed


def _swipe(controls, *, rightward=True, finger_id=7):
  start, end = ((0.15, 0.5), (0.85, 0.5)) if rightward else ((0.85, 0.5), (0.15, 0.5))
  _touch(controls, controls.pygame.FINGERDOWN, start, finger_id)
  _touch(controls, controls.pygame.FINGERMOTION, end, finger_id)
  return _touch(controls, controls.pygame.FINGERUP, end, finger_id)


def _target(controls, name):
  left, top, width, height = controls.layout[name]
  return (left + width / 2) / controls.logical_size[0], (top + height / 2) / controls.logical_size[1]


def test_default_brightness_is_eighty_percent_and_menu_is_hidden(controls):
  state, _, _ = controls
  assert state.brightness == 80
  assert state.brightness_factor == 0.8
  assert state.tint_color == (204, 204, 204)
  assert not state.screen_off
  assert not state.menu_visible
  assert state.render_menu() is None


def test_action_requires_peer_and_a_complete_button_tap(controls):
  state, pygame, _ = controls
  assert _swipe(state)
  assert not _tap(state, _target(state, "update"))
  assert state.pending_action is None
  state.set_actions_available(True)
  state.set_action_status({"state": "idle", "pi_update_available": True})
  point = _target(state, "update")
  assert not _touch(state, pygame.FINGERDOWN, point)
  assert state.take_action() is None
  assert not _touch(state, pygame.FINGERMOTION, point)
  assert _touch(state, pygame.FINGERUP, point)
  assert state.take_action() == "pi_update"
  assert state.action_busy and state.take_action() is None
  assert not _tap(state, point)


@pytest.mark.parametrize("height,rotate_180", [(480, False), (480, True), (720, False), (720, True)])
@pytest.mark.parametrize("tap_y", [325, 361, 397])
def test_park_button_only_dispatches_on_release_in_the_displayed_hitbox(controls, height, rotate_180, tap_y):
  state, pygame, _ = controls
  state.set_actions_available(True)
  state.set_size((1920, height))
  state.set_park_available(True)
  assert _swipe(state, rightward=False) and state.park_visible
  state.set_park_touch((1920, height), rotate_180)
  x, y = 1621 / 1920, (tap_y + (height - 480) / 2) / height
  point = (1 - x, 1 - y) if rotate_180 else (x, y)
  assert state.park_log_upload_status() == (True, None)

  assert not _touch(state, pygame.FINGERDOWN, point)
  assert state.pending_action is None and not state.menu_visible
  state.set_park_touch((1920, height), rotate_180)
  assert not _touch(state, pygame.FINGERUP, point, finger_id=8)
  assert _touch(state, pygame.FINGERUP, point)
  assert state.pending_action == "park_upload_logs" and not state.menu_visible
  assert state.park_log_upload_status() == (False, "checking")
  assert state.take_action() == "park_upload_logs"
  assert not _touch(state, pygame.FINGERDOWN, point)
  assert not _touch(state, pygame.FINGERUP, point)
  assert state.take_action() is None

  old_x, old_y = x, (110 + (height - 480) / 2) / height
  old_point = (1 - old_x, 1 - old_y) if rotate_180 else (old_x, old_y)
  assert not _tap(state, old_point)
  assert state.park_visible and state.take_action() is None


@pytest.mark.parametrize("interrupt", ["left_park", "disconnect", "drag", "screen_off"])
def test_park_button_discards_interrupted_gestures(controls, interrupt):
  state, pygame, _ = controls
  state.set_actions_available(True)
  state.set_park_available(True)
  assert _swipe(state, rightward=False)
  state.set_park_touch((1920, 480))
  point = 1621 / 1920, 361 / 480
  assert not _touch(state, pygame.FINGERDOWN, point)
  if interrupt == "left_park":
    state.set_park_available(False)
    state.set_park_touch((1920, 480))
    state.set_park_available(True)
  elif interrupt == "disconnect":
    state.set_actions_available(False)
    state.set_actions_available(True)
  elif interrupt == "drag":
    _touch(state, pygame.FINGERMOTION, (0.1, 0.5))
  else:
    state.turn_off()
  assert not _touch(state, pygame.FINGERUP, point)
  assert state.take_action() is None


@pytest.mark.parametrize("leave_park", ["gear_change", "swipe", "hidden_frame"])
def test_park_button_clears_an_unsent_upload_on_leaving_park(controls, leave_park):
  state, pygame, _ = controls
  state.set_actions_available(True)
  state.set_park_available(True)
  assert _swipe(state, rightward=False)
  state.set_park_touch((1920, 480))
  point = 1621 / 1920, 361 / 480
  _touch(state, pygame.FINGERDOWN, point)
  assert _touch(state, pygame.FINGERUP, point)
  if leave_park == "gear_change":
    state.set_park_available(False)
  elif leave_park == "swipe":
    assert _swipe(state)
  else:
    state.set_park_touch()
  assert state.take_action() is None and not state.action_busy
  assert not state.park_touch_active


def test_running_action_can_be_hidden_and_completion_remains_visible_for_the_timeout(controls):
  state, _, now = controls
  state.set_actions_available(True)
  state.set_action_status({"state": "idle", "pi_update_available": True})
  assert _swipe(state)
  _tap(state, _target(state, "update"))
  now[0] = 100.0
  assert not state.update() and state.menu_visible
  assert _swipe(state, rightward=False)
  assert not state.menu_visible and state.action_busy
  assert _swipe(state)
  state.set_action_status({"state": "succeeded", "action": "pi_update"})
  assert state.action_label("update")
  assert state.action_label("pull") == "Not checked"
  now[0] += state.menu_timeout - 0.01
  assert not state.update()
  now[0] += 0.02
  assert state.update() and not state.menu_visible


def test_status_updates_only_redraw_when_menu_is_visible(controls):
  state, _, _ = controls
  revision = state.revision
  state.set_status({"cpu": 25.0, "temperature": 54.2, "gpu": 40.0, "gpu_temperature": 58.7,
                    "fan_pwm": 60, "fan_rpm": None, "fps": 17.5})
  assert state.revision == revision
  assert _swipe(state)
  revision = state.revision
  state.set_status({**state.status, "fan_rpm": 3200})
  assert state.revision == revision + 1
  state.set_status(dict(state.status))
  assert state.revision == revision + 1


def test_horizontal_swipe_opens_and_closes_controls(controls):
  state, _, _ = controls
  state.set_actions_available(True)
  assert not _tap(state)
  assert not state.menu_visible
  assert _swipe(state)
  assert state.menu_visible
  assert "upload" not in state.layout and "close" not in state.layout
  assert not _tap(state, (0, 0))
  assert state.menu_visible and state.take_action() is None
  assert not _swipe(state)
  assert state.menu_visible
  assert _swipe(state, rightward=False)
  assert not state.menu_visible


@pytest.mark.parametrize("start,end", [
  ((0.4, 0.5), (0.46, 0.5)),
  ((0.2, 0.1), (0.33, 0.9)),
  ((0.5, 0.1), (0.5, 0.9)),
])
def test_short_and_vertical_gestures_do_not_open_menu(controls, start, end):
  state, pygame, _ = controls
  _touch(state, pygame.FINGERDOWN, start)
  _touch(state, pygame.FINGERMOTION, end)
  assert not _touch(state, pygame.FINGERUP, end)
  assert not (state.menu_visible or state.park_visible)


def test_park_swipes_require_availability_and_do_not_reopen_the_other_view(controls):
  state, _, _ = controls
  assert not _swipe(state, rightward=False)
  assert not state.park_visible
  state.set_park_available(True)
  assert not state.park_visible
  assert _swipe(state, rightward=False)
  assert state.park_visible and not state.menu_visible
  assert not _swipe(state, rightward=False)
  assert state.park_visible
  assert _swipe(state)
  assert not (state.park_visible or state.menu_visible)
  assert _swipe(state)
  assert state.menu_visible and not state.park_visible
  assert _swipe(state, rightward=False)
  assert not (state.menu_visible or state.park_visible)
  assert _swipe(state, rightward=False) and state.park_visible
  state.set_park_available(False)
  assert not state.park_visible


def test_swiping_right_from_the_park_button_closes_park_without_uploading(controls):
  state, pygame, _ = controls
  state.set_actions_available(True)
  state.set_park_available(True)
  assert _swipe(state, rightward=False)
  state.set_park_touch((1920, 480))
  start = (1450 / 1920, 361 / 480)
  end = (0.97, start[1])
  assert not _touch(state, pygame.FINGERDOWN, start)
  assert not _touch(state, pygame.FINGERMOTION, end)
  assert _touch(state, pygame.FINGERUP, end)
  assert not (state.menu_visible or state.park_visible)
  assert state.take_action() is None


@pytest.mark.parametrize("button", ["update", "pull", "brightness_down", "off"])
def test_swiping_from_a_control_button_cannot_trigger_it(controls, button):
  state, pygame, _ = controls
  state.set_actions_available(True)
  state.set_action_status({"state": "idle", "pi_update_available": True, "update_available": True})
  assert _swipe(state)
  start = _target(state, button)
  # Start on the control itself in either direction. The far-left power button
  # can only start a rightward swipe, which keeps the menu open.
  end = (0.85 if button == "off" else 0.12, start[1])
  assert not _touch(state, pygame.FINGERDOWN, start)
  assert not _touch(state, pygame.FINGERMOTION, end)
  assert _touch(state, pygame.FINGERUP, end) is (button != "off")
  assert state.take_action() is None and state.brightness == 80 and not state.screen_off


def test_waking_touch_restores_previous_brightness_without_opening_menu(controls):
  state, pygame, _ = controls
  state.set_brightness(35)
  assert _swipe(state)
  assert _tap(state, _target(state, "off"))
  assert state.screen_off
  assert state.brightness_factor == 0
  assert state.tint_color == (0, 0, 0)
  assert not state.menu_visible

  assert _touch(state, pygame.FINGERDOWN, _target(state, "off"))
  assert not state.screen_off
  assert state.brightness == 40
  assert not state.menu_visible
  assert not _touch(state, pygame.FINGERMOTION, _target(state, "brightness_down"))
  assert not _touch(state, pygame.FINGERUP)
  assert state.brightness == 40
  assert not state.menu_visible
  assert _swipe(state)
  assert state.menu_visible


@pytest.mark.parametrize("next_finger", [7, 42])
def test_controller_reconnect_after_off_can_wake_with_reused_or_new_finger_id(controls, next_finger):
  state, pygame, now = controls
  state.set_brightness(35)
  assert _swipe(state)
  _touch(state, pygame.FINGERDOWN, _target(state, "off"), finger_id=7)
  state.turn_off()
  # The device can lose an active contact while the output goes dark.
  now[0] += 100
  state.update()
  assert _touch(state, pygame.FINGERDOWN, _target(state, "off"), finger_id=next_finger)
  assert not state.screen_off
  assert not state.menu_visible
  assert state.brightness == 40
  _touch(state, pygame.FINGERUP, finger_id=next_finger)
  assert _swipe(state, finger_id=next_finger)
  assert state.menu_visible


def test_screen_off_gesture_motion_and_release_do_not_restore_screen(controls):
  state, pygame, _ = controls
  assert _swipe(state)
  assert _tap(state, _target(state, "off"))
  assert not _touch(state, pygame.FINGERMOTION, (0.5, 0.5))
  assert not _touch(state, pygame.FINGERUP, (0.5, 0.5))
  assert state.screen_off


@pytest.mark.parametrize("rightward", [False, True])
def test_waking_swipe_only_wakes_even_if_park_is_available(controls, rightward):
  state, pygame, _ = controls
  state.set_park_available(True)
  state.turn_off()
  start, end = ((0.15, 0.5), (0.85, 0.5)) if rightward else ((0.85, 0.5), (0.15, 0.5))
  assert _touch(state, pygame.FINGERDOWN, start)
  assert not _touch(state, pygame.FINGERMOTION, end)
  assert not _touch(state, pygame.FINGERUP, end)
  assert not (state.screen_off or state.park_visible or state.menu_visible)


@pytest.mark.parametrize("name,initial,expected", [
  ("brightness_down", 80, 70), ("brightness_up", 80, 90),
  ("brightness_down", 20, 10), ("brightness_down", 10, 10),
  ("brightness_up", 90, 100), ("brightness_up", 100, 100),
])
def test_brightness_button_steps_and_clamps(controls, name, initial, expected):
  state, _, _ = controls
  state.set_brightness(initial)
  assert _swipe(state)
  assert _tap(state, _target(state, name)) == (initial != expected)
  assert state.brightness == expected
  assert state.menu_visible


def test_moving_or_holding_a_brightness_press_does_not_adjust_it_again(controls):
  state, pygame, _ = controls
  assert _swipe(state)
  down = _target(state, "brightness_down")
  up = _target(state, "brightness_up")
  assert not _touch(state, pygame.FINGERDOWN, down)
  assert not _touch(state, pygame.FINGERMOTION, up)
  assert not _touch(state, pygame.FINGERDOWN, down)
  assert not _touch(state, pygame.FINGERUP, down)
  assert state.brightness == 80


def test_second_finger_cannot_change_brightness_or_turn_off_screen(controls):
  state, pygame, _ = controls
  assert _swipe(state)
  point = _target(state, "brightness_down")
  _touch(state, pygame.FINGERDOWN, point, finger_id=7)
  assert not _touch(state, pygame.FINGERDOWN, _target(state, "brightness_up"), finger_id=9)
  assert not _touch(state, pygame.FINGERDOWN, _target(state, "off"), finger_id=9)
  assert not _touch(state, pygame.FINGERUP, _target(state, "off"), finger_id=9)
  assert state.brightness == 80
  assert _touch(state, pygame.FINGERUP, point, finger_id=7)
  assert state.brightness == 70
  assert not state.screen_off


def test_release_over_power_does_not_activate_it(controls):
  state, pygame, _ = controls
  assert _swipe(state)
  left, top, _, height = state.layout["off"]
  y = (top + height / 2) / state.logical_size[1]
  start = ((left - 1) / state.logical_size[0], y)
  end = ((left + 1) / state.logical_size[0], y)
  assert not _touch(state, pygame.FINGERDOWN, start)
  assert not _touch(state, pygame.FINGERUP, end)
  assert not state.screen_off
  assert state.menu_visible


def test_idle_menu_hides_but_stays_open_while_touch_is_held(controls):
  state, pygame, now = controls
  assert _swipe(state)
  now[0] = state.menu_timeout - 0.01
  assert not state.update()
  _touch(state, pygame.FINGERDOWN, _target(state, "brightness_down"))
  now[0] += state.menu_timeout * 2
  assert not state.update()
  assert state.menu_visible
  _touch(state, pygame.FINGERUP)
  now[0] += state.menu_timeout
  assert state.update()
  assert not state.menu_visible


@pytest.mark.parametrize("point", [(float("nan"), 0.5), (0.5, float("inf"))])
def test_nonfinite_touch_coordinates_are_ignored(controls, point):
  state, pygame, _ = controls
  assert _swipe(state)
  assert not _touch(state, pygame.FINGERDOWN, point)
  assert not _touch(state, pygame.FINGERUP, point)
  assert state.brightness == 80
  assert state.menu_visible


def test_motion_and_unknown_events_cannot_open_menu(controls):
  state, pygame, _ = controls
  assert not _touch(state, pygame.FINGERMOTION)
  assert not _touch(state, pygame.FINGERUP)
  assert not _touch(state, 999)
  assert not state.menu_visible


@pytest.mark.parametrize("size", [(1920, 480), (1920, 720), (960, 240), (480, 1920)])
def test_menu_and_touch_targets_fit_logical_screen(size):
  pygame = SimpleNamespace(FINGERDOWN=1, FINGERMOTION=2, FINGERUP=3)
  state = DisplayControls(pygame, logical_size=size)
  assert state.layout["panel"] == (0, 0, *size)
  for name in ("brightness_down", "brightness_up", "off", "update", "pull"):
    left, top, width, height = state.layout[name]
    assert 0 <= left < left + width <= size[0]
    assert 0 <= top < top + height <= size[1]
  off_x, off_y = _target(state, "off")
  assert off_x < 0.1 and off_y < 0.2
  assert _target(state, "brightness_down")[0] < _target(state, "brightness_up")[0]
  assert _target(state, "pull")[1] < _target(state, "update")[1]
  assert _swipe(state)
  _tap(state, _target(state, "brightness_down"))
  assert state.brightness == 70


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
  assert _swipe(state)
  surface, position = state.render_menu()
  assert surface.get_size() == state.logical_size
  assert position == (0, 0)
  assert surface.get_at((0, 0)).a == 255
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


@pytest.mark.parametrize("korean", [False, True])
@pytest.mark.parametrize("temperature, color", [
  (None, (175, 201, 217)), (float("nan"), (175, 201, 217)),
  (59.9, (108, 211, 145)), (60, (238, 211, 105)), (69.9, (238, 211, 105)),
  (70, (246, 164, 80)), (79.9, (246, 164, 80)), (80, (241, 101, 101)),
])
def test_menu_renders_gauge_values_and_temperature_colors(raster_pygame, monkeypatch, korean, temperature, color):
  state = DisplayControls(raster_pygame, logical_size=(1920, 720) if korean else (1920, 480), brightness=80)
  state.set_status({"cpu": 99.0, "temperature": temperature, "gpu": 100.0, "gpu_temperature": 75,
                    "fan_rpm": 99999, "fan_pwm": 60, "fps": 60.0})
  state.set_actions_available(True)
  assert _swipe(state)
  state._font(34)
  if not korean:
    state._font_path = None
  original_label = state._label
  labels = {}

  def draw_label(surface, text, size, center, foreground):
    if text:
      bounds = state._font(size).render(text, True, foreground).get_rect(center=center)
      assert surface.get_rect().contains(bounds), text
      labels[text] = bounds, foreground
    original_label(surface, text, size, center, foreground)

  monkeypatch.setattr(state, "_label", draw_label)
  surface, position = state.render_menu()
  assert surface.get_size() == state.logical_size and position == (0, 0)
  cpu_temperature = "--" if temperature is None or not math.isfinite(temperature) else f"{temperature:.1f}°C"
  assert labels[cpu_temperature][1] == color
  assert labels["75.0°C"][1] == (246, 164, 80)
  for reading in ("99%", "100%", "99999 RPM", "출력 60%" if korean else "Power 60%", "80%"):
    assert reading in labels
  assert labels["99%"][0].right < labels["100%"][0].left
  assert labels["100%"][0].right < labels["99999 RPM"][0].left
  fps = labels["FPS 60.0"][0]
  assert fps.centerx < state.logical_size[0] / 4
  assert fps.top >= state.layout["cpu"][1] + state.layout["cpu"][3]


def test_missing_telemetry_has_independent_placeholders(raster_pygame, monkeypatch):
  state = DisplayControls(raster_pygame)
  assert _swipe(state)
  labels = []
  monkeypatch.setattr(state, "_label", lambda _surface, text, *_args: labels.append(text))
  state.render_menu()
  assert labels.count("--") >= 4
  assert "RPM 미수신" in labels and "출력 --" in labels and "FPS --" in labels


def test_fan_without_tachometer_still_displays_output_percent(raster_pygame, monkeypatch):
  state = DisplayControls(raster_pygame)
  state.set_status({"fan_pwm": 60})
  assert _swipe(state)
  labels = {}
  monkeypatch.setattr(state, "_label", lambda _surface, text, size, *_args: labels.update({text: size}))
  state.render_menu()
  assert labels["출력 60%"] > labels["RPM 미수신"]


def test_git_pull_touch_requires_commit_check_and_is_disabled_while_busy(controls):
  state, _, _ = controls
  state.set_actions_available(True)
  assert _swipe(state)
  assert not _tap(state, _target(state, "pull"))
  assert "check" not in state.layout
  state.set_action_status({"action": "commit_check", "state": "succeeded", "update_available": True,
                           "commit_compare": '"1234567" != "abcdef0"'})
  assert _tap(state, _target(state, "pull"))
  assert state.take_action() == "git_pull"
  state.set_action_status({"action": "git_pull", "state": "succeeded", "update_available": False})
  assert "tmux" in state.action_label("pull")
  assert state.action_label("update") == "Not checked"
  assert not _tap(state, _target(state, "pull"))


def test_failed_commit_recheck_disables_git_pull_and_displays_failure(controls):
  state, _, _ = controls
  state.set_actions_available(True)
  assert _swipe(state)
  state.set_action_status({"action": "commit_check", "state": "succeeded", "update_available": True,
                           "commit_state": "available", "commit_compare": f'"{"a" * 40}" != "{"b" * 40}"'})
  assert state.button_enabled("pull")
  state.set_action_status({"action": "commit_check", "state": "running", "update_available": False, "commit_state": "checking"})
  assert not _tap(state, _target(state, "pull"))
  state.set_action_status({"action": "commit_check", "state": "failed", "update_available": False,
                           "commit_state": "failed", "message": "Command failed (exit 124); check C4 dashboard logs"})
  assert not state.button_enabled("pull")
  assert not _tap(state, _target(state, "pull"))
  assert state.take_action() is None
  assert "Failed" in state.action_label("pull") and "exit 124" in state.action_label("pull")
  assert state.action_label("update") == "Not checked"


@pytest.mark.parametrize("operator, current_commit, expected_commit", [
  ("==", None, "1234567"),
  ("!=", "invalid", "1234567"),
  ("!=", None, "1234567"),
  ("!=", "7654321" + "c" * 33, "7654321"),
  (None, None, "--"),
  (None, "7654321" + "c" * 33, "7654321"),
])
@pytest.mark.parametrize("size", [(1920, 480), (1920, 720)])
def test_current_versions_and_status_fit_on_two_lines_in_each_action_button(
    raster_pygame, monkeypatch, operator, current_commit, expected_commit, size):
  state = DisplayControls(raster_pygame, logical_size=size, brightness=100)
  local = "1234567" + "a" * 33
  remote = local if operator == "==" else "abcdef0" + "b" * 33
  state.set_actions_available(True)
  status = {"action": "commit_check", "state": "succeeded", "update_available": operator == "!=",
            "commit_state": {"==": "current", "!=": "available"}.get(operator, "unknown"),
            "commit_compare": f'"{local}" {operator} "{remote}"' if operator else "",
            "pi_version_state": "checking", "pi_version": "20261122093000"}
  if current_commit is not None:
    status["current_commit"] = current_commit
  state.set_action_status(status)
  assert _swipe(state)
  expected = {"pull": f"({expected_commit})", "update": "(20261122093000)"}
  original_label = state._bounded_label
  labels = {}

  def draw_label(surface, text, size, position, max_width, *args, **kwargs):
    assert "C4" not in text
    assert remote[:7] not in text or remote == local
    assert state._font(size).size(text)[0] <= max_width
    labels[text] = position
    original_label(surface, text, size, position, max_width, *args, **kwargs)

  monkeypatch.setattr(state, "_bounded_label", draw_label)
  state.render_menu()
  for name, version in expected.items():
    left, top, width, height = state.layout[name]
    status = state.action_label(name, korean=True)
    version_x, version_y = labels[version]
    status_x, status_y = labels[status]
    assert left <= status_x < version_x <= left + width
    assert top < version_y < top + height / 2 < status_y <= top + height


@pytest.mark.parametrize("available", [False, True])
def test_pi_button_uses_its_own_package_difference(controls, available):
  state, _, _ = controls
  state.set_actions_available(True)
  state.set_action_status({"state": "idle", "update_available": False, "pi_update_available": available,
                           "pi_version_state": "available" if available else "current"})
  assert _swipe(state)
  assert state.button_enabled("update") is available
  assert state.button_enabled("pull") is False
  assert _tap(state, _target(state, "update")) is available
  assert state.take_action() == ("pi_update" if available else None)


@pytest.mark.parametrize("action, busy_button", [("commit_check", "pull"), ("git_pull", "pull"), ("pi_update", "update")])
def test_update_buttons_show_independent_current_available_and_checking_colors(controls, action, busy_button):
  state, _, _ = controls
  version_states = {"commit_state": "current", "pi_version_state": "available"}
  version_colors = {"pull": (30, 85, 53), "update": (112, 39, 44)}
  state.set_actions_available(True)
  state.set_action_status({"state": "idle", **version_states})
  assert {name: state.button_color(name) for name in version_colors} == version_colors

  state.set_action_status({"state": "running", "action": action, "update_available": True,
                           "pi_update_available": True, **version_states})
  for name, color in version_colors.items():
    assert state.button_color(name) == ((108, 87, 34) if name == busy_button else color)
    assert not state.button_enabled(name)


@pytest.mark.parametrize("version_state, label", [
  ("current", "Up to date"),
  ("available", "Update available"),
  ("failed", "Version check failed"),
  ("checking", "Checking version…"),
  ("unknown", "Not checked"),
])
def test_each_idle_button_describes_its_own_version_state(controls, version_state, label):
  state, _, _ = controls
  state.set_actions_available(True)
  state.set_action_status({"state": "idle", "commit_state": version_state, "pi_version_state": "current"})
  assert state.action_label("pull") == label
  assert state.action_label("update") == "Up to date"


@pytest.mark.parametrize("action, job_state, active_button", [
  ("commit_check", "checking", "pull"),
  ("git_pull", "running", "pull"),
  ("git_pull", "succeeded", "pull"),
  ("git_pull", "failed", "pull"),
  ("pi_update", "checking", "update"),
  ("pi_update", "running", "update"),
  ("pi_update", "succeeded", "update"),
  ("pi_update", "failed", "update"),
])
def test_action_progress_is_drawn_inside_its_own_button_with_both_versions(raster_pygame, monkeypatch,
                                                                      action, job_state, active_button):
  state = DisplayControls(raster_pygame)
  state.set_actions_available(True)
  state.set_action_status({"action": action, "state": job_state,
                           "commit_compare": '"1234567" != "abcdef0"', "pi_version": "20261122093000",
                           "commit_state": "available", "pi_version_state": "current"})
  assert _swipe(state)
  label = state.action_label(active_button, korean=True)
  other_button = "update" if active_button == "pull" else "pull"
  other_label = "최신 버전" if other_button == "update" else "업데이트 가능"
  assert label != other_label and state.action_label(other_button, korean=True) == other_label

  original_label = state._bounded_label
  visible_labels = []

  def draw_label(surface, text, size, position, max_width, *args, **kwargs):
    if text:
      visible_labels.append((text, position))
    original_label(surface, text, size, position, max_width, *args, **kwargs)

  monkeypatch.setattr(state, "_bounded_label", draw_label)
  state.render_menu()

  expected = [("(1234567)", "pull"), ("(20261122093000)", "update"), (label, active_button), (other_label, other_button)]
  assert sorted(text for text, _ in visible_labels) == sorted(text for text, _ in expected)
  for text, button in expected:
    position, = (position for visible_text, position in visible_labels if visible_text == text)
    assert raster_pygame.Rect(state.layout[button]).collidepoint(position)
