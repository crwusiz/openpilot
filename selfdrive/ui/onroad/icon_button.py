import time
import pyray as rl
from typing import Callable
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget


class IconButton(Widget):
  def __init__(self, icon_path: str, button_size: int, icon_size: int, bg_color: rl.Color = None):
    super().__init__()

    # Visual properties
    self._button_size = button_size
    self._icon_size = icon_size
    self._bg_color = bg_color or rl.Color(0, 0, 0, 166)
    self._texture = gui_app.texture(icon_path, icon_size, icon_size)
    self._rect = rl.Rectangle(0, 0, icon_size, icon_size)

    # State
    self._is_active = False
    self._opacity = 1.0

    # Hold mechanism (like ExpButton)
    self._hold_duration = 2.0  # seconds
    self._held_state: bool | None = None
    self._hold_end_time: float | None = None

    # Interaction callback
    self._on_click: Callable[[], None] | None = None

  def set_rect(self, rect: rl.Rectangle) -> None:
    self._rect = rect

  def set_active(self, active: bool) -> None:
    self._is_active = active

  def set_opacity(self, opacity: float) -> None:
    self._opacity = max(0.0, min(1.0, opacity))

  def set_on_click(self, callback: Callable[[], None]) -> None:
    self._on_click = callback

  def _handle_mouse_release(self, _):
    super()._handle_mouse_release(_)
    if self._on_click and self._is_click_allowed():
      # Execute callback
      self._on_click()

      # Hold new state temporarily (like ExpButton)
      self._held_state = not self._is_active
      self._hold_end_time = time.monotonic() + self._hold_duration

  def _is_click_allowed(self) -> bool:
    return True

  def _render(self, rect: rl.Rectangle) -> None:
    center_x = int(self._rect.x + self._rect.width // 2)
    center_y = int(self._rect.y + self._rect.height // 2)

    # Adjust opacity if pressed (like ExpButton)
    alpha = int(self._opacity * (180 if self.is_pressed else 255))

    radius = self._button_size / 2
    rl.draw_circle(center_x, center_y, radius, self._bg_color)

    # Draw icon with opacity
    color = rl.Color(255, 255, 255, alpha)
    rl.draw_texture(
      self._texture,
      center_x - self._texture.width // 2,
      center_y - self._texture.height // 2,
      color
    )

  def _get_held_or_actual_state(self) -> bool:
    now = time.monotonic()
    if self._hold_end_time and now < self._hold_end_time:
      return self._held_state

    if self._hold_end_time and now >= self._hold_end_time:
      self._hold_end_time = self._held_state = None

    return self._is_active


class RotatableIconButton(IconButton):
  def __init__(self, icon_path: str, button_size: int, icon_size: int, bg_color: rl.Color = None):
    super().__init__(icon_path, button_size, icon_size, bg_color)
    self._rotation = 0.0

  def set_rotation(self, rotation: float) -> None:
    self._rotation = rotation

  def _render(self, rect: rl.Rectangle) -> None:
    center_x = int(self._rect.x + self._rect.width // 2)
    center_y = int(self._rect.y + self._rect.height // 2)

    # Adjust opacity if pressed
    alpha = int(self._opacity * (180 if self.is_pressed else 255))

    radius = self._button_size / 2
    rl.draw_circle(center_x, center_y, radius, self._bg_color)

    # Draw rotated icon with opacity
    color = rl.Color(255, 255, 255, alpha)
    rl.draw_texture_pro(
      self._texture,
      rl.Rectangle(0, 0, self._texture.width, self._texture.height),
      rl.Rectangle(center_x, center_y, self._icon_size, self._icon_size),
      rl.Vector2(self._icon_size / 2, self._icon_size / 2),
      self._rotation,
      color
    )


class ToggleIconButton(IconButton):
  def __init__(self, icon_on_path: str, icon_off_path: str, button_size: int, icon_size: int, bg_color: rl.Color = None):
    super().__init__(icon_on_path, button_size, icon_size, bg_color)
    self._texture_on = self._texture
    self._texture_off = gui_app.texture(icon_off_path, icon_size, icon_size)

  def _render(self, rect: rl.Rectangle) -> None:
    center_x = int(self._rect.x + self._rect.width // 2)
    center_y = int(self._rect.y + self._rect.height // 2)

    # Adjust opacity if pressed
    alpha = int(self._opacity * (180 if self.is_pressed else 255))

    radius = self._button_size / 2
    rl.draw_circle(center_x, center_y, radius, self._bg_color)

    # Choose texture based on held or actual state
    texture = self._texture_on if self._get_held_or_actual_state() else self._texture_off

    # Draw icon with opacity
    color = rl.Color(255, 255, 255, alpha)
    rl.draw_texture(
      texture,
      center_x - texture.width // 2,
      center_y - texture.height // 2,
      color
    )


class IconGroup:
  def __init__(self):
    self._buttons: list[IconButton] = []

  def add_button(self, button: IconButton) -> None:
    self._buttons.append(button)

  def render_horizontal(self, start_x: float, y: float, spacing: float, from_right: bool = False) -> None:
    direction = -1 if from_right else 1
    current_x = start_x

    for button in self._buttons:
      button.set_rect(rl.Rectangle(
        current_x - (button._icon_size / 2 if not from_right else button._icon_size / 2),
        y - button._icon_size / 2,
        button._icon_size,
        button._icon_size
      ))
      button.render(button._rect)
      current_x += (button._icon_size + spacing) * direction

  def update_all(self) -> None:
    for button in self._buttons:
      button.update()

  def is_any_pressed(self) -> bool:
    return any(button.is_pressed for button in self._buttons)
