from types import SimpleNamespace

from openpilot.selfdrive.addon.traffic_controller import (TrafficState, TrafficStopController, TrafficStopDistanceTracker, XState,
                                                          get_traffic_stop_accel_floor, should_limit_traffic_stop_accel)


def make_inputs(v_ego=10.0, steering_angle_deg=0.0, model_distance=80.0):
  car_state = SimpleNamespace(
    vEgo=v_ego,
    gasPressed=False,
    leftBlinker=False,
    steeringAngleDeg=steering_angle_deg,
  )
  model = SimpleNamespace(
    position=SimpleNamespace(x=[model_distance] * 33, y=[0.0] * 33),
    velocity=SimpleNamespace(x=[v_ego] * 32 + [0.0]),
  )
  radar_state = SimpleNamespace(leadOne=SimpleNamespace(present=False, dRel=0.0))
  return car_state, model, radar_state


def test_distance_tracker_rejects_transient_closer_estimate():
  tracker = TrafficStopDistanceTracker(sample_count=3)

  assert tracker.update(40.0, 0.0) == 40.0
  assert tracker.update(10.0, 1.0) == 39.0

  tracker.reset()
  assert tracker.update(5.0, 0.0) == 5.0


def test_controller_detects_and_enters_signal_stop():
  controller = TrafficStopController(dt=0.05)
  inputs = make_inputs()

  plans = [controller.update(*inputs, comfort_brake=1.5) for _ in range(4)]
  plan = plans[-1]

  assert controller.traffic_state == TrafficState.red
  assert controller.x_state == XState.e2eStop
  assert plan.signal_stop_active
  assert 0.0 < plan.stop_distance < plan.raw_stop_distance


def test_steering_angle_blocks_only_signal_stop_entry():
  controller = TrafficStopController(dt=0.05)
  inputs = make_inputs(steering_angle_deg=60.0)

  plans = [controller.update(*inputs, comfort_brake=1.5) for _ in range(4)]
  plan = plans[-1]

  assert controller.traffic_state == TrafficState.red
  assert controller.x_state == XState.e2eCruise
  assert not plan.signal_stop_active


def test_accel_floor_and_source_selection():
  assert get_traffic_stop_accel_floor(10.0, 100.0, 6.5) == -2.2
  assert get_traffic_stop_accel_floor(10.0, 20.0, 6.5) == -4.0
  assert should_limit_traffic_stop_accel(True, "e2e")
  assert not should_limit_traffic_stop_accel(True, "lead0")
