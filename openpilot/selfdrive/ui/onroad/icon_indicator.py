import pyray as rl

from openpilot.system.ui.lib.application import gui_app


class IconIndicator:
  def __init__(self, icon_path: str, background_size: int, icon_size: int, background_color: rl.Color | None = None):
    self._background_size = background_size
    self._icon_size = icon_size
    self._background_color = background_color or rl.Color(0, 0, 0, 166)
    self._texture = gui_app.texture(icon_path, icon_size, icon_size)
    self._rect = rl.Rectangle(0, 0, icon_size, icon_size)
    self._opacity = 1.0

  @property
  def icon_size(self) -> int:
    return self._icon_size

  def set_rect(self, rect: rl.Rectangle) -> None:
    self._rect = rect

  def set_texture(self, texture: rl.Texture) -> None:
    self._texture = texture

  def set_opacity(self, opacity: float) -> None:
    self._opacity = max(0.0, min(1.0, opacity))

  def render(self) -> None:
    center_x = int(self._rect.x + self._rect.width / 2)
    center_y = int(self._rect.y + self._rect.height / 2)

    rl.draw_circle(center_x, center_y, self._background_size / 2, self._background_color)
    rl.draw_texture(
      self._texture,
      center_x - self._texture.width // 2,
      center_y - self._texture.height // 2,
      rl.Color(255, 255, 255, int(self._opacity * 255)),
    )


class RotatableIconIndicator(IconIndicator):
  def __init__(self, icon_path: str, background_size: int, icon_size: int, background_color: rl.Color | None = None):
    super().__init__(icon_path, background_size, icon_size, background_color)
    self._rotation = 0.0

  def set_rotation(self, rotation: float) -> None:
    self._rotation = rotation

  def render(self) -> None:
    center_x = int(self._rect.x + self._rect.width / 2)
    center_y = int(self._rect.y + self._rect.height / 2)

    rl.draw_circle(center_x, center_y, self._background_size / 2, self._background_color)
    rl.draw_texture_pro(
      self._texture,
      rl.Rectangle(0, 0, self._texture.width, self._texture.height),
      rl.Rectangle(center_x, center_y, self._icon_size, self._icon_size),
      rl.Vector2(self._icon_size / 2, self._icon_size / 2),
      self._rotation,
      rl.Color(255, 255, 255, int(self._opacity * 255)),
    )


class IconIndicatorGroup:
  def __init__(self):
    self._indicators: list[IconIndicator] = []

  def add_indicator(self, indicator: IconIndicator) -> None:
    self._indicators.append(indicator)

  def render_horizontal(self, start_x: float, y: float, spacing: float) -> None:
    current_x = start_x

    for indicator in self._indicators:
      indicator.set_rect(rl.Rectangle(
        current_x - indicator.icon_size / 2,
        y - indicator.icon_size / 2,
        indicator.icon_size,
        indicator.icon_size,
      ))
      indicator.render()
      current_x += indicator.icon_size + spacing
