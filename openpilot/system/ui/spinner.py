#!/usr/bin/env python3
import pyray as rl
import select
import sys

from openpilot.system.ui.lib.application import FONT_SCALE, FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.text import wrap_text
from openpilot.system.ui.widgets import Widget

from openpilot.system.ui.widgets.network import WifiManager, WifiManagerUI

# Constants
if gui_app.big_ui():
  PROGRESS_BAR_WIDTH = 1000
  PROGRESS_BAR_HEIGHT = 20
  TEXTURE_SIZE = 360
  WRAPPED_SPACING = 50
  CENTERED_SPACING = 150
  MARGIN_H = 100
  FONT_SIZE = 96
  LINE_HEIGHT = 104
  IP_FONT_SIZE = 72
  IP_TOP_MARGIN = 36
  STATUS_FONT_SIZE = 48
  STATUS_LINE_MARGIN = 14
else:
  PROGRESS_BAR_WIDTH = 268
  PROGRESS_BAR_HEIGHT = 10
  TEXTURE_SIZE = 140
  WRAPPED_SPACING = 10
  CENTERED_SPACING = 20
  MARGIN_H = 20
  FONT_SIZE = 28
  LINE_HEIGHT = 32
  IP_FONT_SIZE = 36
  IP_TOP_MARGIN = 12
  STATUS_FONT_SIZE = 28
  STATUS_LINE_MARGIN = 4

DEGREES_PER_SECOND = 360.0  # one full rotation per second
DARKGRAY = (55, 55, 55, 255)
COLOR_UP_TO_DATE = rl.Color(128, 216, 166, 255)


def clamp(value: int, min_value: int, max_value: int) -> int:
  return max(min(value, max_value), min_value)


def fit_single_line(text: str, font: rl.Font, font_size: float, max_width: float) -> str:
  text = " ".join(text.split())
  if not text:
    return ""
  if rl.measure_text_ex(font, text, font_size, 0.0).x <= max_width:
    return text

  suffix = "..."
  lo, hi = 0, len(text)
  while lo < hi:
    mid = (lo + hi + 1) // 2
    candidate = text[:mid].rstrip() + suffix
    if rl.measure_text_ex(font, candidate, font_size, 0.0).x <= max_width:
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
    ip_color = COLOR_UP_TO_DATE if self._ip_address != "Offline" else rl.WHITE

    ip_font = gui_app.font(FontWeight.SEMI_BOLD)
    ip_scaled_size = IP_FONT_SIZE * FONT_SCALE
    ip_size = rl.measure_text_ex(ip_font, ip_label, ip_scaled_size, 0.0)
    ip_x = rect.x + rect.width / 2.0 - ip_size.x / 2.0
    draw_text_ex = getattr(rl, "_orig_draw_text_ex", rl.draw_text_ex)
    draw_text_ex(ip_font, ip_label, rl.Vector2(round(ip_x), rect.y + IP_TOP_MARGIN),
                 ip_scaled_size, 0.0, ip_color)

    if self._wrapped_lines:
      # Calculate total height required for spinner and text
      spacing = WRAPPED_SPACING
      total_height = TEXTURE_SIZE + spacing + len(self._wrapped_lines) * LINE_HEIGHT
      center_y = (rect.height - total_height) / 2.0 + TEXTURE_SIZE / 2.0
    else:
      # Center spinner vertically
      spacing = CENTERED_SPACING
      center_y = rect.height / 2.0
    y_pos = center_y + TEXTURE_SIZE / 2.0 + spacing

    center = rl.Vector2(rect.width / 2.0, center_y)
    spinner_origin = rl.Vector2(TEXTURE_SIZE / 2.0, TEXTURE_SIZE / 2.0)
    comma_position = rl.Vector2(center.x - TEXTURE_SIZE / 2.0, center.y - TEXTURE_SIZE / 2.0)

    delta_time = rl.get_frame_time()
    self._rotation = (self._rotation + DEGREES_PER_SECOND * delta_time) % 360.0

    # Draw rotating spinner and static comma logo
    rl.draw_texture_pro(self._spinner_texture, rl.Rectangle(0, 0, TEXTURE_SIZE, TEXTURE_SIZE),
                        rl.Rectangle(center.x, center.y, TEXTURE_SIZE, TEXTURE_SIZE),
                        spinner_origin, self._rotation, rl.WHITE)
    rl.draw_texture_v(self._comma_texture, comma_position, rl.WHITE)

    # Display the progress bar or text based on user input
    if self._progress is not None:
      bar = rl.Rectangle(center.x - PROGRESS_BAR_WIDTH / 2.0, y_pos, PROGRESS_BAR_WIDTH, PROGRESS_BAR_HEIGHT)
      rl.draw_rectangle_rounded(bar, 1, 10, DARKGRAY)

      bar.width *= self._progress / 100.0
      rl.draw_rectangle_rounded(bar, 1, 10, rl.WHITE)

      if self._status_line:
        status_font = gui_app.font(FontWeight.SEMI_BOLD)
        status_scaled_size = STATUS_FONT_SIZE * FONT_SCALE
        status_line = fit_single_line(self._status_line, status_font, status_scaled_size, gui_app.width - MARGIN_H)
        status_size = rl.measure_text_ex(status_font, status_line, status_scaled_size, 0.0)
        status_y = y_pos - STATUS_LINE_MARGIN - status_size.y
        draw_text_ex = getattr(rl, "_orig_draw_text_ex", rl.draw_text_ex)
        draw_text_ex(status_font, status_line, rl.Vector2(round(center.x - status_size.x / 2), status_y),
                     status_scaled_size, 0.0, rl.WHITE)
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
