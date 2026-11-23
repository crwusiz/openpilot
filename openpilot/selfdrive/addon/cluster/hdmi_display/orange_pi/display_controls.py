import math
import re
import time
from pathlib import Path

try:
  from openpilot.selfdrive.addon.cluster.cluster_render_support import park_upload_button_bounds
except ImportError:
  from cluster_render_support import park_upload_button_bounds


DEFAULT_BRIGHTNESS = 80
MIN_BRIGHTNESS = 10
BRIGHTNESS_STEP = 10
MENU_TIMEOUT_SECONDS = 8.0
SWIPE_WIDTH_FRACTION = 0.12
TAP_SLOP = 24

MENU_BACKGROUND = (11, 17, 29)
CARD_BACKGROUND = (20, 29, 46)
CARD_BORDER = (42, 56, 78)
GAUGE_TRACK = (48, 63, 86)
TEXT_COLOR = (238, 246, 255)
MUTED_TEXT = (164, 183, 207)
USAGE_COLOR = (97, 179, 255)
FAN_COLOR = (87, 219, 195)
BRIGHTNESS_COLOR = (255, 205, 113)
UPDATE_ACTIONS = {"pull": ("commit_check", "git_pull"), "update": ("pi_update",)}


def cluster_font_path():
  for directory in Path(__file__).resolve().parents:
    path = directory / "assets" / "fonts" / "NotoSansKR-Bold.ttf"
    if path.is_file():
      return str(path)
  raise FileNotFoundError("Required cluster font missing: assets/fonts/NotoSansKR-Bold.ttf")


def _quantize_brightness(value):
  value = BRIGHTNESS_STEP * math.floor(float(value) / BRIGHTNESS_STEP + 0.5)
  return max(MIN_BRIGHTNESS, min(100, value))


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
    self.brightness = _quantize_brightness(brightness)
    self.screen_off = False
    self.menu_visible = False
    self.park_visible = False
    self._park_available = False
    self.revision = 0
    self._last_interaction = self.clock()
    self._active_finger = None
    self._touch_start = None
    self._tap_moved = False
    self._park_upload_target = None
    self._park_upload_pressed = False
    self._menu_surface = None
    self._menu_revision = None
    self._fonts = {}
    self._font_path = None
    self._font_checked = False
    self.status = {}
    self.actions_available = False
    self.action_busy = False
    self.action_status = {"state": "idle"}
    self._pending_action = None
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
    self._active_finger = None
    self._touch_start = None
    self.set_park_touch()
    self._layout = self._make_layout()
    self._fonts.clear()
    self.revision += 1
    return True

  def _make_layout(self):
    width, height = self.logical_size
    scale = min(width / 1920, height / 480)
    left = (width - round(1920 * scale)) // 2
    top = (height - round(480 * scale)) // 2

    def rect(x, y, w, h):
      return (left + round(x * scale), top + round(y * scale),
              max(1, round(w * scale)), max(1, round(h * scale)))

    edge, button = round(24 * scale), max(1, round(64 * scale))
    return {
      "panel": (0, 0, width, height),
      "off": (edge, edge, button, button),
      "cpu": rect(47, 104, 298, 284),
      "gpu": rect(371, 104, 298, 284),
      "fan": rect(695, 104, 298, 284),
      "brightness": rect(1019, 104, 298, 284),
      "brightness_down": rect(1096, 281, 64, 60),
      "brightness_up": rect(1176, 281, 64, 60),
      "pull": rect(1371, 123, 500, 109),
      "update": rect(1371, 252, 500, 109),
      "scale": scale,
    }

  @staticmethod
  def _contains(rect, point):
    left, top, width, height = rect
    return left <= point[0] <= left + width and top <= point[1] <= top + height

  def _changed(self):
    self.revision += 1
    return True

  def _panel_changed(self):
    # Telemetry updates must not redraw live video while controls are hidden.
    if self.menu_visible:
      self._changed()

  def set_status(self, status):
    if status != self.status:
      self.status = dict(status)
      self._panel_changed()

  def set_actions_available(self, available):
    if self.actions_available != bool(available):
      self.actions_available = bool(available)
      if not self.actions_available:
        self._cancel_park_press()
      self._panel_changed()

  def set_park_touch(self, frame_size=None, rotate_180=False):
    target = None
    if frame_size is not None:
      width, height = frame_size
      left, top, right, bottom = park_upload_button_bounds(frame_size)
      button_width, button_height = right - left, bottom - top
      if rotate_180:
        left, top = width - right, height - bottom
      target = left / width, top / height, button_width / width, button_height / height
    if target != self._park_upload_target:
      self._park_upload_target = target
      self._cancel_park_press()

  def set_park_available(self, available):
    self._park_available = bool(available)
    if not self._park_available and self.park_visible:
      self.park_visible = False
      self._touch_start = None
      self.set_park_touch()

  def _cancel_park_press(self):
    self._park_upload_pressed = False
    if self._pending_action == "park_upload_logs":
      self._pending_action = None
      self.set_action_status({**self.action_status, "action": None, "state": "idle"})

  @property
  def park_touch_active(self):
    return self.park_visible and self._park_upload_target is not None and self.actions_available and not self.screen_off

  def park_log_upload_status(self):
    state = self.action_status.get("state") if self.action_status.get("action") == "park_upload_logs" else None
    return self.actions_available and not self.action_busy, state

  def set_action_status(self, status):
    if status is None or status == self.action_status:
      return
    was_busy = self.action_busy
    self.action_status = dict(status)
    self.action_busy = status.get("state") in ("running", "checking")
    if was_busy and not self.action_busy:
      self._last_interaction = self.clock()
    self._panel_changed()

  @property
  def pending_action(self):
    return self._pending_action

  def take_action(self):
    action, self._pending_action = self._pending_action, None
    return action

  def _request_action(self, action):
    if action not in ("pi_update", "git_pull", "park_upload_logs") or self.action_busy or not self.actions_available:
      return False
    if action == "park_upload_logs" and not self.park_touch_active:
      return False
    if action == "git_pull" and not self.action_status.get("update_available"):
      return False
    if action == "pi_update" and not self.action_status.get("pi_update_available"):
      return False
    self._pending_action = action
    self.action_busy = True
    self.action_status = {**self.action_status, "action": action, "state": "checking"}
    return self._changed()

  def button_enabled(self, name):
    if name not in ("pull", "update"):
      return True
    if not self.actions_available or self.action_busy:
      return False
    key = "update_available" if name == "pull" else "pi_update_available"
    return self.action_status.get(key, False)

  def button_color(self, name):
    if name not in ("pull", "update"):
      return (38, 46, 58) if self.button_enabled(name) else (27, 31, 37)
    if self.action_busy and self.action_status.get("action") in UPDATE_ACTIONS[name]:
      return 108, 87, 34
    colors = {"current": (30, 85, 53), "available": (112, 39, 44), "failed": (82, 40, 44)}
    return colors.get(self._version_state(name), (87, 74, 36))

  def _version_state(self, name):
    key = "commit_state" if name == "pull" else "pi_version_state"
    return self.action_status.get(key, "unknown")

  def _current_commit(self):
    commit = self.action_status.get("current_commit")
    if isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{7,40}", commit):
      return commit[:7]
    comparison = self.action_status.get("commit_compare")
    match = re.fullmatch(r'"([a-f0-9]{7,40})" (?:==|!=) "[a-f0-9]{7,40}"', comparison) if isinstance(comparison, str) else None
    return match[1][:7] if match else "--"

  def set_brightness(self, value):
    value = _quantize_brightness(value)
    if self.brightness == value:
      return False
    self.brightness = value
    return self._changed()

  def turn_off(self):
    self.screen_off = True
    self.menu_visible = False
    self._cancel_park_press()
    return self._changed()

  def handle_touch(self, touch):
    """Classify a corrected SDL touch on release before activating a button."""
    event_type = touch["type"]
    if event_type not in (self.pygame.FINGERDOWN, self.pygame.FINGERMOTION, self.pygame.FINGERUP):
      return False
    finger = touch.get("finger_id", 0)
    point = float(touch["x"]), float(touch["y"])
    finite = all(math.isfinite(coord) for coord in point)
    if event_type == self.pygame.FINGERDOWN:
      if self.screen_off:
        # Consume the wake gesture even if the preceding finger release was lost.
        self._active_finger = finger
        self._touch_start = None
        self._last_interaction = self.clock()
        self.screen_off = False
        self.menu_visible = False
        return self._changed()
      if self._active_finger is not None or not finite:
        return False
      self._active_finger = finger
      self._touch_start = point
      self._tap_moved = False
      self._last_interaction = self.clock()
      self._park_upload_pressed = (self.park_touch_active and self.park_log_upload_status()[0]
                                   and self._contains(self._park_upload_target, point))
      return False
    if finger != self._active_finger:
      return False

    self._last_interaction = self.clock()
    start = self._touch_start
    width, height = self.logical_size
    if start is not None and finite:
      dx, dy = point[0] - start[0], point[1] - start[1]
      self._tap_moved |= math.hypot(dx * width, dy * height) > TAP_SLOP * self._layout["scale"]
    if event_type == self.pygame.FINGERMOTION:
      if self._park_upload_pressed and (not finite or not self._contains(self._park_upload_target, point)):
        self._park_upload_pressed = False
      return False
    self._active_finger = self._touch_start = None
    park_pressed, self._park_upload_pressed = self._park_upload_pressed, False
    if start is None or not finite:
      return False
    if abs(dx) >= SWIPE_WIDTH_FRACTION and abs(dx * width) >= 2 * abs(dy * height):
      return self._handle_swipe(dx > 0)
    if self._tap_moved:
      return False
    if self.menu_visible:
      return self._tap_menu((start[0] * width, start[1] * height), (point[0] * width, point[1] * height))
    if park_pressed and self.park_touch_active and self._contains(self._park_upload_target, point):
      return self._request_action("park_upload_logs")
    return False

  def _handle_swipe(self, rightward):
    if self.menu_visible:
      if not rightward:
        self.menu_visible = False
        return self._changed()
    elif self.park_visible:
      if rightward:
        self.park_visible = False
        self.set_park_touch()
        return True
    elif rightward:
      self.menu_visible = True
      return self._changed()
    elif self._park_available:
      self.park_visible = True
      return True
    return False

  def _tap_menu(self, start, end):
    def hit(name):
      rect = self._layout[name]
      return self._contains(rect, start) and self._contains(rect, end)

    if hit("brightness_down"):
      return self.set_brightness(self.brightness - BRIGHTNESS_STEP)
    if hit("brightness_up"):
      return self.set_brightness(self.brightness + BRIGHTNESS_STEP)
    for name, action in (("update", "pi_update"), ("pull", "git_pull")):
      if hit(name):
        return self._request_action(action)
    if hit("off"):
      return self.turn_off()
    return False

  def update(self):
    """Hide idle controls after the touch is released."""
    if self.menu_visible and not self.action_busy and self._active_finger is None and self.clock() - self._last_interaction >= self.menu_timeout:
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
      self._font_path = cluster_font_path()
      self._font_checked = True
    size = max(10, round(size))
    if size not in self._fonts:
      self._fonts[size] = pygame.font.Font(self._font_path, size)
    return self._fonts[size]

  def render_menu(self):
    """Return the cached, dimmed full-screen menu, or None when hidden."""
    if not self.menu_visible or self.screen_off:
      return None
    if self._menu_surface is not None and self._menu_revision == self.revision:
      return self._menu_surface, (0, 0)
    width, height = self.logical_size
    surface = self.pygame.Surface(self.logical_size)
    surface.fill(MENU_BACKGROUND)
    scale = self._layout["scale"]
    self._font(28 * scale)
    korean = self._font_path is not None

    self._draw_icon_button(surface, "off", (255, 146, 152))
    self._label(surface, "← 밀어 닫기" if korean else "← Swipe to close", 21 * scale,
                (width - round(108 * scale), round(56 * scale)), MUTED_TEXT)
    for key, title, temperature_key in (("cpu", "CPU", "temperature"), ("gpu", "GPU", "gpu_temperature")):
      center = self._draw_gauge_card(surface, key, title, USAGE_COLOR)
      self._draw_processor_gauge(surface, center, self._measurement(temperature_key), self._measurement(key), korean)

    center = self._draw_gauge_card(surface, "fan", "FAN", FAN_COLOR)
    fan_pwm = self._measurement("fan_pwm")
    self._draw_ring(surface, center, -44, 224, fan_pwm, FAN_COLOR, reverse=True)
    power = self._number(fan_pwm, "%")
    power_label = f"{'출력' if korean else 'Power'} {power}"
    fan_rpm = self._measurement("fan_rpm")
    reading = power_label if fan_rpm is None else f"{self._number(fan_rpm)} RPM"
    detail = ("RPM 미수신" if korean else "RPM unavailable") if fan_rpm is None else power_label
    self._label(surface, reading, 32 * scale, center, TEXT_COLOR)
    self._label(surface, detail, 21 * scale, (center[0], center[1] + round(39 * scale)),
                MUTED_TEXT if fan_rpm is None else FAN_COLOR)

    center = self._draw_gauge_card(surface, "brightness", "밝기" if korean else "Brightness", BRIGHTNESS_COLOR)
    self._draw_ring(surface, center, -44, 224, self.brightness, BRIGHTNESS_COLOR, reverse=True)
    self._label(surface, f"{self.brightness}%", 46 * scale, (center[0], center[1] - round(16 * scale)), TEXT_COLOR)
    self._draw_icon_button(surface, "brightness_down", BRIGHTNESS_COLOR, enabled=self.brightness > MIN_BRIGHTNESS)
    self._draw_icon_button(surface, "brightness_up", BRIGHTNESS_COLOR, enabled=self.brightness < 100)

    self._draw_action_button(surface, "pull", "GIT PULL", self._current_commit(), self.action_label("pull", korean))
    self._draw_action_button(surface, "update", "PI UPDATE", self.action_status.get("pi_version") or "--",
                             self.action_label("update", korean))

    fps_x, fps_y = round(110 * scale), height - round(29 * scale)
    self._draw_box(surface, (fps_x - round(86 * scale), fps_y - round(18 * scale),
                             round(172 * scale), round(36 * scale)), radius=18)
    self._label(surface, "FPS " + self._number(self._measurement("fps"), precision=1), 21 * scale,
                (fps_x, fps_y), USAGE_COLOR)
    self.apply_dimming(surface)
    self._menu_surface = surface
    self._menu_revision = self.revision
    return surface, (0, 0)

  def _measurement(self, key):
    value = self.status.get(key)
    return value if value is not None and math.isfinite(value) else None

  @staticmethod
  def _number(value, suffix="", precision=0):
    return "--" if value is None else f"{value:.{precision}f}{suffix}"

  @staticmethod
  def _temperature_color(temperature):
    if temperature is None:
      return (175, 201, 217)
    # Display thresholds only; fan and thermal policies are unchanged.
    return ((108, 211, 145) if temperature < 60 else (238, 211, 105) if temperature < 70
            else (246, 164, 80) if temperature < 80 else (241, 101, 101))

  def _draw_box(self, surface, rect, *, fill=CARD_BACKGROUND, border=CARD_BORDER, radius=22):
    scale = self._layout["scale"]
    draw = self.pygame.draw
    radius = max(1, round(radius * scale))
    draw.rect(surface, fill, rect, border_radius=radius)
    draw.rect(surface, border, rect, width=max(1, round(scale)), border_radius=radius)

  def _draw_icon_button(self, surface, name, color, *, enabled=True):
    x, y, width, height = self._layout[name]
    self._draw_box(surface, (x, y, width, height), radius=16)
    self._draw_icon(surface, name, (x + width // 2, y + height // 2), color if enabled else MUTED_TEXT, size=28)

  def _draw_gauge_card(self, surface, name, title, color):
    x, y, width, height = self._layout[name]
    self._draw_box(surface, (x, y, width, height))
    scale = self._layout["scale"]
    center_x = x + width // 2
    title_y = y + round(35 * scale)
    title_width = self._font(25 * scale).size(title)[0]
    icon_width = round(24 * scale)
    self._draw_icon(surface, name, (center_x - title_width // 2 - round(6 * scale), title_y), color, size=24)
    self._label(surface, title, 25 * scale, (center_x + icon_width // 2 + round(6 * scale), title_y), TEXT_COLOR)
    return center_x, y + round(157 * scale)

  def _draw_processor_gauge(self, surface, center, temperature, utilization, korean):
    scale = self._layout["scale"]
    color = self._temperature_color(temperature)
    self._draw_ring(surface, center, 109, 251, temperature, color, reverse=True)
    self._draw_ring(surface, center, -71, 71, utilization, USAGE_COLOR)
    x, y = center
    self.pygame.draw.line(surface, CARD_BORDER, (x, y - round(24 * scale)), (x, y + round(35 * scale)),
                          max(1, round(scale)))
    for side, value, caption, text_color in (
      (-1, self._number(temperature, "°C", 1), "온도" if korean else "Temp", color),
      (1, self._number(utilization, "%"), "사용량" if korean else "Load", TEXT_COLOR),
    ):
      value_x = x + round(side * 50 * scale)
      self._label(surface, value, 27 * scale, (value_x, y - round(6 * scale)), text_color)
      self._label(surface, caption, 17 * scale, (value_x, y + round(26 * scale)), MUTED_TEXT)

  def _draw_ring(self, surface, center, start_degrees, end_degrees, percent, color, *, reverse=False):
    from pygame import gfxdraw

    scale = self._layout["scale"]
    outer = max(1, round(104 * scale))
    thickness = max(1, round(10 * scale))
    inner = max(0, outer - thickness)
    x, y = center

    def point(angle, radius):
      return round(x + radius * math.cos(angle)), round(y - radius * math.sin(angle))

    def draw_arc(start, end, arc_color):
      segments = max(1, math.ceil((end - start) / math.radians(4)))
      angles = [start + (end - start) * index / segments for index in range(segments + 1)]
      points = [point(angle, outer) for angle in angles]
      points.extend(point(angle, inner) for angle in reversed(angles))
      gfxdraw.filled_polygon(surface, points, arc_color)
      gfxdraw.aapolygon(surface, points, arc_color)
      midpoint = (outer + inner) / 2
      cap = max(1, thickness // 2)
      for angle in (start, end):
        cap_x, cap_y = point(angle, midpoint)
        gfxdraw.filled_circle(surface, cap_x, cap_y, cap, arc_color)
        gfxdraw.aacircle(surface, cap_x, cap_y, cap, arc_color)

    start, end = math.radians(start_degrees), math.radians(end_degrees)
    draw_arc(start, end, GAUGE_TRACK)
    if percent is not None and percent > 0:
      sweep = (end - start) * min(percent / 100, 1)
      start, end = (end - sweep, end) if reverse else (start, start + sweep)
      draw_arc(start, end, color)

  def _draw_action_button(self, surface, name, title, version, status):
    x, y, width, height = self._layout[name]
    scale = self._layout["scale"]
    enabled = self.button_enabled(name)
    self._draw_box(surface, (x, y, width, height))
    icon_size = max(1, round(62 * scale))
    icon_x, icon_y = x + round(19 * scale), y + (height - icon_size) // 2
    self._draw_box(surface, (icon_x, icon_y, icon_size, icon_size), fill=self.button_color(name), radius=15)
    self._draw_icon(surface, name, (icon_x + icon_size // 2, icon_y + icon_size // 2),
                    TEXT_COLOR if enabled else MUTED_TEXT, size=29)
    label_x = icon_x + icon_size + round(20 * scale)
    color = TEXT_COLOR if enabled else MUTED_TEXT
    label = self._font(30 * scale).render(title, True, color)
    title_y = y + round(37 * scale)
    surface.blit(label, label.get_rect(midleft=(label_x, title_y)))
    text_right = x + width - round(14 * scale)
    version_x = label_x + label.get_width() + round(12 * scale)
    self._bounded_label(surface, f"({version})", 20 * scale, (version_x, title_y),
                        text_right - version_x, align="midleft")
    self._bounded_label(surface, status, 20 * scale, (label_x, y + round(74 * scale)),
                        text_right - label_x, align="midleft")

  def _draw_icon(self, surface, name, center, color, *, size):
    pygame = self.pygame
    x, y = center
    unit = size * self._layout["scale"] / 24
    stroke = max(1, round(2 * unit))

    def point(dx, dy):
      return round(x + dx * unit), round(y + dy * unit)

    def line(*points):
      pygame.draw.lines(surface, color, False, [point(*p) for p in points], stroke)

    def circle(radius, width):
      pygame.draw.circle(surface, color, center, max(1, round(radius * unit)), width)

    if name == "off":
      radius = max(1, round(10 * unit))
      pygame.draw.arc(surface, color, (x - radius, y - radius, 2 * radius, 2 * radius),
                      math.radians(123), math.radians(417), stroke)
      line((0, -12), (0, 0))
    elif name in ("brightness_down", "brightness_up"):
      direction = -1 if name == "brightness_down" else 1
      line((-direction * 4, -8), (direction * 4, 0), (-direction * 4, 8))
    elif name == "brightness":
      circle(5, stroke)
      for tick in range(8):
        angle = math.tau * tick / 8
        dx, dy = math.cos(angle), math.sin(angle)
        line((8 * dx, 8 * dy), (11 * dx, 11 * dy))
    elif name == "fan":
      circle(10, stroke)
      for blade in range(3):
        angle = math.tau * blade / 3
        dx, dy = math.cos(angle), math.sin(angle)
        line((0, 0), (4 * dx - 2 * dy, 4 * dy + 2 * dx), (6 * dx, 6 * dy))
      circle(2, 0)
    elif name == "pull":
      line((0, -10), (0, 5))
      line((-5, 0), (0, 5), (5, 0))
      line((-9, 5), (-9, 9), (9, 9), (9, 5))
    else:
      rect_x, rect_y = point(-7, -7)
      side = max(1, round(14 * unit))
      pygame.draw.rect(surface, color, (rect_x, rect_y, side, side), width=stroke, border_radius=stroke)
      for offset in (-4, 4):
        line((offset, -10), (offset, -7))
        line((offset, 7), (offset, 10))
        line((-10, offset), (-7, offset))
        line((7, offset), (10, offset))
      if name == "gpu":
        line((-3, 3), (-3, 0), (0, 0), (0, -3), (3, -3))
      elif name == "update":
        line((0, 3), (0, -3))
        line((-2, -1), (0, -3), (2, -1))
      else:
        pygame.draw.circle(surface, color, center, max(1, round(2 * unit)))

  def action_label(self, name, korean=False):
    state, action = self.action_status.get("state"), self.action_status.get("action")
    reason = self.action_status.get("reason")
    if action is None and (not self.actions_available or state == "unavailable"):
      if reason == "update_c4":
        return "대시보드 업데이트 필요" if korean else "Dashboard update required"
      return "연결 필요" if korean else "Connection required"
    if action not in UPDATE_ACTIONS[name]:
      return self._version_label(name, korean)
    if state == "checking":
      return "요청 확인 중…" if korean else "Checking request…"
    if state == "running":
      if action == "commit_check":
        return "커밋 확인 중…" if korean else "Checking commits…"
      if action == "git_pull":
        return "업데이트 시작 중…" if korean else "Starting update…"
      return "업데이트 중 · 완료 후 자동 재부팅" if korean else "Updating · auto reboot on completion"
    if state == "succeeded":
      if action == "git_pull":
        return "tmux에서 시작 · 완료 후 재시작" if korean else "Update started in tmux · restarts after update"
      if action == "pi_update":
        if self.action_status.get("unchanged"):
          return "변경 사항 없음" if korean else "Up to date · no changes"
        return "업데이트 완료 · 재부팅 예정" if korean else "Updated · reboot scheduled"
    if state in ("failed", "warning"):
      if reason == "update_c4":
        return "대시보드 업데이트 필요" if korean else "Dashboard update required"
      if reason == "busy":
        return "다른 작업 진행 중" if korean else "Another job is running"
      if state == "warning":
        label = "완료 · 경고" if korean else "Completed with warnings"
      else:
        label = "작업 실패" if korean else "Failed"
      message = self.action_status.get("message")
      return f"{label} · {message[:120]}" if message else label
    return self._version_label(name, korean)

  def _version_label(self, name, korean):
    labels = {
      "current": ("최신 버전", "Up to date"),
      "available": ("업데이트 가능", "Update available"),
      "checking": ("버전 확인 중…", "Checking version…"),
      "failed": ("버전 확인 실패", "Version check failed"),
    }
    return labels.get(self._version_state(name), ("버전 미확인", "Not checked"))[0 if korean else 1]

  def _label(self, surface, text, size, center, color):
    label = self._font(size).render(text, True, color)
    surface.blit(label, label.get_rect(center=center))

  def _bounded_label(self, surface, text, size, position, max_width, align="center"):
    font = self._font(size)
    if font.size(text)[0] > max_width:
      while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
      text += "…"
    label = font.render(text, True, (175, 195, 210))
    surface.blit(label, label.get_rect(**{align: position}))
