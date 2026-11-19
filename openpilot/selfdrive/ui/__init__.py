import raylib_nb as rl

from openpilot.selfdrive.color_support import colors_alpha as rgba_with_alpha, make_ui_colors


UI_BORDER_SIZE = 30
Colors = make_ui_colors(rl.Color)


def colors_alpha(color, alpha):
  rgb = color if isinstance(color, tuple) else (color.r, color.g, color.b)
  return rl.Color(*rgba_with_alpha(rgb, alpha))
