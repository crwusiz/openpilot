import pyray as rl

from openpilot.selfdrive.ui.ui_state import ChestnutState, ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached


SPINNER_FRAMES = '-\\|/'


def draw_chestnut_loading(rect: rl.Rectangle) -> None:
  if ui_state.chestnut_state != ChestnutState.LOADING:
    return

  font = gui_app.font(FontWeight.SEMI_BOLD)
  font_size = 28
  text = "chestnut initialize"
  text_size = measure_text_cached(font, text, font_size)
  spinner = SPINNER_FRAMES[int(rl.get_time() * 10) % len(SPINNER_FRAMES)]
  spinner_size = measure_text_cached(font, spinner, font_size)
  # Keep the text and background still as the spinner glyph changes width.
  spinner_width = max(measure_text_cached(font, frame, font_size).x for frame in SPINNER_FRAMES)
  content_width = text_size.x + 12 + spinner_width
  text_x = rect.x + (rect.width - content_width) / 2
  text_y = rect.y + (rect.height - text_size.y) / 2

  background = rl.Rectangle(text_x - 16, text_y - 10, content_width + 32, text_size.y + 20)
  rl.draw_rectangle_rounded(background, 0.25, 10, rl.Color(0, 0, 0, 180))
  rl.draw_text_ex(font, text, rl.Vector2(text_x, text_y), font_size, 0, rl.WHITE)
  spinner_x = text_x + text_size.x + 12 + (spinner_width - spinner_size.x) / 2
  rl.draw_text_ex(font, spinner, rl.Vector2(spinner_x, text_y), font_size, 0, rl.WHITE)
