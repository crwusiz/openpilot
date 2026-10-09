from opendbc.car import DT_CTRL


NAVI_SPEED_CAMERA_PARAM_UPDATE_FRAMES = round(1.0 / DT_CTRL)
NAVI_MAX_EVENT_DISTANCE = 2500.0
NAVI_PASSED_EVENT_DISTANCE = 30.0
NAVI_MAX_EVENTS = 32
NAVI_CAMERA_KINDS = (0, 1, 2)
NAVI_CONTROLLED_ACCESS_LINK_CLASSES = (1, 2, 3)  # Freeway, IC, JC
NAVI_CONTROLLED_ACCESS_ROAD_CLASSES = (1, 2)  # Freeway, arterial/city freeway
NAVI_SCHOOL_ZONE_MAX_DISTANCE = 1000.0
NAVI_POSITION_TIMEOUT_NS = 1_000_000_000


class NaviState:
  def __init__(self, params=None):
    self.params = params
    self.total_distance = 0.0
    self.speed_limit_distance = 0.0

    self.speed_camera_distance_time = 6.0
    self.can_control = True
    self.school_zone_control = True
    self.speed_camera_params_counter = 0
    self.events = []
    self.segment_timestamp = 0
    self.profile_timestamp = 0
    self.available = False
    self.route_reset_timestamp = 0
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
    self.profile = None

  @staticmethod
  def _speed_camera_distance_time(raw_value):
    return min(200, max(10, raw_value)) / 10.0

  def _update_speed_camera_params(self):
    self.speed_camera_params_counter += 1
    if self.speed_camera_params_counter < NAVI_SPEED_CAMERA_PARAM_UPDATE_FRAMES:
      return False

    self.speed_camera_params_counter = 0
    distance_time_tenths = 60
    distance_time = self._speed_camera_distance_time(distance_time_tenths)
    changed = distance_time != self.speed_camera_distance_time
    self.speed_camera_distance_time = distance_time
    return changed

  def _clear_events(self):
    self.events = []
    self.camera_target = None

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
        if segment["calculated_route"] == 2:
          self.route_reset_timestamp = timestamp
          self._clear_events()
          self._clear_speed_zone()
          self._clear_school_zone()

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

  def update(self, cp, ret, speed_limit_cam, position, segment, profile, hda_info=None):
    self.hda_info = hda_info
    self.position = position
    self.segment = segment
    self.profile = profile

    distance_time_changed = self._update_speed_camera_params()
    speed_limit_cam = self._update_events(cp, ret, speed_limit_cam) or speed_limit_cam
    self.update_speed_limit(ret, speed_limit_cam, distance_time_changed)
    return speed_limit_cam
