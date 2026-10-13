import math


NAVI_SERVICE_MESSAGE = b"EON:ROAD_LIMIT_SERVICE:v2"
CAMERA_SOURCE_TIMEOUT = 3.0
REAR_CAMERA_TYPES = {
  75: "후면 과속 단속",
  76: "후면 신호·과속 단속",
  86: "후면 구간단속 시작",
  87: "후면 구간단속 종료",
  88: "후면 신호·구간단속 시작",
  89: "후면 신호·구간단속 종료",
}


def _has_rear_metadata(navi_data):
  return int(getattr(navi_data, "camType", 0)) in REAR_CAMERA_TYPES and any(
    getattr(navi_data, key, 0) != 0 for key in ("camRearHoldDist", "camDistanceAge", "camDistanceId")
  )


def camera_limit_distance(navi_data):
  """Return published, already age-compensated distance; never compensate it again."""
  if _has_rear_metadata(navi_data):
    return float(getattr(navi_data, "camLimitSpeedLeftDistPrecise", getattr(navi_data, "camLimitSpeedLeftDist", 0)))
  return float(getattr(navi_data, "camLimitSpeedLeftDist", 0))


def camera_limit_active(navi_data):
  """Point camera validity, including the signed rear-camera hold interval."""
  if navi_data is None or getattr(navi_data, "camLimitSpeed", 0) <= 0:
    return False
  distance = camera_limit_distance(navi_data)
  if not math.isfinite(distance):
    return False
  if _has_rear_metadata(navi_data):
    hold = float(getattr(navi_data, "camRearHoldDist", 0))
    age = float(getattr(navi_data, "camDistanceAge", 0))
    sample_id = getattr(navi_data, "camDistanceId", 0)
    return (math.isfinite(hold) and hold > 0 and math.isfinite(age) and 0 <= age < CAMERA_SOURCE_TIMEOUT
            and isinstance(sample_id, int) and not isinstance(sample_id, bool) and 0 < sample_id <= 2**63 - 1 and distance >= -hold)
  return distance > 0


def rear_camera_hold_active(navi_data):
  return camera_limit_active(navi_data) and _has_rear_metadata(navi_data) and camera_limit_distance(navi_data) <= 0


class CameraDistanceTracker:
  """Keep a source distance sample alive only for its own lifetime, even on UDP retransmission."""
  def __init__(self):
    self.reset()

  def reset(self):
    self.last_time = None
    self.last_raw_distance = None
    self.frozen_time = 0.0
    self.source_id = 0
    self.source_distance = 0.0
    self.source_signature = None
    self.travelled = 0.0
    self.effective_age = 0.0
    self.expired = False

  def update(self, raw_distance, cam_type, cam_speed, rear_hold_dist, source_age, source_id, received_at, now, v_ego):
    dt = max(now - self.last_time, 0.0) if self.last_time is not None else 0.0
    self.last_time = now
    speed = max(float(v_ego), 0.0) if math.isfinite(v_ego) else 0.0
    since_received = max(now - received_at, 0.0)
    rear_metadata = cam_type in REAR_CAMERA_TYPES and (rear_hold_dist != 0 or source_age != 0 or source_id != 0)

    if raw_distance > 0 or rear_metadata:
      if raw_distance == self.last_raw_distance:
        if speed > 1.0:
          self.frozen_time += dt
      else:
        self.frozen_time = 0.0
      self.last_raw_distance = raw_distance
    else:
      self.last_raw_distance = None
      self.frozen_time = 0.0

    if not rear_metadata:
      if self.source_id:
        self.expired = True
        self.effective_age += dt
      distance = max(raw_distance - speed * since_received, 0.0)
      valid = cam_speed > 0 and distance > 0 and self.frozen_time <= CAMERA_SOURCE_TIMEOUT
      return (distance if valid else 0.0), 0.0, valid

    packet_age = source_age + since_received
    valid_source_id = isinstance(source_id, int) and not isinstance(source_id, bool) and 0 < source_id <= 2**63 - 1
    if not (math.isfinite(raw_distance) and math.isfinite(rear_hold_dist) and rear_hold_dist > 0
            and math.isfinite(source_age) and source_age >= 0 and valid_source_id and cam_speed > 0):
      if valid_source_id and source_id >= self.source_id:
        self.source_id = source_id
        self.expired = True
        self.effective_age = max(self.effective_age + dt, packet_age)
        return 0.0, self.effective_age, False
      return 0.0, packet_age, False
    if source_id < self.source_id:
      return 0.0, packet_age, False

    signature = (cam_type, cam_speed, rear_hold_dist)
    if source_id != self.source_id:
      self.source_id = source_id
      self.source_distance = raw_distance
      self.source_signature = signature
      self.travelled = speed * packet_age
      self.effective_age = packet_age
      self.expired = False
    else:
      if signature != self.source_signature:
        self.expired = True
        self.effective_age = max(self.effective_age + dt, packet_age)
        return 0.0, self.effective_age, False
      next_age = self.effective_age + dt
      self.travelled += speed * (dt + max(packet_age - next_age, 0.0))
      self.effective_age = max(next_age, packet_age)

    distance = self.source_distance - self.travelled
    self.expired = (self.expired or self.effective_age >= CAMERA_SOURCE_TIMEOUT or distance < -rear_hold_dist
                    or self.frozen_time > CAMERA_SOURCE_TIMEOUT)
    return (0.0 if self.expired else distance), self.effective_age, not self.expired
