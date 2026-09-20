import pyray as rl
from collections.abc import Callable
from openpilot.system.ui.lib.application import MousePos
from openpilot.system.ui.widgets import Widget

from openpilot.selfdrive.ui import Colors


WIDTH, HEIGHT = 160, 80
BG_HEIGHT = 60
ANIMATION_SPEED = 8.0


class Toggle(Widget):
  def __init__(self, initial_state: bool = False, callback: Callable[[bool], None] | None = None):
    super().__init__()
    self._state = initial_state
    self._callback = callback
    self._enabled = True
    self._progress = 1.0 if initial_state else 0.0
    self._target = self._progress
    self._clicked = False

  def set_rect(self, rect: rl.Rectangle):
    self._rect = rl.Rectangle(rect.x, rect.y, WIDTH, HEIGHT)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if not self._enabled:
      return

    self._clicked = True
    self._state = not self._state
    self._target = 1.0 if self._state else 0.0
    if self._callback:
      self._callback(self._state)

  def get_state(self) -> bool:
    return self._state

  def set_state(self, state: bool):
    self._state = state
    self._target = 1.0 if state else 0.0

  def is_enabled(self):
    return self._enabled

  def update(self):
    if abs(self._progress - self._target) > 0.01:
      delta = rl.get_frame_time() * ANIMATION_SPEED
      self._progress += delta if self._progress < self._target else -delta
      self._progress = max(0.0, min(1.0, self._progress))

  def _render(self, rect: rl.Rectangle):
    self.update()

    if self._enabled:
      bg_color = self._blend_color(Colors.Toggle.OFF, Colors.Toggle.ON, self._progress)
      knob_color = Colors.Toggle.KNOB
    else:
      bg_color = self._blend_color(Colors.Toggle.DISABLED_OFF, Colors.Toggle.DISABLED_ON, self._progress)
      knob_color = Colors.Toggle.DISABLED_KNOB

    # Draw background
    bg_rect = rl.Rectangle(self._rect.x + 5, self._rect.y + 10, WIDTH - 10, BG_HEIGHT)
    rl.draw_rectangle_rounded(bg_rect, 1.0, 10, bg_color)

    # Draw knob
    knob_x = self._rect.x + HEIGHT / 2 + (WIDTH - HEIGHT) * self._progress
    knob_y = self._rect.y + HEIGHT / 2
    rl.draw_circle(int(knob_x), int(knob_y), HEIGHT / 2, knob_color)

    # TODO: use click callback
    clicked = self._clicked
    self._clicked = False
    return clicked

  def _blend_color(self, c1, c2, t):
    return rl.Color(int(c1.r + (c2.r - c1.r) * t), int(c1.g + (c2.g - c1.g) * t), int(c1.b + (c2.b - c1.b) * t), 255)
