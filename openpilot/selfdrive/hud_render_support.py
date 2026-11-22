"""HUD display rules shared across the on-device UI and cluster."""

from enum import Enum


SET_SPEED_NA = 255
KM_TO_MILE = 0.621371

LONG_INDICATOR_ASSET_DIR = "icons_mici/longitudinal"
LONG_INDICATOR_HIGHLIGHT_TIME = 2.5  # seconds
_LONG_INDICATOR_DISTANCE_BARS = (
  ((32, 7), (60, 35), 2),
  ((40, 9), (68, 37), 16),
  ((48, 11), (76, 39), 32),
  ((56, 13), (84, 41), 50),
)
LONG_INDICATOR_ICONS = (("car", (35, 27), -20), ("car_green", (50, 42), -20)) + tuple(
  (f"distance_{i}{suffix}", size, y)
  for i, (white, green, y) in enumerate(_LONG_INDICATOR_DISTANCE_BARS, start=1)
  for suffix, size in (("", white), ("_green", green))
)


class LongIndicatorState:
  """Compute icon opacities in LONG_INDICATOR_ICONS order for either renderer."""

  def __init__(self, dt: float, filter_type, *, settle_first: bool = False):
    self._settle_first = settle_first
    self._alpha_filter = filter_type(0.0, 0.05, dt)
    self._lead_filters = (filter_type(0.0, 0.1, dt), filter_type(0.0, 0.1, dt))
    self._distance_filters = [(filter_type(0.0, 0.1, dt), filter_type(0.0, 0.1, dt)) for _ in _LONG_INDICATOR_DISTANCE_BARS]
    self._distance_level: int | None = None
    self.reset()

  def reset(self) -> None:
    self._distance_level = None
    self._changed_at = -LONG_INDICATOR_HIGHLIGHT_TIME
    self._alpha_filter.x = 0.0
    if self._settle_first:
      self._alpha_filter.initialized = False
      for white, green in (self._lead_filters, *self._distance_filters):
        white.initialized = green.initialized = False

  def update(self, now: float, *, distance_level: int, has_lead: bool, e2e_lead: bool,
             disengaged: float, visible: bool = True) -> tuple[float, ...]:
    dim = 1 - (1 - 0.32 / 0.9) * disengaged
    alpha = self._alpha_filter.update(visible) * dim
    white_filter, green_filter = self._lead_filters
    e2e_lead = has_lead and e2e_lead
    white = white_filter.update(0.0 if e2e_lead else 0.9 if has_lead else 0.35)
    green = green_filter.update(float(e2e_lead))
    alphas = [(white + (0.9 - white) * disengaged) * alpha, green * (1 - disengaged) * alpha]

    if self._distance_level is not None and distance_level != self._distance_level:
      self._changed_at = now
    self._distance_level = distance_level
    # Keep a distance change highlighted until the indicator can be seen.
    if not visible and now - self._changed_at < LONG_INDICATOR_HIGHLIGHT_TIME:
      self._changed_at = now
    highlight = now - self._changed_at < LONG_INDICATOR_HIGHLIGHT_TIME

    for i, (active_filter, green_filter) in enumerate(self._distance_filters, start=1):
      active = active_filter.update(float(i <= distance_level))
      green = green_filter.update(float(highlight and i == distance_level))
      alphas.extend(((0.35 * (1 - active) + 0.9 * (active - green)) * alpha, green * alpha))
    return tuple(alphas)


class UIStatus(Enum):
  DISENGAGED = "disengaged"
  ENGAGED = "engaged"
  OVERRIDE = "override"
  RED = "red"
  STEERING = "steering"
  BLINKER = "blinker"
  ACTIVE = "active"
  READY = "ready"


def get_hud_status(*, enabled: bool, lat_active: bool, pre_enabled_or_overriding: bool, steering_pressed: bool,
                   brake_pressed: bool, blinker: bool, reverse: bool, cruise_available: bool, v_ego: float) -> UIStatus:
  if pre_enabled_or_overriding and not steering_pressed:
    return UIStatus.OVERRIDE
  if enabled:
    if steering_pressed:
      return UIStatus.STEERING
    if brake_pressed:
      return UIStatus.RED
    if blinker:
      return UIStatus.BLINKER
    return UIStatus.ACTIVE if lat_active else UIStatus.ENGAGED
  if reverse:
    return UIStatus.RED
  if cruise_available:
    if lat_active:
      return UIStatus.READY
    if v_ego > 0.3:
      return UIStatus.BLINKER
  return UIStatus.DISENGAGED


def select_vehicle_speed(v_ego: float, v_ego_cluster: float, cluster_speed_seen: bool) -> tuple[float, bool]:
  # Once the instrument cluster supplies speed, a later zero is also authoritative.
  cluster_speed_seen = cluster_speed_seen or v_ego_cluster != 0.0
  return (v_ego_cluster if cluster_speed_seen else v_ego), cluster_speed_seen


def is_cruise_speed_set(speed: float) -> bool:
  return 0 < speed < SET_SPEED_NA


def current_speed_color(accel: float) -> tuple[int, int, int]:
  if accel > 0:
    channel = max(80, int(255 - 180 * min(accel / 3.0, 1.0)))
    return channel, 255, channel
  channel = max(60, int(255 - 255 * min(abs(accel) / 4.0, 1.0)))
  return 255, channel, channel


def traffic_light_index(state: int | None) -> int:
  if state == 1:
    return 1
  if state == 2:
    return 2
  return 0


def speed_limit_warning_level(speed: float, limit: float) -> int:
  if speed > limit + 25:
    return 3
  if speed > limit + 15:
    return 2
  if speed > limit + 5:
    return 1
  return 0


def format_speed_limit_distance(distance: float) -> str:
  return f"{distance / 1000:.1f} km" if distance >= 1000 else f"{int(distance)} m"


def tpms_reading(pressure: float) -> tuple[str, bool]:
  if not 5 <= pressure <= 60:
    return "--", False
  return str(round(pressure)), pressure < 31


def ignore_limit_ratio(timer: float) -> float:
  max_ticks = 3000.0
  return (max_ticks - timer) / max_ticks if 0 < timer < max_ticks else 0.0
