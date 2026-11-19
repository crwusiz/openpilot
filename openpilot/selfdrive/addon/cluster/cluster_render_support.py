"""Portable rendering primitives shared by C4 and the standalone Pi bundle."""

try:
  from openpilot.selfdrive.color_support import ClusterColors as Colors, RGBColor, RGBAColor, colors_alpha
except ImportError:
  from color_support import ClusterColors as Colors, RGBColor, RGBAColor, colors_alpha


__all__ = ["Colors", "FirstOrderFilter", "RGBColor", "RGBAColor", "colors_alpha"]


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
