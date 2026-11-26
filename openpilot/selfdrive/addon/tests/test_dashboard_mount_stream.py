from collections import defaultdict, deque
import math
import sys
from types import ModuleType, SimpleNamespace

from capnp import KjException
import pytest

from openpilot.selfdrive.addon import dashboard_mount_stream as mount_stream


class FakeMessaging:
  class IpcError(Exception):
    pass

  def __init__(self):
    self.now_ns = 10_000_000_000
    self.sockets = defaultdict(list)

  def sub_sock(self, service, *, conflate=False):
    socket = SimpleNamespace(conflate=conflate, queue=deque())
    self.sockets[service].append(socket)
    return socket

  def publish(self, service, event):
    for socket in self.sockets[service]:
      if socket.conflate:
        socket.queue.clear()
      socket.queue.append(event)

  def recv_one_or_none(self, socket):
    return socket.queue.popleft() if socket.queue else None

  def car(self, age_ns=0, valid=True, **overrides):
    state = {'canValid': True, 'canTimeout': False, 'standstill': True, 'vEgo': 0., 'vEgoRaw': 0.} | overrides
    self.publish('carState', SimpleNamespace(logMonoTime=self.now_ns - age_ns, valid=valid, carState=SimpleNamespace(**state)))

  def device(self, age_ns=0, valid=True, started=True):
    self.publish('deviceState', SimpleNamespace(logMonoTime=self.now_ns - age_ns, valid=valid,
                                                deviceState=SimpleNamespace(started=started)))

  def imu(self, service, timestamp_ns, vector=(1., 2., 3.), valid=True, field=None):
    field = field or ('acceleration' if service == 'accelerometer' else 'gyroUncalibrated')
    sample = SimpleNamespace(timestamp=timestamp_ns, which=lambda: field, **{field: SimpleNamespace(v=vector)})
    self.publish(service, SimpleNamespace(valid=valid, **{service: sample}))


@pytest.fixture
def sensor_stream(monkeypatch):
  messaging = FakeMessaging()
  package = ModuleType('openpilot.cereal')
  package.messaging = messaging
  monkeypatch.setitem(sys.modules, 'openpilot.cereal', package)
  monkeypatch.setitem(sys.modules, 'openpilot.cereal.messaging', messaging)
  monkeypatch.setattr(mount_stream, 'time', SimpleNamespace(monotonic_ns=lambda: messaging.now_ns))
  return mount_stream.MountSensorStream(), messaging


def ready(stream, messaging):
  messaging.device()
  messaging.car()
  assert stream.read(0, 0) is None


@pytest.mark.parametrize('state', [
  None,
  {'valid': False},
  {'age_ns': -1},
  {'age_ns': mount_stream.DEVICE_MAX_AGE_NS + 1},
])
def test_waits_for_a_valid_fresh_device_state(sensor_stream, state):
  stream, messaging = sensor_stream
  messaging.car()
  if state is not None:
    messaging.device(**state)

  assert stream.read(0, 0) is None
  assert not messaging.sockets['accelerometer']
  assert not messaging.sockets['gyroscope']


def test_requires_device_started(sensor_stream):
  stream, messaging = sensor_stream
  messaging.device(started=False)
  messaging.car()

  with pytest.raises(RuntimeError, match='시동'):
    stream.read(0, 0)
  assert not messaging.sockets['gyroscope']


@pytest.mark.parametrize('state', [
  None,
  {'valid': False},
  {'age_ns': -1},
  {'age_ns': mount_stream.SAMPLE_MAX_AGE_NS + 1},
  {'canValid': False},
  {'canTimeout': True},
  {'vEgo': math.nan},
  {'vEgoRaw': math.inf},
])
def test_waits_for_valid_fresh_vehicle_safety_data(sensor_stream, state):
  stream, messaging = sensor_stream
  messaging.device()
  if state is not None:
    messaging.car(**state)

  assert stream.read(0, 0) is None
  assert not messaging.sockets['gyroscope']


@pytest.mark.parametrize('state', [
  {'standstill': False},
  {'vEgo': 0.1},
  {'vEgoRaw': -0.11},
])
def test_vehicle_motion_blocks_measurement(sensor_stream, state):
  stream, messaging = sensor_stream
  messaging.device()
  messaging.car(**state)

  with pytest.raises(RuntimeError, match='움직임'):
    stream.read(0, 0)
  assert not messaging.sockets['gyroscope']


def test_imu_samples_are_in_camera_frame_and_each_reader_controls_its_cursor(sensor_stream):
  stream, messaging = sensor_stream
  ready(stream, messaging)
  early_ns = messaging.now_ns - 20_000_000
  late_ns = messaging.now_ns - 10_000_000
  messaging.imu('accelerometer', 0)
  messaging.imu('accelerometer', early_ns - 2, field='gyro')
  messaging.imu('accelerometer', early_ns - 1, vector=(1., 2.))
  messaging.imu('accelerometer', early_ns, vector=(1., 2., 3.))
  messaging.imu('accelerometer', early_ns, vector=(0., 0., 0.))
  messaging.imu('accelerometer', late_ns, vector=(4., 5., 6.))
  messaging.imu('gyroscope', early_ns, valid=False)
  messaging.imu('gyroscope', late_ns, vector=(.1, -.2, .3))

  accel_old = (early_ns, (-3., -2., -1.))
  accel_new = (late_ns, (-6., -5., -4.))
  gyro_new = (late_ns, (-.3, .2, -.1))
  assert stream.read(0, 0) == ([accel_old, accel_new], [gyro_new])
  assert stream.read(early_ns, late_ns) == ([accel_new], [])
  assert stream.read(late_ns, 0) == ([], [gyro_new])
  assert stream.read(late_ns, late_ns) == ([], [])
  assert len(messaging.sockets['accelerometer']) == len(messaging.sockets['gyroscope']) == 1


@pytest.mark.parametrize('timestamp_offset', [None, -(mount_stream.SAMPLE_MAX_AGE_NS + 1), 1])
def test_waits_for_both_fresh_imu_streams(sensor_stream, timestamp_offset):
  stream, messaging = sensor_stream
  ready(stream, messaging)
  messaging.imu('accelerometer', messaging.now_ns)
  if timestamp_offset is not None:
    messaging.imu('gyroscope', messaging.now_ns + timestamp_offset)

  assert stream.read(0, 0) is None


def test_resume_skips_stale_backlog_without_opening_more_msgq_readers(sensor_stream):
  stream, messaging = sensor_stream
  ready(stream, messaging)
  initial_ns = messaging.now_ns
  for service in ('accelerometer', 'gyroscope'):
    messaging.imu(service, initial_ns)
  assert stream.read(0, 0) is not None

  messaging.now_ns += 2 * mount_stream.SAMPLE_MAX_AGE_NS
  messaging.car()
  messaging.device()
  for service in ('accelerometer', 'gyroscope'):
    for index in range(mount_stream.SENSOR_BATCH_LIMIT + 1):
      messaging.imu(service, initial_ns + index + 1)
    messaging.imu(service, messaging.now_ns)

  assert stream.read(initial_ns, initial_ns) is None
  expected = [(messaging.now_ns, (-3., -2., -1.))]
  assert stream.read(initial_ns, initial_ns) == (expected, expected)
  assert all(len(sockets) == 1 for sockets in messaging.sockets.values())


def test_future_sensor_timestamp_does_not_prevent_a_fresh_pair(sensor_stream):
  stream, messaging = sensor_stream
  ready(stream, messaging)
  messaging.imu('accelerometer', messaging.now_ns)
  messaging.imu('gyroscope', messaging.now_ns + mount_stream.SAMPLE_MAX_AGE_NS + 1)
  assert stream.read(0, 0) is None

  messaging.imu('gyroscope', messaging.now_ns)
  expected = [(messaging.now_ns, (-3., -2., -1.))]
  assert stream.read(0, 0) == (expected, expected)


def test_vehicle_motion_stops_an_active_reader(sensor_stream):
  stream, messaging = sensor_stream
  ready(stream, messaging)
  messaging.imu('accelerometer', messaging.now_ns)
  messaging.imu('gyroscope', messaging.now_ns)
  assert stream.read(0, 0) is not None

  messaging.now_ns += 50_000_000
  messaging.car(vEgoRaw=.2)
  with pytest.raises(RuntimeError, match='움직임'):
    stream.read(0, 0)


def test_active_reader_reports_sensor_loss_without_replaying_old_samples(sensor_stream):
  stream, messaging = sensor_stream
  ready(stream, messaging)
  initial_ns = messaging.now_ns
  for service in ('accelerometer', 'gyroscope'):
    messaging.imu(service, initial_ns)
  assert stream.read(0, 0) is not None

  cursor = initial_ns
  for delta_ns in (100_000_000, 200_000_000):
    messaging.now_ns = initial_ns + delta_ns
    messaging.car()
    messaging.imu('accelerometer', messaging.now_ns)
    expected_accel = (messaging.now_ns, (-3., -2., -1.))
    assert stream.read(cursor, initial_ns) == ([expected_accel], [])
    cursor = expected_accel[0]

  messaging.now_ns = initial_ns + mount_stream.SAMPLE_MAX_AGE_NS + 1
  messaging.car()
  messaging.imu('accelerometer', messaging.now_ns)
  assert stream.read(cursor, initial_ns) is None


@pytest.mark.parametrize('error_type', [FakeMessaging.IpcError, KjException])
def test_read_reports_native_sensor_errors(sensor_stream, error_type, monkeypatch):
  stream, messaging = sensor_stream
  ready(stream, messaging)
  recv = messaging.recv_one_or_none
  gyro_socket, = messaging.sockets['gyroscope']

  def recv_with_error(socket):
    if socket is gyro_socket:
      raise error_type('failed gyroscope read')
    return recv(socket)

  monkeypatch.setattr(messaging, 'recv_one_or_none', recv_with_error)
  with pytest.raises(RuntimeError, match='센서 또는 차량 상태') as caught:
    stream.read(0, 0)
  assert isinstance(caught.value.__cause__, error_type)
