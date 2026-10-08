import math
import os
import time


DEFAULT_BRIGHTNESS = 80
MIN_BRIGHTNESS = 10
MENU_TIMEOUT_SECONDS = 8.0


class DisplayControls:
  """Local touch controls in the unrotated cluster coordinate system.

  brightness_factor can tint an SDL texture without a full-frame CPU overlay.
  apply_dimming is the Surface fallback; it modifies a freshly drawn destination
  in place. This controls image brightness, not an HDMI panel's backlight.
  """

  def __init__(self, pygame_module, logical_size=(1920, 480), brightness=DEFAULT_BRIGHTNESS,
               clock=time.monotonic, menu_timeout=MENU_TIMEOUT_SECONDS):
    self.pygame = pygame_module
    self.clock = clock
    self.menu_timeout = max(0.1, float(menu_timeout))
    self.logical_size = tuple(max(1, int(value)) for value in logical_size)
    self.brightness = max(MIN_BRIGHTNESS, min(100, int(brightness)))
    self.screen_off = False
    self.menu_visible = False
    self.revision = 0
    self._last_interaction = self.clock()
    self._active_finger = None
    self._dragging_slider = False
    self._menu_surface = None
    self._menu_revision = None
    self._fonts = {}
    self._font_path = None
    self._font_checked = False
    self._layout = self._make_layout()

  @property
  def brightness_factor(self):
    return 0.0 if self.screen_off else self.brightness / 100.0

  @property
  def tint_color(self):
    value = round(self.brightness_factor * 255)
    return value, value, value

  @property
  def layout(self):
    """Logical pixel rectangles for drawing and touch hit testing."""
    return self._layout

  def set_size(self, logical_size):
    size = tuple(max(1, int(value)) for value in logical_size)
    if size == self.logical_size:
      return False
    self.logical_size = size
    self._layout = self._make_layout()
    self._fonts.clear()
    self.revision += 1
    return True

  def _make_layout(self):
    width, height = self.logical_size
    scale = min(width / 1200, height / 430)
    panel_width, panel_height = max(1, round(1000 * scale)), max(1, round(340 * scale))
    left, top = (width - panel_width) // 2, (height - panel_height) // 2

    def rect(x, y, w, h):
      return (left + round(x * scale), top + round(y * scale),
              max(1, round(w * scale)), max(1, round(h * scale)))

    return {
      "panel": (left, top, panel_width, panel_height),
      "slider": rect(64, 116, 872, 72),
      "off": rect(64, 224, 392, 82),
      "close": rect(544, 224, 392, 82),
      "scale": scale,
    }

  @staticmethod
  def _contains(rect, point):
    left, top, width, height = rect
    return left <= point[0] <= left + width and top <= point[1] <= top + height

  def _changed(self):
    self.revision += 1
    return True

  def set_brightness(self, value):
    value = max(MIN_BRIGHTNESS, min(100, round(float(value))))
    if self.brightness == value:
      return False
    self.brightness = value
    return self._changed()

  def turn_off(self):
    self.screen_off = True
    self.menu_visible = False
    self._dragging_slider = False
    return self._changed()

  def _set_slider(self, x):
    left, _, width, _ = self._layout["slider"]
    fraction = max(0.0, min(1.0, (x - left) / width))
    return self.set_brightness(MIN_BRIGHTNESS + (100 - MIN_BRIGHTNESS) * fraction)

  def handle_touch(self, touch):
    """Consume corrected SDL finger events (normalized x/y, type, finger_id).

    A waking touch is consumed until its finger is released. A new DOWN wakes
    even if a controller reconnect or missing UP left a previous finger active.
    Motion and release from the screen-off gesture never wake the screen.
    """
    event_type = touch["type"]
    if event_type not in (self.pygame.FINGERDOWN, self.pygame.FINGERMOTION, self.pygame.FINGERUP):
      return False
    finger = touch.get("finger_id", 0)
    if event_type == self.pygame.FINGERDOWN:
      if self.screen_off:
        self._active_finger = finger
        self._dragging_slider = False
        self._last_interaction = self.clock()
        self.screen_off = False
        self.menu_visible = False
        return self._changed()
      if self._active_finger is not None:
        return False
      self._active_finger = finger
      self._last_interaction = self.clock()
      if not self.menu_visible:
        self.menu_visible = True
        return self._changed()
    elif finger != self._active_finger:
      return False

    if event_type == self.pygame.FINGERUP:
      self._active_finger = None
      self._dragging_slider = False
      self._last_interaction = self.clock()
      return False

    x, y = float(touch["x"]), float(touch["y"])
    if not math.isfinite(x) or not math.isfinite(y):
      return False
    point = x * self.logical_size[0], y * self.logical_size[1]
    self._last_interaction = self.clock()
    if event_type == self.pygame.FINGERMOTION:
      if self._dragging_slider and self.menu_visible:
        return self._set_slider(point[0])
      return False

    if self._contains(self._layout["slider"], point):
      self._dragging_slider = True
      return self._set_slider(point[0])
    if self._contains(self._layout["off"], point):
      return self.turn_off()
    if self._contains(self._layout["close"], point) or not self._contains(self._layout["panel"], point):
      self.menu_visible = False
      return self._changed()
    return False

  def update(self):
    """Hide idle controls, without dismissing a slider held by a finger."""
    if self.menu_visible and self._active_finger is None and self.clock() - self._last_interaction >= self.menu_timeout:
      self.menu_visible = False
      return self._changed()
    return False

  def apply_dimming(self, surface):
    """Dim a fresh destination once; retain raw frames for subsequent redraws."""
    if self.screen_off:
      surface.fill((0, 0, 0))
    elif self.brightness < 100:
      surface.fill(self.tint_color, special_flags=self.pygame.BLEND_RGB_MULT)

  def _font(self, size):
    pygame = self.pygame
    if not self._font_checked:
      pygame.font.init()
      self._font_path = next((path for path in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
      ) if os.path.isfile(path)), None)
      if self._font_path is None:
        self._font_path = pygame.font.match_font("notosanscjkkr,nanumgothic,malgungothic,applegothic")
      self._font_checked = True
    size = max(10, round(size))
    if size not in self._fonts:
      self._fonts[size] = pygame.font.Font(self._font_path, size)
    return self._fonts[size]

  def render_menu(self):
    """Return (cached menu Surface, logical position), or None when hidden.

    Upload this small Surface only when its identity changes. Draw it after
    dimming the frame so controls stay readable at low brightness. The Surface
    already contains the selected brightness's tint, limiting menu glare too.
    """
    if not self.menu_visible or self.screen_off:
      return None
    left, top, width, height = self._layout["panel"]
    if self._menu_surface is not None and self._menu_revision == self.revision:
      return self._menu_surface, (left, top)
    pygame = self.pygame
    scale = self._layout["scale"]
    surface = pygame.Surface((width, height))
    surface.fill((18, 22, 28))
    self._font(36 * scale)
    korean = self._font_path is not None
    title = f"화면 밝기 {self.brightness}%" if korean else f"Brightness {self.brightness}%"
    self._label(surface, title, 36 * scale, (width // 2, round(54 * scale)), (225, 232, 240))

    slider = self._local_rect(self._layout["slider"], left, top)
    center_y = slider[1] + slider[3] // 2
    track_height = max(4, round(10 * scale))
    pygame.draw.rect(surface, (65, 73, 84), (slider[0], center_y - track_height // 2, slider[2], track_height))
    fraction = (self.brightness - MIN_BRIGHTNESS) / (100 - MIN_BRIGHTNESS)
    selected_width = round(slider[2] * fraction)
    if selected_width:
      pygame.draw.rect(surface, (100, 180, 220), (slider[0], center_y - track_height // 2, selected_width, track_height))
    pygame.draw.circle(surface, (195, 220, 235), (slider[0] + selected_width, center_y), max(8, round(22 * scale)))
    self._label(surface, "10%", 22 * scale, (slider[0], round(204 * scale)), (150, 162, 175))
    self._label(surface, "100%", 22 * scale, (slider[0] + slider[2], round(204 * scale)), (150, 162, 175))
    for name, title in (("off", "화면 끄기" if korean else "Screen off"), ("close", "닫기" if korean else "Close")):
      rect = self._local_rect(self._layout[name], left, top)
      pygame.draw.rect(surface, (38, 46, 58), rect, border_radius=max(1, round(12 * scale)))
      self._label(surface, title, 30 * scale, (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2), (220, 229, 239))
    self.apply_dimming(surface)
    self._menu_surface = surface
    self._menu_revision = self.revision
    return surface, (left, top)

  @staticmethod
  def _local_rect(rect, left, top):
    return rect[0] - left, rect[1] - top, rect[2], rect[3]

  def _label(self, surface, text, size, center, color):
    label = self._font(size).render(text, True, color)
    surface.blit(label, label.get_rect(center=center))
