from collections import deque
from functools import lru_cache
import math
import time

from openpilot.selfdrive.addon.dashboard_mount import TimedVector

SAMPLE_MAX_AGE_NS = 250_000_000
DEVICE_MAX_AGE_NS = 1_500_000_000  # deviceState is published at 2 Hz.
SENSOR_BATCH_LIMIT = 128  # Catch up on queued packets after reopening the adjustment view.


class MountSensorStream:
  """Read the existing sensor publishers without adding a subscriber for each web client."""

  def __init__(self):
    from capnp import KjException
    from openpilot.cereal import messaging

    self.messaging = messaging
    self._read_errors = (messaging.IpcError, KjException)
    try:
      self.car_socket = messaging.sub_sock('carState', conflate=True)
      self.device_socket = messaging.sub_sock('deviceState', conflate=True)
    except messaging.IpcError as error:
      raise RuntimeError('차량 상태를 읽을 수 없습니다.') from error
    self.accel_socket = None
    self.gyro_socket = None
    self.car_event = None
    self.device_event = None
    self.acceleration: deque[TimedVector] = deque(maxlen=SENSOR_BATCH_LIMIT)
    self.gyroscope: deque[TimedVector] = deque(maxlen=SENSOR_BATCH_LIMIT)

  def _receive_imu(self, socket, service: str, field: str, history: deque[TimedVector]) -> None:
    for _ in range(SENSOR_BATCH_LIMIT):
      event = self.messaging.recv_one_or_none(socket)
      if event is None:
        break
      if not event.valid:
        continue
      sample = getattr(event, service)
      timestamp = sample.timestamp
      if not 0 <= time.monotonic_ns() - timestamp <= SAMPLE_MAX_AGE_NS:
        continue
      if (history and timestamp <= history[-1][0]) or sample.which() != field:
        continue
      values = getattr(sample, field).v
      if len(values) == 3:
        x, y, z = values
        # Same sensor-to-camera forward/right/down frame transform as locationd.
        history.append((timestamp, (-z, -y, -x)))

  def read(self, after_accel_ns: int, after_gyro_ns: int) -> tuple[list[TimedVector], list[TimedVector]] | None:
    try:
      return self._read(after_accel_ns, after_gyro_ns)
    except self._read_errors as error:
      raise RuntimeError('센서 또는 차량 상태를 읽을 수 없습니다.') from error

  def _read(self, after_accel_ns: int, after_gyro_ns: int) -> tuple[list[TimedVector], list[TimedVector]] | None:
    self.car_event = self.messaging.recv_one_or_none(self.car_socket) or self.car_event
    self.device_event = self.messaging.recv_one_or_none(self.device_socket) or self.device_event
    now = time.monotonic_ns()
    if self.device_event is None or not self.device_event.valid or not 0 <= now - self.device_event.logMonoTime <= DEVICE_MAX_AGE_NS:
      return None
    if not self.device_event.deviceState.started:
      raise RuntimeError('시동이 켜진 정차 상태에서 장착 조정을 시작하세요.')

    if self.car_event is None or not self.car_event.valid or not 0 <= now - self.car_event.logMonoTime <= SAMPLE_MAX_AGE_NS:
      return None
    car_state = self.car_event.carState
    if not car_state.canValid or car_state.canTimeout or not all(math.isfinite(v) for v in (car_state.vEgo, car_state.vEgoRaw)):
      return None
    if not car_state.standstill or max(abs(car_state.vEgo), abs(car_state.vEgoRaw)) >= 0.1:
      raise RuntimeError('차량 움직임이 감지됐습니다. 정차 후 기준을 다시 잡으세요.')

    # Keep the subscriptions after first use. Reconnecting consumes more native reader slots.
    if self.accel_socket is None:
      self.accel_socket = self.messaging.sub_sock('accelerometer')
    if self.gyro_socket is None:
      self.gyro_socket = self.messaging.sub_sock('gyroscope')
    self._receive_imu(self.accel_socket, 'accelerometer', 'acceleration', self.acceleration)
    self._receive_imu(self.gyro_socket, 'gyroscope', 'gyroUncalibrated', self.gyroscope)
    now = time.monotonic_ns()
    if any(not history or not 0 <= now - history[-1][0] <= SAMPLE_MAX_AGE_NS for history in (self.acceleration, self.gyroscope)):
      return None
    return (
      [sample for sample in self.acceleration if after_accel_ns < sample[0] and 0 <= now - sample[0] <= SAMPLE_MAX_AGE_NS],
      [sample for sample in self.gyroscope if after_gyro_ns < sample[0] and 0 <= now - sample[0] <= SAMPLE_MAX_AGE_NS],
    )


@lru_cache(maxsize=1)
def get_mount_sensor_stream() -> MountSensorStream:
  return MountSensorStream()
