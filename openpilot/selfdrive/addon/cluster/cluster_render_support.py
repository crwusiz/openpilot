"""Portable rendering primitives shared by C4 and the standalone Pi bundle."""

from types import SimpleNamespace


RGBColor = tuple[int, int, int]
RGBAColor = tuple[int, int, int, int]


def colors_alpha(color: RGBColor | RGBAColor, alpha: int = 255) -> RGBAColor:
  return (*color[:3], alpha)


class Colors:
  """The cluster palette, also re-exported by the C4 ClusterConfig module."""
  BLACK = (0, 0, 0, 255)
  PANEL = (7, 12, 18, 255)
  DIVIDER = (42, 54, 68, 255)
  MUTED_TEXT = (120, 132, 148, 255)
  SIGN_TEXT = (20, 25, 30, 255)
  DISTANCE_BADGE = (18, 25, 34, 255)
  WHITE = (255, 255, 255, 255)
  RED = (201, 34, 49, 255)
  STEERING = (0, 191, 255, 255)
  ORANGE = (255, 149, 0, 255)
  MAX_ACTIVE = (128, 216, 166, 255)
  CAUTION = (255, 200, 100, 255)
  Border = SimpleNamespace(
    DISENGAGED=(18, 40, 57, 255), OVERRIDE=(137, 146, 141, 255),
    ENGAGED=(22, 127, 64, 255), RED=RED, STEERING=STEERING,
    BLINKER=ORANGE, ACTIVE=(111, 192, 201, 255), READY=(143, 201, 192, 255),
  )
  THROTTLE = [(13, 248, 122, 102), (114, 255, 92, 89), (114, 255, 92, 0)]
  NO_THROTTLE = [(242, 242, 242, 102), (242, 242, 242, 89), (242, 242, 242, 0)]
  STEERING_PRESSED = [(0, 191, 255, 102), (0, 191, 255, 89), (0, 191, 255, 0)]


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
