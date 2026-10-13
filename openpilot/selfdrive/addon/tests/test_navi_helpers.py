from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.navi_helpers import CameraDistanceTracker, camera_limit_active, camera_limit_distance, rear_camera_hold_active


REAR_CAMERA_TYPES = (75, 76, 86, 87, 88, 89)


def make_camera(cam_type=75, *, distance=-10.0, speed=70, **overrides):
  values = {
    "camType": cam_type,
    "camLimitSpeed": speed,
    "camLimitSpeedLeftDist": int(distance),
    "camLimitSpeedLeftDistPrecise": distance,
    "camRearHoldDist": 87.5,
    "camDistanceAge": 0.25,
    "camDistanceId": 1234567890123456,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
@pytest.mark.parametrize("distance", [100.0, 0.0, -0.9, -87.5])
def test_rear_camera_keeps_signed_distance_and_hold_boundary(cam_type, distance):
  data = make_camera(cam_type, distance=distance)
  assert camera_limit_distance(data) == distance
  assert camera_limit_active(data)
  assert rear_camera_hold_active(data) == (distance <= 0)


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
def test_fractional_distance_after_hold_boundary_releases_camera(cam_type):
  data = make_camera(cam_type, distance=-87.5001)
  assert data.camLimitSpeedLeftDist == -87
  assert camera_limit_distance(data) == -87.5001
  assert not camera_limit_active(data)
  assert not rear_camera_hold_active(data)


@pytest.mark.parametrize("distance", [100.0, 0.0, -10.0])
@pytest.mark.parametrize("override", [
  {"camDistanceAge": 3.0},
  {"camDistanceAge": -0.1},
  {"camDistanceAge": float("nan")},
  {"camDistanceAge": float("inf")},
  {"camDistanceId": 0},
  {"camRearHoldDist": 0.0},
  {"camRearHoldDist": -1.0},
  {"camRearHoldDist": float("nan")},
])
def test_stale_or_incomplete_rear_metadata_rejects_positive_and_passed_distance(distance, override):
  data = make_camera(distance=distance, **override)
  assert not camera_limit_active(data)
  assert not rear_camera_hold_active(data)


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
@pytest.mark.parametrize("distance", [100, 0, -10])
def test_legacy_rear_messages_keep_positive_distance_semantics(cam_type, distance):
  data = SimpleNamespace(camType=cam_type, camLimitSpeed=70, camLimitSpeedLeftDist=distance)
  assert camera_limit_distance(data) == distance
  assert camera_limit_active(data) == (distance > 0)
  assert not rear_camera_hold_active(data)


def test_new_reader_defaults_keep_legacy_positive_distance_semantics():
  data = make_camera(distance=0.0, camLimitSpeedLeftDist=100, camRearHoldDist=0.0, camDistanceAge=0.0, camDistanceId=0)
  assert camera_limit_distance(data) == 100
  assert camera_limit_active(data)
  assert not rear_camera_hold_active(data)


def test_rear_message_without_precise_field_uses_legacy_distance():
  data = make_camera(distance=-10)
  del data.camLimitSpeedLeftDistPrecise
  assert camera_limit_distance(data) == -10
  assert camera_limit_active(data)
  assert rear_camera_hold_active(data)


@pytest.mark.parametrize("cam_type", [2, 3, 4, 20, 21, 22])
@pytest.mark.parametrize("distance", [100, 0, -10])
def test_front_cameras_school_zones_and_bumps_keep_positive_distance_semantics(cam_type, distance):
  data = make_camera(cam_type, distance=distance)
  assert camera_limit_distance(data) == distance
  assert camera_limit_active(data) == (distance > 0)
  assert not rear_camera_hold_active(data)


@pytest.mark.parametrize("speed", [0, -1])
def test_rear_hold_requires_valid_camera_speed(speed):
  data = make_camera(speed=speed)
  assert not camera_limit_active(data)
  assert not rear_camera_hold_active(data)


def test_section_limit_is_separate_from_point_camera_helpers():
  data = make_camera(speed=0, sectionLimitSpeed=70, sectionLeftDist=1000)
  assert not camera_limit_active(data)
  assert not rear_camera_hold_active(data)


def test_empty_navigation_data_is_inactive():
  assert camera_limit_distance(None) == 0
  assert not camera_limit_active(None)
  assert not rear_camera_hold_active(None)


def update_tracker(tracker, *, raw_distance=100.0, cam_type=75, cam_speed=70, rear_hold_dist=87.5,
                   source_age=0.0, source_id=1234567890123456, received_at=100.0, now=100.0, v_ego=20.0):
  return tracker.update(raw_distance, cam_type, cam_speed, rear_hold_dist, source_age, source_id, received_at, now, v_ego)


def test_tracker_compensates_source_age_and_local_receiving_delay_once():
  tracker = CameraDistanceTracker()
  distance, age, valid = update_tracker(tracker, source_age=0.25, received_at=99.25)
  assert distance == pytest.approx(80.0)
  assert age == pytest.approx(1.0)
  assert valid


def test_tracker_duplicate_id_ignores_changed_distance_and_fresh_receiving_timestamp():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, source_age=0.5) == (90.0, 0.5, True)
  distance, age, valid = update_tracker(tracker, raw_distance=500.0, source_age=0.5, received_at=101.0, now=101.0)
  assert distance == pytest.approx(70.0)
  assert age == pytest.approx(1.5)
  assert valid


def test_tracker_duplicate_id_with_increased_source_age_does_not_double_compensate():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, source_age=0.5) == (90.0, 0.5, True)
  assert update_tracker(tracker, source_age=1.5, received_at=101.0, now=101.0) == (70.0, 1.5, True)


def test_tracker_integrates_changed_vehicle_speed_between_source_updates():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, v_ego=0.0) == (100.0, 0.0, True)
  assert update_tracker(tracker, received_at=100.5, now=100.5, v_ego=10.0) == (95.0, 0.5, True)
  assert update_tracker(tracker, received_at=101.0, now=101.0, v_ego=30.0) == (80.0, 1.0, True)


def test_tracker_expired_id_cannot_be_revived_by_regressed_age():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, source_age=2.5) == (50.0, 2.5, True)
  assert update_tracker(tracker, received_at=100.5, now=100.5) == (0.0, 3.0, False)
  distance, age, valid = update_tracker(tracker, raw_distance=500.0, source_age=0.0, received_at=101.0, now=101.0)
  assert distance == 0.0
  assert age >= 3.0
  assert not valid


def test_tracker_invalid_metadata_after_source_expiry_preserves_expired_age():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, source_age=3.0) == (0.0, 3.0, False)
  distance, age, valid = update_tracker(tracker, rear_hold_dist=0.0, source_age=0.0,
                                      received_at=100.5, now=100.5)
  assert distance == 0.0
  assert age >= 3.0
  assert not valid


def test_tracker_new_source_id_replaces_expired_sample():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, source_age=3.0) == (0.0, 3.0, False)
  assert update_tracker(tracker, raw_distance=200.0, source_age=0.25, source_id=1234567890123457,
                        received_at=101.0, now=101.0) == (195.0, 0.25, True)


def test_tracker_hold_boundary_is_inclusive_and_cannot_revive_after_release():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, raw_distance=-80.0, v_ego=10.0) == (-80.0, 0.0, True)
  assert update_tracker(tracker, raw_distance=-80.0, received_at=100.75, now=100.75, v_ego=10.0) == (-87.5, 0.75, True)
  distance, age, valid = update_tracker(tracker, raw_distance=-80.0, received_at=100.7501, now=100.7501, v_ego=10.0)
  assert distance == 0.0
  assert not valid
  distance, age, valid = update_tracker(tracker, raw_distance=-10.0, received_at=100.8, now=100.8, v_ego=0.0)
  assert distance == 0.0
  assert not valid


def test_tracker_missing_payload_cannot_revive_same_rear_source_id():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, raw_distance=-10.0) == (-10.0, 0.0, True)
  distance, age, valid = update_tracker(tracker, raw_distance=0.0, cam_type=0, cam_speed=0, rear_hold_dist=0.0,
                                      source_id=0, now=100.5)
  assert distance == 0.0
  assert not valid
  distance, age, valid = update_tracker(tracker, raw_distance=-10.0, received_at=101.0, now=101.0)
  assert distance == 0.0
  assert not valid


def test_tracker_invalid_new_source_id_cannot_be_repaired_by_same_id():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker) == (100.0, 0.0, True)
  distance, age, valid = update_tracker(tracker, source_id=1234567890123457, rear_hold_dist=0.0,
                                      received_at=100.5, now=100.5)
  assert distance == 0.0
  assert not valid
  distance, age, valid = update_tracker(tracker, source_id=1234567890123457, rear_hold_dist=87.5,
                                      received_at=101.0, now=101.0)
  assert distance == 0.0
  assert not valid
  assert update_tracker(tracker, source_id=1234567890123458,
                        received_at=101.5, now=101.5) == (100.0, 0.0, True)


def test_tracker_legacy_distance_stays_positive_and_releases_at_camera():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, raw_distance=100.0, cam_type=3, rear_hold_dist=0.0, source_id=0,
                        received_at=99.0) == (80.0, 0.0, True)
  assert update_tracker(tracker, raw_distance=10.0, cam_type=3, rear_hold_dist=0.0, source_id=0,
                        received_at=100.0, now=101.0) == (0.0, 0.0, False)


def test_tracker_legacy_frozen_distance_releases_while_vehicle_moves():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, cam_type=3, rear_hold_dist=0.0, source_id=0) == (100.0, 0.0, True)
  for now in (101.0, 102.0, 103.0):
    assert update_tracker(tracker, cam_type=3, rear_hold_dist=0.0, source_id=0,
                          received_at=now, now=now) == (100.0, 0.0, True)
  assert update_tracker(tracker, cam_type=3, rear_hold_dist=0.0, source_id=0,
                        received_at=103.01, now=103.01) == (0.0, 0.0, False)


def test_tracker_legacy_frozen_distance_remains_valid_while_vehicle_is_stopped():
  tracker = CameraDistanceTracker()
  assert update_tracker(tracker, cam_type=3, rear_hold_dist=0.0, source_id=0, v_ego=0.0) == (100.0, 0.0, True)
  assert update_tracker(tracker, cam_type=3, rear_hold_dist=0.0, source_id=0, v_ego=0.0,
                        received_at=110.0, now=110.0) == (100.0, 0.0, True)


def test_tracker_fresh_rear_ids_cannot_keep_frozen_distance_valid_while_moving():
  tracker = CameraDistanceTracker()
  for step in range(4):
    now = 100.0 + step
    distance, age, valid = update_tracker(tracker, source_id=1234567890123456 + step,
                                        received_at=now, now=now, v_ego=20.0)
    assert distance == 100.0
    assert age == 0.0
    assert valid
  distance, age, valid = update_tracker(tracker, source_id=1234567890123460,
                                      received_at=104.0, now=104.0, v_ego=20.0)
  assert distance == 0.0
  assert not valid


def test_tracker_fresh_rear_ids_keep_stationary_unchanged_distance_valid():
  tracker = CameraDistanceTracker()
  for step in range(6):
    now = 100.0 + step
    assert update_tracker(tracker, source_id=1234567890123456 + step,
                          received_at=now, now=now, v_ego=0.0) == (100.0, 0.0, True)


def test_tracker_submeter_rear_distance_updates_do_not_falsely_freeze():
  tracker = CameraDistanceTracker()
  for step, raw_distance in enumerate((100.0, 99.95, 99.90, 99.85, 99.80, 99.75)):
    now = 100.0 + step
    distance, age, valid = update_tracker(tracker, raw_distance=raw_distance,
                                        source_id=1234567890123456 + step,
                                        received_at=now, now=now, v_ego=20.0)
    assert distance == pytest.approx(raw_distance)
    assert age == 0.0
    assert valid
