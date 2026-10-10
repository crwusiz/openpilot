import pyray as rl

SIDE_PANEL_WIDTH = 60
RIGHT_ICON_CENTER_OFFSET = 46
LEFT_COLUMN_SIZES = (52, 52, 44, 44)  # cruise, set, driver monitoring, steering wheel


def left_column_rect(rect: rl.Rectangle, row: int) -> rl.Rectangle:
  top_padding, bottom_padding = 10, 14
  gap = (rect.height - top_padding - bottom_padding - sum(LEFT_COLUMN_SIZES)) / (len(LEFT_COLUMN_SIZES) - 1)
  size = LEFT_COLUMN_SIZES[row]
  return rl.Rectangle(rect.x + 46 - size / 2,
                      rect.y + top_padding + sum(LEFT_COLUMN_SIZES[:row]) + row * gap, size, size)


def blend_colors(a: rl.Color, b: rl.Color, f: float) -> rl.Color:
  h0, s0, v0 = (hsv0 := rl.color_to_hsv(a)).x, hsv0.y, hsv0.z
  h1, s1, v1 = (hsv1 := rl.color_to_hsv(b)).x, hsv1.y, hsv1.z
  dh = ((h1 - h0 + 180) % 360) - 180  # shortest hue delta
  return rl.color_from_hsv((h0 + f * dh) % 360,
                           s0 + f * (s1 - s0),
                           v0 + f * (v1 - v0))
