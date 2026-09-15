#!/usr/bin/env python3
import re
import sys
import pyray as rl
from openpilot.system.hardware import HARDWARE, PC
from openpilot.system.ui.lib.application import BIG_UI, gui_app
from openpilot.system.ui.lib.scroll_panel import GuiScrollPanel
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle

import subprocess
import threading
from pathlib import Path
from openpilot.system.ui.widgets.network import WifiManager, WifiManagerUI

if BIG_UI:
  MARGIN = 50
  SPACING = 40
  FONT_SIZE = 72
  LINE_HEIGHT = 80
  BUTTON_SIZE = rl.Vector2(310, 160)
else:
  MARGIN = 20
  SPACING = 30
  FONT_SIZE = 25
  LINE_HEIGHT = 25
  BUTTON_SIZE = rl.Vector2(150, 80)

DEMO_TEXT = """This is a sample text that will be wrapped and scrolled if necessary.
            The text is long enough to demonstrate scrolling and word wrapping.""" * 30


def wrap_text(text, font_size, max_width):
  lines = []
  font = gui_app.font()

  for paragraph in text.split("\n"):
    if not paragraph.strip():
      # Don't add empty lines first, ensuring wrap_text("") returns []
      if lines:
        lines.append("")
      continue
    indent = re.match(r"^\s*", paragraph).group()
    current_line = indent
    words = re.split(r"(\s+|-)", paragraph[len(indent):])
    while len(words):
      word = words.pop(0)
      test_line = current_line + word + (words.pop(0) if words else "")
      if measure_text_cached(font, test_line, font_size).x <= max_width:
        current_line = test_line
      else:
        lines.append(current_line)
        current_line = word + " "
    current_line = current_line.rstrip()
    if current_line:
      lines.append(current_line)

  return lines


class TextWindow(Widget):
  def __init__(self, text: str):
    super().__init__()

    header_space = FONT_SIZE + SPACING
    self._textarea_rect = rl.Rectangle(MARGIN, MARGIN + header_space, gui_app.width - MARGIN * 2, gui_app.height - MARGIN * 2 - header_space)
    self._wrapped_lines = wrap_text(text, FONT_SIZE, self._textarea_rect.width - 20)
    self._content_rect = rl.Rectangle(0, 0, self._textarea_rect.width - 20, len(self._wrapped_lines) * LINE_HEIGHT)
    self._scroll_panel = GuiScrollPanel()
    self._scroll_panel._offset_filter_y.x = -max(self._content_rect.height - self._textarea_rect.height, 0)

    button_text = "Exit" if PC else "Reboot"
    self._button_reboot = Button(button_text, click_callback=self._on_reboot_clicked, button_style=ButtonStyle.TRANSPARENT_WHITE_BORDER, font_size=FONT_SIZE)
    self._button_git_pull = Button("Git Pull", click_callback=self._on_git_pull_clicked, button_style=ButtonStyle.TRANSPARENT_WHITE_BORDER, font_size=FONT_SIZE)

    self.wifi_manager = WifiManager()
    self.wifi_manager_ui = WifiManagerUI(self.wifi_manager)

    self._is_pulling = False
    self._git_pull_exit_file = Path("/data/gitpull_exit_code.log")

  @staticmethod
  def _on_reboot_clicked():
    gui_app.request_close()
    if not PC:
      HARDWARE.reboot()

  def _update_git_button_text(self, text: str):
    """Git Pull 진행 상태에 따라 버튼 텍스트를 업데이트합니다."""
    self._button_git_pull = Button(text, click_callback=self._on_git_pull_clicked, button_style=ButtonStyle.TRANSPARENT_WHITE_BORDER, font_size=FONT_SIZE)

  def _on_git_pull_clicked(self):
    if self._is_pulling:
      return

    self._is_pulling = True
    self._update_git_button_text("Pulling...")
    self._git_pull_exit_file.unlink(missing_ok=True)

    def run_git_pull():
      try:
        subprocess.run(["/bin/sh", "/data/openpilot/scripts/gitpull.sh"], timeout=60)

        if self._git_pull_exit_file.exists():
          self._update_git_button_text("Pull Done")
        else:
          self._update_git_button_text("Pull Failed")
      except subprocess.TimeoutExpired:
        self._update_git_button_text("Timeout")
      except Exception:
        self._update_git_button_text("Pull Failed")
      finally:
        self._is_pulling = False

    threading.Thread(target=run_git_pull, daemon=True).start()

  def _render(self, rect: rl.Rectangle):
    self.wifi_manager_ui._update_state()
    ip_address = self.wifi_manager_ui.ip_address or "Offline"

    ip_text = f"IP: {ip_address}"
    ip_text_size = measure_text_cached(gui_app.font(), ip_text, FONT_SIZE)
    ip_pos = rl.Vector2(rect.width - MARGIN - ip_text_size.x, MARGIN)
    rl.draw_text_ex(gui_app.font(), ip_text, ip_pos, FONT_SIZE, 0, rl.WHITE)

    scroll = self._scroll_panel.update(self._textarea_rect, self._content_rect)
    rl.begin_scissor_mode(int(self._textarea_rect.x), int(self._textarea_rect.y), int(self._textarea_rect.width), int(self._textarea_rect.height))
    for i, line in enumerate(self._wrapped_lines):
      position = rl.Vector2(self._textarea_rect.x, self._textarea_rect.y + scroll + i * LINE_HEIGHT)
      if position.y + LINE_HEIGHT < self._textarea_rect.y or position.y > self._textarea_rect.y + self._textarea_rect.height:
        continue
      rl.draw_text_ex(gui_app.font(), line, position, FONT_SIZE, 0, rl.WHITE)
    rl.end_scissor_mode()

    reboot_bounds = rl.Rectangle(rect.width - MARGIN - BUTTON_SIZE.x - SPACING, rect.height - MARGIN - BUTTON_SIZE.y, BUTTON_SIZE.x, BUTTON_SIZE.y)
    self._button_reboot.render(reboot_bounds)

    git_pull_bounds = rl.Rectangle(reboot_bounds.x - BUTTON_SIZE.x - SPACING, reboot_bounds.y, BUTTON_SIZE.x, BUTTON_SIZE.y)
    self._button_git_pull.render(git_pull_bounds)


if __name__ == "__main__":
  text = sys.argv[1] if len(sys.argv) > 1 else DEMO_TEXT
  gui_app.init_window("Text Viewer")
  text_window = TextWindow(text)
  for _ in gui_app.render():
    text_window.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
