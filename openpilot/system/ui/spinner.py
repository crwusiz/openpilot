#!/usr/bin/env python3
import raylib_nb as rl
import select
import sys

from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.text import wrap_text
from openpilot.system.ui.widgets import Widget

from openpilot.system.ui.widgets.network import WifiManager, WifiManagerUI
from openpilot.selfdrive.ui import Colors


# Constants
_BIG_UI = gui_app.big_ui()
PROGRESS_BAR_WIDTH = 1000 if _BIG_UI else 268
PROGRESS_BAR_HEIGHT = 20 if _BIG_UI else 10
TEXTURE_SIZE = 360 if _BIG_UI else 140
WRAPPED_SPACING = 50 if _BIG_UI else 10
CENTERED_SPACING = 150 if _BIG_UI else 20
MARGIN_H = 100 if _BIG_UI else 20
FONT_SIZE = 96 if _BIG_UI else 28
LINE_HEIGHT = 104 if _BIG_UI else 32
IP_FONT_SIZE = 72 if _BIG_UI else 36
IP_TOP_MARGIN = 36 if _BIG_UI else 12
STATUS_FONT_SIZE = 48 if _BIG_UI else 28
STATUS_LINE_MARGIN = 14 if _BIG_UI else 4

DEGREES_PER_SECOND = 360.0  # one full rotation per second
DARKGRAY = rl.Color(55, 55, 55, 255)


def clamp(value, min_value, max_value):
  return max(min(value, max_value), min_value)


def fit_single_line(text: str, font: rl.Font, font_size: int, max_width: float) -> str:
  text = " ".join(text.split())
  if not text:
    return ""
  if measure_text_cached(font, text, font_size).x <= max_width:
    return text

  suffix = "..."
  lo, hi = 0, len(text)
  while lo < hi:
    mid = (lo + hi + 1) // 2
    candidate = text[:mid].rstrip() + suffix
    if measure_text_cached(font, candidate, font_size).x <= max_width:
      lo = mid
    else:
      hi = mid - 1
  return text[:lo].rstrip() + suffix


class Spinner(Widget):
  def __init__(self):
    super().__init__()
    self._comma_texture = gui_app.texture("images/spinner_comma.png", TEXTURE_SIZE, TEXTURE_SIZE)
    self._spinner_texture = gui_app.texture("images/spinner_track.png", TEXTURE_SIZE, TEXTURE_SIZE, alpha_premultiply=True)
    self._rotation = 0.0
    self._progress: int | None = None
    self._status_line = ""
    self._wrapped_lines: list[str] = []

    self.wifi_manager = WifiManager()
    self.wifi_manager_ui = WifiManagerUI(self.wifi_manager)
    self._ip_address = "Offline"
    self._last_refresh = 0

  def set_text(self, text: str) -> None:
    text = text.strip()
    if text.isdigit():
      self._progress = clamp(int(text), 0, 100)
      self._wrapped_lines = []
    else:
      self._status_line = text
      if self._progress is None:
        self._wrapped_lines = wrap_text(text, FONT_SIZE, gui_app.width - MARGIN_H)

  def _render(self, rect: rl.Rectangle):
    self.wifi_manager_ui._update_state()
    if rl.get_time() - self._last_refresh > 5.0:
      ip = self.wifi_manager_ui.ip_address
      self._ip_address = ip if ip else "Offline"
      self._last_refresh = rl.get_time()

    ip_label = self._ip_address
    ip_color = Colors.LIME if self._ip_address != "Offline" else rl.WHITE

    ip_font = gui_app.font(FontWeight.SEMI_BOLD)
    ip_size = measure_text_cached(ip_font, ip_label, IP_FONT_SIZE)
    ip_x = rect.x + rect.width / 2.0 - ip_size.x / 2.0
    rl.draw_text_ex(ip_font, ip_label, rl.Vector2(round(ip_x), rect.y + IP_TOP_MARGIN), IP_FONT_SIZE, 0.0, ip_color)

    status_line = ""
    status_size = rl.Vector2(0, 0)
    if self._progress is not None and self._status_line:
      status_line = fit_single_line(self._status_line, ip_font, STATUS_FONT_SIZE, rect.width - MARGIN_H)
      status_size = measure_text_cached(ip_font, status_line, STATUS_FONT_SIZE)

    # Reserve the IP header before laying out the logo and its message.
    content_top = rect.y + IP_TOP_MARGIN + ip_size.y + WRAPPED_SPACING
    available_height = max(0.0, rect.y + rect.height - IP_TOP_MARGIN - content_top)
    if self._wrapped_lines:
      spacing = WRAPPED_SPACING
      line_height = max(LINE_HEIGHT, measure_text_cached(gui_app.font(), self._wrapped_lines[-1], FONT_SIZE).y)
      text_height = (len(self._wrapped_lines) - 1) * LINE_HEIGHT + line_height
    elif self._progress is not None:
      spacing = max(CENTERED_SPACING, status_size.y + STATUS_LINE_MARGIN + WRAPPED_SPACING) if status_line else CENTERED_SPACING
      text_height = PROGRESS_BAR_HEIGHT
    else:
      spacing = 0
      text_height = 0
    texture_size = min(TEXTURE_SIZE, max(0.0, available_height - spacing - text_height))
    total_height = texture_size + spacing + text_height
    center_y = content_top + max(0.0, (available_height - total_height) / 2.0) + texture_size / 2.0
    y_pos = center_y + texture_size / 2.0 + spacing

    center = rl.Vector2(rect.x + rect.width / 2.0, center_y)
    spinner_origin = rl.Vector2(texture_size / 2.0, texture_size / 2.0)
    comma_rect = rl.Rectangle(center.x - texture_size / 2.0, center.y - texture_size / 2.0, texture_size, texture_size)

    delta_time = rl.get_frame_time()
    self._rotation = (self._rotation + DEGREES_PER_SECOND * delta_time) % 360.0

    # Draw rotating spinner and static comma logo
    rl.draw_texture_pro(self._spinner_texture, rl.Rectangle(0, 0, TEXTURE_SIZE, TEXTURE_SIZE),
                        rl.Rectangle(center.x, center.y, texture_size, texture_size),
                        spinner_origin, self._rotation, rl.WHITE)
    rl.draw_texture_pro(self._comma_texture, rl.Rectangle(0, 0, TEXTURE_SIZE, TEXTURE_SIZE),
                        comma_rect, rl.Vector2(0, 0), 0, rl.WHITE)

    # Display the progress bar or text based on user input
    if self._progress is not None:
      bar = rl.Rectangle(center.x - PROGRESS_BAR_WIDTH / 2.0, y_pos, PROGRESS_BAR_WIDTH, PROGRESS_BAR_HEIGHT)
      rl.draw_rectangle_rounded(bar, 1, 10, DARKGRAY)

      bar.width *= self._progress / 100.0
      rl.draw_rectangle_rounded(bar, 1, 10, rl.WHITE)

      if status_line:
        status_y = y_pos - STATUS_LINE_MARGIN - status_size.y
        rl.draw_text_ex(ip_font, status_line, rl.Vector2(round(center.x - status_size.x / 2), status_y),
                        STATUS_FONT_SIZE, 0.0, rl.WHITE)
    elif self._wrapped_lines:
      for i, line in enumerate(self._wrapped_lines):
        text_size = measure_text_cached(gui_app.font(), line, FONT_SIZE)
        rl.draw_text_ex(gui_app.font(), line, rl.Vector2(center.x - text_size.x / 2, y_pos + i * LINE_HEIGHT),
                        FONT_SIZE, 0.0, rl.WHITE)


def _read_stdin():
  """Non-blocking read of available lines from stdin."""
  lines = []
  while True:
    rlist, _, _ = select.select([sys.stdin], [], [], 0.0)
    if not rlist:
      break
    line = sys.stdin.readline().strip()
    if line == "":
      break
    lines.append(line)
  return lines


def main():
  gui_app.init_window("Spinner")
  spinner = Spinner()
  for _ in gui_app.render():
    text_list = _read_stdin()
    for text in text_list:
      spinner.set_text(text)

    spinner.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))


if __name__ == "__main__":
  main()
