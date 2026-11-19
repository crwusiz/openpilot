"""HUD display rules shared across the on-device UI and cluster."""

from enum import Enum


SET_SPEED_NA = 255
KM_TO_MILE = 0.621371


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
