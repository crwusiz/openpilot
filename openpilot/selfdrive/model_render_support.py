"""Model overlay geometry, path styling, and planned-stop rules shared by the driving displays."""

from enum import Enum, auto
import math


DISENGAGED_PATH_ALPHA = 90
FALLBACK_PATH_ALPHA = 30
CLIP_MARGIN = 500  # px
MAX_DRAW_DISTANCE = 100.0  # m
LANE_LINE_WIDTH = 0.025  # m, half-width at full confidence
ROAD_EDGE_WIDTH = 0.025  # m, half-width

MIN_LEAD_BAR_LENGTH = 3.0  # px
LEAD_BAR_LENGTH = 12.0  # px, maximum for Big UI and the cluster
MICI_LEAD_BAR_LENGTH = 10.0  # px, maximum for Mici
LEAD_BAR_WIDTH = 1.8  # m
LANE_EDGES = (-1.8, 1.8)  # m, offsets for a stop bar without lane lines
LANE_LINE_PROB = 0.5  # minimum confidence for stop-bar lane edges

STOP_SPEED = 1.0  # m/s, planned speed that counts as stopping
STOP_APPEAR_DISTANCE = (8.0, 50.0)  # m
STOP_LEAD_MARGIN = 15.0  # m, a lead this close past the stop is what it's stopping for


class PathStyle(Enum):
  DISENGAGED = auto()
  STEERING = auto()
  STOCK = auto()
  ACCELERATION = auto()
  FALLBACK = auto()


def get_path_style(*, disengaged: bool, steering_pressed: bool, enabled: bool, has_acceleration_gradient: bool) -> PathStyle:
  if disengaged:
    return PathStyle.DISENGAGED
  if steering_pressed:
    return PathStyle.STEERING
  if not enabled:
    return PathStyle.STOCK
  if has_acceleration_gradient:
    return PathStyle.ACCELERATION
  return PathStyle.FALLBACK


def get_planned_stop_distance(path_x, velocity_x) -> float | None:
  if len(path_x) != len(velocity_x):
    return None

  for i, speed in enumerate(velocity_x):
    if not math.isfinite(speed):
      return None
    if speed < STOP_SPEED:
      if i == 0:
        return 0.0
      near, far = path_x[i - 1], path_x[i]
      if not math.isfinite(near) or not math.isfinite(far):
        return None
      prior_speed = velocity_x[i - 1]
      fraction = (prior_speed - STOP_SPEED) / (prior_speed - speed)
      return float(near + (far - near) * fraction)
  return None


def hysteresis(x: float, state: bool, on: float, off: float) -> bool:
  return x > on or (state and x > off)


class PlannedStopTracker:
  """Track a predicted stop before it is projected into the camera view."""

  def __init__(self, dt: float, radar_to_camera: float, filter_type):
    self.radar_to_camera = radar_to_camera
    self.d = 0.0
    self.planned_d: float | None = None
    self.visible = False
    self._planning, self._planning_filter = False, filter_type(0.0, 0.3, dt)
    self._behind_lead, self._behind_lead_filter = False, filter_type(0.0, 0.3, dt)
    self.fade_filter = filter_type(0.0, 0.1, dt)

  def update_dt(self, dt: float) -> None:
    for filter_, rc in ((self._planning_filter, 0.3), (self._behind_lead_filter, 0.3), (self.fade_filter, 0.1)):
      filter_.dt = dt
      filter_.update_alpha(rc)

  def update_from_model(self, path_x, velocity_x, model_updated: bool, v_ego: float, lead_distances: list[float]) -> bool:
    # The stop is fixed on the road, so advance its relative position between model updates.
    self.d = max(self.d - v_ego * self._planning_filter.dt, 0.0)
    if model_updated:
      self.planned_d = get_planned_stop_distance(path_x, velocity_x)
      if self.planned_d is not None:
        self.d = self.d + 0.3 * (self.planned_d - self.d) if self.visible else self.planned_d

    self._planning = hysteresis(self._planning_filter.update(self.planned_d is not None), self._planning, 0.6, 0.2)
    behind_lead = any(d < self.d + self.radar_to_camera + STOP_LEAD_MARGIN for d in lead_distances)
    self._behind_lead = hysteresis(self._behind_lead_filter.update(behind_lead), self._behind_lead, 0.6, 0.1)
    appear = STOP_APPEAR_DISTANCE[0] < self.d < STOP_APPEAR_DISTANCE[1]
    self.visible = self._planning and not self._behind_lead and (self.visible or appear)
    return self.visible
