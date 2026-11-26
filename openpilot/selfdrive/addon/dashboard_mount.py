from heapq import merge
import math


Vector = tuple[float, float, float]
TimedVector = tuple[int, Vector]

_GRAVITY = 9.81
_GRAVITY_TOLERANCE = 0.8  # m/s²
_MAX_STILL_ACCEL_CHANGE = 0.4  # m/s²
_MAX_STILL_GYRO = math.radians(3.)
_MAX_GYRO = 10.0  # rad/s, same sanity limit as locationd
_REFERENCE_NS = 500_000_000
_MIN_REFERENCE_SAMPLES = 8
_MAX_GAP_NS = 150_000_000
_GRAVITY_FILTER_S = 0.20
_MIN_HORIZONTAL_PROJECTION = 0.20


def _mean_update(mean: Vector, sample: Vector, count: int) -> Vector:
  x, y, z = mean
  sx, sy, sz = sample
  return x + (sx - x) / count, y + (sy - y) / count, z + (sz - z) / count


def _pitch(down: Vector) -> float:
  return math.atan2(down[0], math.hypot(down[1], down[2]))


class MountAdjustment:
  """Track changes from a stationary reference using camera-frame IMU samples."""

  def __init__(self):
    self._last_accel_ns = 0
    self._last_gyro_ns = 0
    self._clear_reference()
    self._down: Vector | None = None
    self._baseline_pitch = 0.
    self._yaw = 0.
    self._previous_yaw_rate = 0.

  def _clear_reference(self):
    self._still_accel_start_ns = 0
    self._still_gyro_start_ns = 0
    self._still_accel_count = 0
    self._still_gyro_count = 0
    self._mean_accel: Vector = (0., 0., 0.)
    self._gyro_bias: Vector = (0., 0., 0.)

  def _observe_acceleration(self, timestamp_ns: int, sample: Vector, dt: float):
    magnitude = math.hypot(*sample)
    if abs(magnitude - _GRAVITY) > _GRAVITY_TOLERANCE:
      if self._down is None:
        self._clear_reference()
      return

    if self._down is None:
      if self._still_accel_count and math.dist(sample, self._mean_accel) > _MAX_STILL_ACCEL_CHANGE:
        self._clear_reference()
      if not self._still_accel_count:
        self._still_accel_start_ns = timestamp_ns
      self._still_accel_count += 1
      self._mean_accel = _mean_update(self._mean_accel, sample, self._still_accel_count)
    else:
      # The accelerometer measures specific force, which points up at rest.
      ax, ay, az = sample
      gx, gy, gz = self._down
      weight = dt / (_GRAVITY_FILTER_S + dt)
      x = (1. - weight) * gx - weight * ax / magnitude
      y = (1. - weight) * gy - weight * ay / magnitude
      z = (1. - weight) * gz - weight * az / magnitude
      norm = math.hypot(x, y, z)
      if norm < _MIN_HORIZONTAL_PROJECTION or math.hypot(y, z) < norm * _MIN_HORIZONTAL_PROJECTION:
        raise ValueError('Camera direction is nearly vertical')
      self._down = x / norm, y / norm, z / norm

  def _observe_gyroscope(self, timestamp_ns: int, sample: Vector, dt: float):
    if self._down is None:
      if math.hypot(*sample) > _MAX_STILL_GYRO:
        self._clear_reference()
        return
      if not self._still_gyro_count:
        self._still_gyro_start_ns = timestamp_ns
      self._still_gyro_count += 1
      self._gyro_bias = _mean_update(self._gyro_bias, sample, self._still_gyro_count)
    else:
      _, y, z = self._down
      omega_y = sample[1] - self._gyro_bias[1]
      omega_z = sample[2] - self._gyro_bias[2]
      # Heading depends on gravity's lateral/vertical components. Rotation around
      # the camera's forward axis does not change where it points.
      yaw_rate = -(y * omega_y + z * omega_z) / (y * y + z * z)
      self._yaw += 0.5 * (self._previous_yaw_rate + yaw_rate) * dt
      self._previous_yaw_rate = yaw_rate

  def _capture_reference(self):
    if min(self._still_accel_count, self._still_gyro_count) < _MIN_REFERENCE_SAMPLES:
      return
    end = min(self._last_accel_ns, self._last_gyro_ns)
    start = max(self._still_accel_start_ns, self._still_gyro_start_ns)
    if end - start < _REFERENCE_NS:
      return

    x, y, z = self._mean_accel
    norm = math.hypot(x, y, z)
    if norm < _MIN_HORIZONTAL_PROJECTION or math.hypot(y, z) < norm * _MIN_HORIZONTAL_PROJECTION:
      raise ValueError('Camera direction is nearly vertical')
    self._down = -x / norm, -y / norm, -z / norm
    self._baseline_pitch = _pitch(self._down)

  def update(self, acceleration: list[TimedVector], gyroscope: list[TimedVector]) -> tuple[float, float] | None:
    """Receive nanosecond timestamps and FRD vectors in m/s² or rad/s.

    Return pitch down and heading left in degrees from the captured reference.
    """
    accel_samples = ((timestamp, False, sample) for timestamp, sample in acceleration)
    gyro_samples = ((timestamp, True, sample) for timestamp, sample in gyroscope)
    for timestamp_ns, is_gyro, sample in merge(accel_samples, gyro_samples, key=lambda event: event[0]):
      previous = self._last_gyro_ns if is_gyro else self._last_accel_ns
      if timestamp_ns <= previous or len(sample) != 3 or not all(math.isfinite(value) for value in sample):
        raise ValueError('Invalid or out-of-order IMU sample')
      if is_gyro and math.hypot(*sample) >= _MAX_GYRO:
        raise ValueError('Gyroscope sample is outside its valid range')
      if previous and timestamp_ns - previous > _MAX_GAP_NS:
        if self._down is not None:
          raise ValueError('IMU samples were interrupted')
        # If the other source already restarted capture, retain its newer samples.
        still_samples = self._still_gyro_count if is_gyro else self._still_accel_count
        if still_samples:
          self._clear_reference()
      dt = (timestamp_ns - previous) * 1e-9 if previous else 0.

      if is_gyro:
        self._last_gyro_ns = timestamp_ns
        self._observe_gyroscope(timestamp_ns, sample, dt)
      else:
        self._last_accel_ns = timestamp_ns
        self._observe_acceleration(timestamp_ns, sample, dt)

      if self._down is None:
        self._capture_reference()
      elif abs(self._last_accel_ns - self._last_gyro_ns) > _MAX_GAP_NS:
        raise ValueError('IMU samples were interrupted')

    if self._down is None:
      return None
    return math.degrees(_pitch(self._down) - self._baseline_pitch), math.degrees(self._yaw)
