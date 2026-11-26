import math

import pytest

from openpilot.selfdrive.addon.dashboard_mount import MountAdjustment


_STEP_NS = 10_000_000
_START_NS = 1_000_000_000
_LEVEL_FORCE = (0., 0., -9.81)
_RESTING_GYRO = (0., 0., 0.)


def samples(start, count, vector):
  return [(_START_NS + index * _STEP_NS, vector) for index in range(start, start + count)]


def update(adjustment, start, count, force=_LEVEL_FORCE, gyro=_RESTING_GYRO):
  return adjustment.update(samples(start, count, force), samples(start, count, gyro))


def test_reference_needs_half_second_of_still_samples():
  adjustment = MountAdjustment()
  assert update(adjustment, 0, 50) is None
  assert update(adjustment, 50, 1) == (0., 0.)


def test_moving_during_reference_restarts_the_still_window():
  adjustment = MountAdjustment()
  assert update(adjustment, 0, 40) is None
  assert update(adjustment, 40, 10, gyro=(0., .45, 0.)) is None
  assert update(adjustment, 50, 50) is None
  assert update(adjustment, 100, 1) == (0., 0.)


def test_static_bias_does_not_accumulate_heading_change():
  adjustment = MountAdjustment()
  bias = (.005, -.008, .014)
  assert update(adjustment, 0, 51, gyro=bias) == (0., 0.)
  assert update(adjustment, 51, 100, gyro=bias) == (0., 0.)


@pytest.mark.parametrize('degrees', [8., -8.])
def test_pitch_increases_when_lens_points_down(degrees):
  adjustment = MountAdjustment()
  update(adjustment, 0, 51)
  angle = math.radians(degrees)
  tilted_force = (-9.81 * math.sin(angle), 0., -9.81 * math.cos(angle))
  assert update(adjustment, 51, 160, force=tilted_force) == pytest.approx((degrees, 0.), abs=.01)


@pytest.mark.parametrize('degrees', [12., -12.])
def test_heading_compensates_for_mount_pitch_and_roll(degrees):
  adjustment = MountAdjustment()
  pitch = math.radians(25.)
  roll = math.radians(17.)
  down = (math.sin(pitch), math.cos(pitch) * math.sin(roll), math.cos(pitch) * math.cos(roll))
  force = tuple(-9.81 * value for value in down)
  update(adjustment, 0, 51, force=force)
  turn = tuple(-math.radians(degrees) * value for value in down)
  assert update(adjustment, 51, 100, force=force, gyro=turn) == pytest.approx((0., degrees), abs=.07)
  # A stationary sample completes the trapezoid at the end of the turn.
  assert update(adjustment, 151, 1, force=force) == pytest.approx((0., degrees))


def test_optical_roll_does_not_generate_heading():
  adjustment = MountAdjustment()
  force = (-3., 0., -math.sqrt(9.81**2 - 9.))
  update(adjustment, 0, 51, force=force)
  assert update(adjustment, 51, 100, force=force, gyro=(.3, 0., 0.)) == pytest.approx((0., 0.))


def test_stale_gap_during_reference_restarts_capture():
  adjustment = MountAdjustment()
  assert update(adjustment, 0, 40) is None
  assert update(adjustment, 80, 50) is None
  assert update(adjustment, 130, 1) == (0., 0.)


def test_active_measurement_rejects_interruption():
  adjustment = MountAdjustment()
  assert update(adjustment, 0, 51) == (0., 0.)
  with pytest.raises(ValueError, match='interrupted'):
    adjustment.update(samples(51, 20, _LEVEL_FORCE), [])


@pytest.mark.parametrize('accel, gyro', [
  (samples(50, 1, _LEVEL_FORCE), []),
  ([], samples(51, 1, (0., math.nan, 0.))),
])
def test_active_measurement_rejects_invalid_or_duplicate_samples(accel, gyro):
  adjustment = MountAdjustment()
  update(adjustment, 0, 51)
  with pytest.raises(ValueError, match='Invalid or out-of-order'):
    adjustment.update(accel, gyro)


def test_nearly_vertical_reference_is_unavailable():
  adjustment = MountAdjustment()
  with pytest.raises(ValueError, match='vertical'):
    update(adjustment, 0, 51, force=(-9.81, 0., 0.))
