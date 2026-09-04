from types import SimpleNamespace

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
