import math
import time
from pathlib import Path


DEFAULT_BRIGHTNESS = 80
MIN_BRIGHTNESS = 10
BRIGHTNESS_STEP = 10
MENU_TIMEOUT_SECONDS = 8.0


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
    self.revision = 0
    self._last_interaction = self.clock()
    self._active_finger = None
    self._dragging_slider = False
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
    self._layout = self._make_layout()
    self._fonts.clear()
    self.revision += 1
    return True

  def _make_layout(self):
    width, height = self.logical_size
    scale = min(width / 1920, height / 480)
    panel_width, panel_height = max(1, round(1840 * scale)), max(1, round(340 * scale))
    left, top = (width - panel_width) // 2, height - panel_height - round(12 * scale)

    def rect(x, y, w, h):
      return (left + round(x * scale), top + round(y * scale),
              max(1, round(w * scale)), max(1, round(h * scale)))

    return {
      "panel": (left, top, panel_width, panel_height),
      "slider": rect(390, 108, 1400, 64),
      "off": rect(40, 200, 420, 78),
      "update": rect(480, 200, 420, 78),
      "upload": rect(920, 200, 420, 78),
      "close": rect(1360, 200, 420, 78),
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
      self._panel_changed()

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
    if self.action_busy or not self.actions_available:
      return False
    self._pending_action = action
    self.action_busy = True
    self.action_status = {"action": action, "state": "checking"}
    return self._changed()

  def set_brightness(self, value):
    value = _quantize_brightness(value)
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
    for name, action in (("update", "pi_update"), ("upload", "upload_logs")):
      if self._contains(self._layout[name], point):
        return self._request_action(action)
    if self._contains(self._layout["off"], point):
      return self.turn_off()
    if self._contains(self._layout["close"], point) or not self._contains(self._layout["panel"], point):
      self.menu_visible = False
      return self._changed()
    return False

  def update(self):
    """Hide idle controls, without dismissing a slider held by a finger."""
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
    self._font(34 * scale)
    korean = self._font_path is not None
    labels = self.status_labels(korean)
    column_width = (width - 80 * scale) / len(labels)
    for index, (text, color) in enumerate(zip(labels, self.status_colors(), strict=True)):
      center_x = round(40 * scale + column_width * (index + 0.5))
      self._label(surface, text, 34 * scale, (center_x, round(48 * scale)), color)
    step = self.brightness // BRIGHTNESS_STEP
    title = f"밝기 {step}/10 · {self.brightness}%" if korean else f"Light {step}/10 · {self.brightness}%"
    self._label(surface, title, 34 * scale, (round(200 * scale), round(140 * scale)), (225, 232, 240))

    slider = self._local_rect(self._layout["slider"], left, top)
    center_y = slider[1] + slider[3] // 2
    track_height = max(4, round(10 * scale))
    pygame.draw.rect(surface, (65, 73, 84), (slider[0], center_y - track_height // 2, slider[2], track_height))
    fraction = (self.brightness - MIN_BRIGHTNESS) / (100 - MIN_BRIGHTNESS)
    selected_width = round(slider[2] * fraction)
    if selected_width:
      pygame.draw.rect(surface, (100, 180, 220), (slider[0], center_y - track_height // 2, selected_width, track_height))
    for value in range(MIN_BRIGHTNESS, 101, BRIGHTNESS_STEP):
      tick_x = slider[0] + round(slider[2] * (value - MIN_BRIGHTNESS) / (100 - MIN_BRIGHTNESS))
      pygame.draw.circle(surface, (150, 162, 175), (tick_x, center_y), max(3, round(7 * scale)))
    pygame.draw.circle(surface, (195, 220, 235), (slider[0] + selected_width, center_y), max(8, round(22 * scale)))
    buttons = (("off", "화면 끄기", "Screen off"), ("update", "Pi 업데이트", "Pi update"),
               ("upload", "로그 전송", "Upload logs"), ("close", "닫기", "Close"))
    for name, korean_title, english_title in buttons:
      rect = self._local_rect(self._layout[name], left, top)
      enabled = name not in ("update", "upload") or (self.actions_available and not self.action_busy)
      color = (38, 46, 58) if enabled else (27, 31, 37)
      pygame.draw.rect(surface, color, rect, border_radius=max(1, round(10 * scale)))
      self._label(surface, korean_title if korean else english_title, 36 * scale,
                  (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2), (220, 229, 239) if enabled else (100, 109, 119))
    self._label(surface, self.action_label(korean), 25 * scale, (width // 2, round(310 * scale)), (175, 195, 210))
    self.apply_dimming(surface)
    self._menu_surface = surface
    self._menu_revision = self.revision
    return surface, (left, top)

  def status_labels(self, korean=False):
    def number(key, suffix, precision=0):
      value = self.status.get(key)
      return "--" if value is None or not math.isfinite(value) else f"{value:.{precision}f}{suffix}"
    fan = number("fan_rpm", " RPM") if self.status.get("fan_rpm") is not None else number("fan_pwm", "% PWM")
    return (("CPU 사용률 " if korean else "CPU usage ") + number("cpu", "%"),
            ("CPU 온도 " if korean else "CPU temp ") + number("temperature", "°C", 1),
            ("GPU 사용률 " if korean else "GPU usage ") + number("gpu", "%"),
            ("GPU 온도 " if korean else "GPU temp ") + number("gpu_temperature", "°C", 1),
            "FAN " + fan, "FPS " + number("fps", "", 1))

  def status_colors(self):
    neutral = (175, 201, 217)
    def temperature_color(key):
      temperature = self.status.get(key)
      if temperature is None or not math.isfinite(temperature):
        return neutral
      # Display thresholds only; fan and thermal policies are unchanged.
      return ((108, 211, 145) if temperature < 60 else (238, 211, 105) if temperature < 70
              else (246, 164, 80) if temperature < 80 else (241, 101, 101))
    return neutral, temperature_color("temperature"), neutral, temperature_color("gpu_temperature"), neutral, neutral

  def action_label(self, korean=False):
    state, action = self.action_status.get("state"), self.action_status.get("action")
    if state == "running":
      return (("Pi 업데이트 중 · 완료 후 자동 재부팅" if action == "pi_update" else "C4·Pi 로그 수집·전송 중") if korean
              else ("Updating Pi · automatic reboot on completion" if action == "pi_update" else "Collecting and uploading C4 + Pi logs"))
    if state == "checking":
      return "C4 응답 확인 중…" if korean else "Waiting for C4 response…"
    if state == "succeeded":
      return (("Pi 업데이트 완료 · 재부팅 예정" if action == "pi_update" else "C4·Pi 로그 전송 완료") if korean
              else ("Pi updated · reboot scheduled" if action == "pi_update" else "C4 + Pi logs uploaded"))
    if state == "warning":
      return "C4 로그 전송 완료 · Pi 로그 재시도 필요" if korean else "C4 log uploaded · Pi log retry needed"
    reason = self.action_status.get("reason")
    if reason == "update_c4":
      return "C4 코드를 업데이트하고 대시보드를 재시작하세요" if korean else "Update C4 code and restart its dashboard"
    if reason == "busy":
      return "다른 작업이 진행 중입니다" if korean else "Another maintenance job is running"
    if state == "failed":
      if self.action_status.get("message"):
        return ("작업 실패 · " if korean else "Failed · ") + self.action_status["message"][:120]
      return "작업 실패 · C4 연결과 대시보드 로그를 확인하세요" if korean else "Action failed · check C4 connection and dashboard logs"
    if not self.actions_available or state == "unavailable":
      return "C4 연결 후 업데이트·로그 전송을 사용할 수 있습니다" if korean else "Connect C4 to use update and log upload"
    return ""

  @staticmethod
  def _local_rect(rect, left, top):
    return rect[0] - left, rect[1] - top, rect[2], rect[3]

  def _label(self, surface, text, size, center, color):
    label = self._font(size).render(text, True, color)
    surface.blit(label, label.get_rect(center=center))
