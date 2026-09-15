import math

from opendbc.car import DT_CTRL
from opendbc.car.common.conversions import UnitConverter


NAVI_SPEED_CAMERA_PARAM_UPDATE_FRAMES = round(1.0 / DT_CTRL)
NAVI_MAX_EVENT_DISTANCE = 2500.0
NAVI_PASSED_EVENT_DISTANCE = 30.0
NAVI_MAX_EVENTS = 32
NAVI_MAX_CURVES = 64
NAVI_CAMERA_KINDS = (0, 1, 2)
NAVI_CONTROLLED_ACCESS_LINK_CLASSES = (1, 2, 3)  # Freeway, IC, JC
NAVI_CONTROLLED_ACCESS_ROAD_CLASSES = (1, 2)  # Freeway, arterial/city freeway
NAVI_SCHOOL_ZONE_MAX_DISTANCE = 1000.0
NAVI_POSITION_TIMEOUT_NS = 1_000_000_000
NAVI_CURVE_MAX_DISTANCE = 1500.0
NAVI_CURVE_DISTANCE_FACTOR = 2.0
NAVI_CURVE_SHORT_SPOT_MAX_DISTANCE = 10.0
NAVI_CURVE_SHORT_SPOT_MAX_SPEED = 20.0
NAVI_CURVE_TARGET_LAT_ACCEL = 1.9

VehicleNaviCurveSpeedFactor = 100
VehicleNaviCurveCtrlEnd = 3
AutoCurveSpeedLowerLimit = 30
AutoNaviSpeedDecelRate = 200


class NaviState:
  def __init__(self, params=None):
    self.params = params
    self.total_distance = 0.0
    self.speed_limit_distance = 0.0

    self.speed_camera_distance_time = 6.0
    self.can_control = True
    self.school_zone_control = True
    self.curve_speed_factor = 1.0
    self.curve_lower_limit = 30.0
    self.curve_decel_rate = 1.2
    self.curve_control_end = 3.0
    self.speed_camera_params_counter = 0
    self.events = []
    self.curves = []
    self.segment_timestamp = 0
    self.curve_timestamp = 0
    self.profile_timestamp = 0
    self.available = False
    self.route_reset_timestamp = 0
    self.curve_route_active = False
    self.curve_route_state = 3
    self.road_class = 7
    self.camera_target = None
    self.speed_zone_active = False
    self.speed_zone_speed = 0.0
    self.school_zone_active = False
    self.school_zone_start_distance = 0.0
    self.school_zone_uses_camera_status = False

    self.hda_info = None
    self.position = None
    self.segment = None
    self.profile_short = None
    self.profile = None

    self._read_control_params()

  @staticmethod
  def _speed_camera_distance_time(raw_value):
    return min(200, max(10, raw_value)) / 10.0

  def _read_control_params(self):
    if self.params is None:
      return

    self.curve_speed_factor = min(2.0, max(0.5, VehicleNaviCurveSpeedFactor * 0.01))
    self.curve_lower_limit = max(5.0, AutoCurveSpeedLowerLimit)
    self.curve_decel_rate = max(0.01, AutoNaviSpeedDecelRate * 0.01)
    self.curve_control_end = max(0.0, VehicleNaviCurveCtrlEnd)

  def _update_speed_camera_params(self):
    self.speed_camera_params_counter += 1
    if self.speed_camera_params_counter < NAVI_SPEED_CAMERA_PARAM_UPDATE_FRAMES:
      return False

    self.speed_camera_params_counter = 0
    self._read_control_params()
    distance_time_tenths = 60
    distance_time = self._speed_camera_distance_time(distance_time_tenths)
    changed = distance_time != self.speed_camera_distance_time
    self.speed_camera_distance_time = distance_time
    return changed

  def _clear_events(self):
    self.events = []
    self.camera_target = None

  def _clear_curves(self):
    self.curves = []

  def _clear_school_zone(self):
    self.school_zone_active = False
    self.school_zone_start_distance = self.total_distance
    self.school_zone_uses_camera_status = False

  def _clear_speed_zone(self):
    self.speed_zone_active = False
    self.speed_zone_speed = 0.0

  @staticmethod
  def _message_timestamp(cp, name):
    return max(cp.ts_nanos.get(name, {}).values(), default=0)

  @staticmethod
  def _decode_segment(values):
    raw = sum(int(values.get(f"BYTE_{i + 1}", 0)) << (i * 8) for i in range(8))
    return {
      "offset": raw & 0x1fff,
      "calculated_route": (raw >> 22) & 0x3,
      "functional_road_class": (raw >> 24) & 0x7,
    }

  def _is_controlled_access_road(self):
    link_class = int(self.hda_info.get("LinkClass", 0)) if self.hda_info is not None else 0
    return (link_class in NAVI_CONTROLLED_ACCESS_LINK_CLASSES or
            self.road_class in NAVI_CONTROLLED_ACCESS_ROAD_CLASSES)

  @staticmethod
  def _decode_profile(values):
    return {
      "value": int(values.get("Value", 0xffffffff)),
      "offset": int(values.get("Offset", 8191)),
      "counter": int(values.get("CyclicCounter", 0)),
      "update": int(values.get("Update", 0)),
      "profile_type": int(values.get("ProfileType", 31)),
    }

  @staticmethod
  def _decode_adasis_curvature(value):
    """Decode the standard ADASIS v2 10-bit piecewise curvature profile."""
    value = int(value)
    if not 0 <= value < 1023:
      return None

    coded = value - 511
    magnitude = abs(coded)
    sign = -1 if coded < 0 else 1
    if magnitude <= 64:
      decoded = coded
    elif magnitude <= 128:
      decoded = 2 * (coded - sign * 32)
    elif magnitude <= 192:
      decoded = 4 * (coded - sign * 80)
    elif magnitude <= 256:
      decoded = 8 * (coded - sign * 136)
    elif magnitude <= 320:
      decoded = 16 * (coded - sign * 196)
    elif magnitude <= 384:
      decoded = 32 * (coded - sign * 258)
    elif magnitude <= 448:
      decoded = 64 * (coded - sign * 321)
    else:
      decoded = 128 * (coded - sign * 384)
    return decoded / 100000.0

  @staticmethod
  def _decode_curve(values):
    if int(values.get("ProfileType", 0)) != 1:
      return None

    offset = int(values.get("Offset", 8191))
    distance = int(values.get("Distance", 1023))
    raw_curvature = int(values.get("Value0", 1023))
    if not 0 <= offset <= NAVI_CURVE_MAX_DISTANCE or raw_curvature == 1023:
      return None

    curvature = NaviState._decode_adasis_curvature(raw_curvature)
    if curvature is None:
      return None
    return {
      "offset": offset,
      "span": distance * NAVI_CURVE_DISTANCE_FACTOR if 0 <= distance < 1023 else 0.0,
      "curvature": curvature,
      "raw_curvature": raw_curvature,
    }

  @staticmethod
  def _curve_reference_speed(curvature):
    if abs(curvature) < 1e-7:
      return 250.0
    return min(250.0, max(5.0, UnitConverter.ms_to_kph(math.sqrt(NAVI_CURVE_TARGET_LAT_ACCEL / abs(curvature)))))

  def _add_curve(self, curve):
    target = self.total_distance + curve["offset"]
    reference_speed = self._curve_reference_speed(curve["curvature"])
    # A single 10 m map node with a hairpin-level value is commonly a turn-node
    # discontinuity, not a road curve long enough to justify an 18 km/h cap.
    short_spot = 0.0 < curve["span"] <= NAVI_CURVE_SHORT_SPOT_MAX_DISTANCE
    if short_spot and reference_speed <= NAVI_CURVE_SHORT_SPOT_MAX_SPEED:
      return
    nearest = min(self.curves, key=lambda item: abs(item["target"] - target), default=None)
    if nearest is not None and abs(nearest["target"] - target) <= 2.0:
      nearest.update(target=target, span=curve["span"], curvature=curve["curvature"], speed=reference_speed)
    else:
      self.curves.append({"target": target, "span": curve["span"], "curvature": curve["curvature"], "speed": reference_speed})
    self.curves.sort(key=lambda item: item["target"])
    self.curves = self.curves[:NAVI_MAX_CURVES]

  def _update_curve_profile(self, cp, ret):
    ret.naviCurveDistance = 0.0
    ret.naviCurveSpeed = 0.0
    ret.naviCurveCurvature = 0.0
    ret.naviCurveRouteActive = self.curve_route_active
    ret.naviCurveRouteState = self.curve_route_state

    if self.profile_short is not None:
      timestamp = self._message_timestamp(cp, "Hud_Navi_V2_PROSHORT_E_00")
      if timestamp > self.curve_timestamp:
        self.curve_timestamp = timestamp
        curve = self._decode_curve(self.profile_short)
        if curve is not None and timestamp > self.route_reset_timestamp:
          self._add_curve(curve)

    # Release each curvature spot as soon as its apex is passed. The following
    # lower-curvature spots then raise the target speed before the curve exit.
    self.curves = [curve for curve in self.curves if curve["target"] >= self.total_distance]
    candidates = []
    for curve in self.curves:
      if curve["speed"] >= 250:
        continue
      distance = curve["target"] - self.total_distance
      target_speed = max(self.curve_lower_limit, curve["speed"] * self.curve_speed_factor)
      safe_speed = UnitConverter.kph_to_ms(target_speed)
      decel_distance = max(0.0, distance - safe_speed * self.curve_control_end)
      preview_speed = UnitConverter.ms_to_kph(math.sqrt(safe_speed ** 2 + 2 * self.curve_decel_rate * decel_distance))
      candidates.append((preview_speed, distance, curve))

    if candidates:
      _, distance, curve = min(candidates, key=lambda item: item[0])
      ret.naviCurveDistance = distance
      ret.naviCurveSpeed = curve["speed"]
      ret.naviCurveCurvature = curve["curvature"]

  @staticmethod
  def _classify_profile(profile):
    if profile["profile_type"] != 16:
      return None

    value = profile["value"]
    if 0 < value <= 0x1ff:
      kind = value & 0xf
      speed_code = value >> 4
      if kind == 7 and profile["offset"] == 0 and 1 < speed_code <= 31:
        return "speed_limit_zone", (speed_code - 1) * 5, kind

    if not 0 < profile["offset"] <= NAVI_MAX_EVENT_DISTANCE:
      return None
    if value == 6:
      return "bump", 0, 6

    if not 0 < value <= 0x1ff:
      return None
    kind = value & 0xf
    speed_code = value >> 4
    if kind not in NAVI_CAMERA_KINDS or not 1 < speed_code <= 31:
      return None
    return "camera", (speed_code - 1) * 5, kind

  def _add_event(self, event_type, speed, kind, offset):
    target = self.total_distance + offset
    for event in self.events:
      if event["type"] == event_type and event["speed"] == speed and event["kind"] == kind and abs(event["target"] - target) < 20:
        event["target"] = target
        return

    self.events.append({"type": event_type, "speed": speed, "kind": kind, "target": target})
    self.events.sort(key=lambda item: item["target"])
    self.events = self.events[:NAVI_MAX_EVENTS]

  def _update_events(self, cp, ret, speed_limit_cam):
    ret.speedBumpDistance = 0.0
    ret.schoolZoneActive = False
    ret.naviActive = False
    ret.naviSectionActive = False
    ret.naviSpeed = 0.0
    profile_timestamp = self._message_timestamp(cp, "Hud_Navi_V2_PROLONG_E")
    self.available = self.available or profile_timestamp > 0
    ret.naviAvailable = self.available
    self.camera_target = None

    # 0x4B4 is periodic while the stock navigation is running. Its range
    # average speed is zero outside a section-camera zone and valid inside it.
    # It is therefore authoritative for the current section state; 0x4BE is
    # sparse future spot data and must not be used alone to hold this state.
    position_timestamp = self._message_timestamp(cp, "Hud_Navi_V2_POS_PE")
    position_seen = position_timestamp > 0
    position_age = int(getattr(cp, "_last_update_nanos", position_timestamp)) - position_timestamp
    position_recent = position_seen and 0 <= position_age <= NAVI_POSITION_TIMEOUT_NS
    range_avg_speed = (int(self.position.get("RangeAvgSpeed", 0))
                       if position_recent and self.position is not None else 0)
    range_section_active = 0 < range_avg_speed < 511

    if self.segment is not None:
      timestamp = self._message_timestamp(cp, "Hud_Navi_V2_SEG_E")
      if timestamp > self.segment_timestamp:
        self.segment_timestamp = timestamp
        segment = self._decode_segment(self.segment)
        if segment["functional_road_class"] != 7:
          self.road_class = segment["functional_road_class"]
        if segment["calculated_route"] == 1:
          if self.curve_route_state != 1:
            self._clear_curves()
          self.curve_route_state = 1
          self.curve_route_active = True
        elif segment["calculated_route"] == 0:
          if self.curve_route_state != 0:
            self._clear_curves()
          self.curve_route_state = 0
          self.curve_route_active = False
        if segment["calculated_route"] == 2:
          self.curve_route_state = 2
          self.curve_route_active = False
          self.route_reset_timestamp = timestamp
          self._clear_events()
          self._clear_curves()
          self._clear_speed_zone()
          self._clear_school_zone()

    self._update_curve_profile(cp, ret)
    on_controlled_access_road = self._is_controlled_access_road()
    if on_controlled_access_road:
      self._clear_school_zone()
    if not (self.can_control or self.school_zone_control):
      return False

    if self.profile is not None:
      timestamp = profile_timestamp
      if timestamp > self.profile_timestamp:
        self.profile_timestamp = timestamp
        profile = self._decode_profile(self.profile)
        event = self._classify_profile(profile)
        if event is not None and timestamp > self.route_reset_timestamp:
          if event[0] == "speed_limit_zone":
            if self.can_control and event[1] > 30:
              self.speed_zone_active = True
              self.speed_zone_speed = event[1]
            if self.school_zone_control:
              if event[1] == 30 and not on_controlled_access_road:
                self.school_zone_active = True
                self.school_zone_start_distance = self.total_distance
                self.school_zone_uses_camera_status = speed_limit_cam and ret.speedLimit == 30
              else:
                self._clear_school_zone()
          elif self.can_control and (not on_controlled_access_road or
                                     (event[0] != "bump" and not (event[0] == "camera" and event[1] == 30))):
            self._add_event(*event, profile["offset"])

    if position_seen:
      if not range_section_active or not self.can_control or 0 < ret.speedLimit <= 30:
        self._clear_speed_zone()
      elif 30 < ret.speedLimit < 255:
        self.speed_zone_active = True
        self.speed_zone_speed = ret.speedLimit

    self.events = [event for event in self.events
                   if event["target"] >= self.total_distance - NAVI_PASSED_EVENT_DISTANCE and
                   (not on_controlled_access_road or
                    (event["type"] != "bump" and not (event["type"] == "camera" and event["speed"] == 30)))]
    upcoming = [event for event in self.events if event["target"] > self.total_distance]

    bumps = [event for event in upcoming if event["type"] == "bump"]
    if bumps:
      ret.speedBumpDistance = bumps[0]["target"] - self.total_distance

    if self.speed_zone_active and (not position_seen and not speed_limit_cam):
      self._clear_speed_zone()

    if self.school_zone_active:
      camera_status_ended = (self.school_zone_uses_camera_status and
                             (not speed_limit_cam or ret.speedLimit != 30))
      distance_expired = self.total_distance - self.school_zone_start_distance >= NAVI_SCHOOL_ZONE_MAX_DISTANCE
      if camera_status_ended or distance_expired:
        self._clear_school_zone()

    if self.school_zone_control and self.school_zone_active and not on_controlled_access_road:
      ret.schoolZoneActive = True
      ret.speedLimit = 30
      if self.can_control:
        ret.naviActive = True
        ret.naviSpeed = 30
      return False

    if self.can_control and self.speed_zone_active:
      ret.naviActive = True
      ret.naviSectionActive = True
      ret.naviSpeed = self.speed_zone_speed

    cameras = [event for event in upcoming if event["type"] == "camera"]
    if cameras:
      camera = cameras[0]
      self.camera_target = camera["target"]
      ret.speedLimit = camera["speed"]
      ret.naviActive = True
      if ret.naviSpeed <= 0:
        ret.naviSpeed = camera["speed"]

    if bumps:
      ret.naviActive = True

    return bool(cameras)

  def update_speed_limit(self, ret, speed_limit_cam, distance_time_changed=None):
    if distance_time_changed is None:
      distance_time_changed = self._update_speed_camera_params()
    if self._is_controlled_access_road() and ret.speedLimit == 30:
      speed_limit_cam = False
    self.total_distance += ret.vEgo * DT_CTRL
    if ret.speedLimit > 0 and speed_limit_cam and self.can_control and self.camera_target is not None:
      self.speed_limit_distance = self.camera_target
      ret.speedLimitDistance = max(0.0, self.speed_limit_distance - self.total_distance)
    elif ret.speedLimit > 0 and speed_limit_cam:
      if distance_time_changed or self.speed_limit_distance <= self.total_distance:
        self.speed_limit_distance = self.total_distance + ret.speedLimit * self.speed_camera_distance_time
      self.speed_limit_distance = max(self.total_distance + 1, self.speed_limit_distance)
      ret.speedLimitDistance = self.speed_limit_distance - self.total_distance
    else:
      self.speed_limit_distance = self.total_distance
      ret.speedLimitDistance = 0.0

  def update(self, cp, ret, speed_limit_cam, position, segment, profile, profile_short=None, hda_info=None):
    self.hda_info = hda_info
    self.position = position
    self.segment = segment
    self.profile_short = profile_short
    self.profile = profile

    distance_time_changed = self._update_speed_camera_params()
    speed_limit_cam = self._update_events(cp, ret, speed_limit_cam) or speed_limit_cam
    self.update_speed_limit(ret, speed_limit_cam, distance_time_changed)
    return speed_limit_cam
