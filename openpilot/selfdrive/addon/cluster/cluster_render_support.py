"""Portable rendering primitives shared by C4 and the standalone Pi bundle."""

try:
  from openpilot.selfdrive.color_support import ClusterColors as Colors, RGBColor, RGBAColor, colors_alpha
except ImportError:
  from color_support import ClusterColors as Colors, RGBColor, RGBAColor, colors_alpha


__all__ = ["Colors", "FirstOrderFilter", "RGBColor", "RGBAColor", "colors_alpha", "park_layout", "park_upload_button_bounds"]


def park_layout(size):
  width, height = size
  scale = min(width / 1920, height / 480)
  return scale, (width - 1920 * scale) / 2, (height - 480 * scale) / 2


def park_upload_button_bounds(size):
  """Inclusive edges in the unrotated logical frame."""
  scale, left, top = park_layout(size)
  return (round(left + 1434 * scale), round(top + 329 * scale),
          round(left + 1808 * scale), round(top + 393 * scale))


class FirstOrderFilter:
  """Pi fallback for common.filter_simple.FirstOrderFilter's exact update."""

  def __init__(self, x0, rc, dt, initialized=True):
    self.x = x0
    self.dt = dt
    self.update_alpha(rc)
    self.initialized = initialized

  def update_alpha(self, rc):
    self.alpha = self.dt / (rc + self.dt)

  def update(self, x):
    if self.initialized:
      self.x = (1.0 - self.alpha) * self.x + self.alpha * x
    else:
      self.initialized = True
      self.x = x
    return self.x
