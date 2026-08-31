from types import SimpleNamespace

from openpilot.selfdrive.addon.traffic_controller import (TrafficState, TrafficStopController, TrafficStopDistanceTracker, XState,
                                                          get_traffic_stop_accel_floor, get_traffic_stop_obstacle_distance,
                                                          should_limit_traffic_stop_accel)


def make_inputs(v_ego=10.0, steering_angle_deg=0.0, model_distance=80.0,
                terminal_velocity=0.0, big=False):
  car_state = SimpleNamespace(
    vEgo=v_ego,
    gasPressed=False,
    leftBlinker=False,
    steeringAngleDeg=steering_angle_deg,
  )
  model = SimpleNamespace(
    position=SimpleNamespace(x=[model_distance] * 33, y=[0.0] * 33),
    velocity=SimpleNamespace(x=[v_ego] * 32 + [terminal_velocity]),
    big=big,
  )
  radar_state = SimpleNamespace(leadOne=SimpleNamespace(present=False, dRel=0.0))
  return car_state, model, radar_state


def enter_stopped_signal_state(controller):
  inputs = make_inputs(v_ego=0.0, model_distance=10.0)
  plan = controller.update(*inputs, comfort_brake=1.5)
  for _ in range(4):
    plan = controller.update(*inputs, comfort_brake=1.5)
  assert controller.x_state == XState.e2eStopped
  assert plan.signal_stop_active
  return inputs


def set_go_prediction(model):
  model.position.x = [30.0] * 33
  model.velocity.x = [0.0] * 32 + [6.0]


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


def test_transient_big_model_go_prediction_does_not_release_stop():
  controller = TrafficStopController(dt=0.05)
  car_state, model, radar_state = enter_stopped_signal_state(controller)
  model.big = True
  set_go_prediction(model)

  plans = [controller.update(car_state, model, radar_state, comfort_brake=1.5) for _ in range(12)]

  assert controller.traffic_state == TrafficState.red
  assert controller.x_state == XState.e2eStopped
  assert plans[-1].signal_stop_active


def test_sustained_big_model_go_prediction_releases_stop():
  controller = TrafficStopController(dt=0.05)
  car_state, model, radar_state = enter_stopped_signal_state(controller)
  model.big = True
  set_go_prediction(model)

  plans = [controller.update(car_state, model, radar_state, comfort_brake=1.5) for _ in range(35)]

  assert controller.traffic_state == TrafficState.green
  assert controller.x_state == XState.e2ePrepare
  assert not plans[-1].signal_stop_active


def test_invalid_model_output_keeps_latched_stop_and_filter_recovers():
  controller = TrafficStopController(dt=0.05)
  car_state, model, radar_state = enter_stopped_signal_state(controller)
  model.position.x[-1] = float("nan")
  model.velocity.x[-1] = float("nan")

  plans = [controller.update(car_state, model, radar_state, comfort_brake=1.5) for _ in range(30)]

  assert controller.traffic_state == TrafficState.red
  assert plans[-1].signal_stop_active
  assert plans[-1].stop_distance < 1000.0

  model.position.x[-1] = 30.0
  model.velocity.x[-1] = 6.0
  plans = [controller.update(car_state, model, radar_state, comfort_brake=1.5) for _ in range(35)]
  assert controller.traffic_state == TrafficState.green
  assert not plans[-1].signal_stop_active


def test_lead_handoff_keeps_signal_stop_latched():
  controller = TrafficStopController(dt=0.05)
  car_state, model, radar_state = enter_stopped_signal_state(controller)
  radar_state.leadOne.present = True
  radar_state.leadOne.dRel = 11.0

  plan = controller.update(car_state, model, radar_state, comfort_brake=1.5)

  assert controller.x_state == XState.lead
  assert controller.traffic_state == TrafficState.red
  assert plan.signal_stop_active
  assert plan.stop_distance < 1000.0


def test_left_blinker_blocks_automatic_stop_release():
  controller = TrafficStopController(dt=0.05)
  car_state, model, radar_state = enter_stopped_signal_state(controller)
  car_state.leftBlinker = True
  set_go_prediction(model)

  plans = [controller.update(car_state, model, radar_state, comfort_brake=1.5) for _ in range(35)]

  assert controller.traffic_state == TrafficState.red
  assert controller.x_state == XState.e2eStopped
  assert plans[-1].signal_stop_active

  car_state.leftBlinker = False
  model.position.x[-1] = float("nan")
  model.velocity.x[-1] = float("nan")
  plan = controller.update(car_state, model, radar_state, comfort_brake=1.5)
  assert plan.signal_stop_active


def test_gas_press_releases_stop_and_suppresses_immediate_reentry():
  controller = TrafficStopController(dt=0.05)
  car_state, model, radar_state = enter_stopped_signal_state(controller)
  car_state.gasPressed = True

  plan = controller.update(car_state, model, radar_state, comfort_brake=1.5)
  assert controller.x_state == XState.e2ePrepare
  assert not plan.signal_stop_active

  car_state.gasPressed = False
  plan = controller.update(car_state, model, radar_state, comfort_brake=1.5)
  assert not plan.signal_stop_active
  assert controller.stop_entry_suppression_time > 9.0


def test_accel_floor_and_source_selection():
  assert get_traffic_stop_accel_floor(10.0, 100.0, 6.5) == -2.2
  assert get_traffic_stop_accel_floor(10.0, 20.0, 6.5) == -4.0
  assert should_limit_traffic_stop_accel(True, "e2e")
  assert not should_limit_traffic_stop_accel(True, "lead0")


def test_accel_floor_ignores_normal_model_endpoint_contraction():
  assert get_traffic_stop_accel_floor(78.23 / 3.6, 96.0, 5.5) == -2.2


def test_accel_floor_blends_only_in_safety_critical_range():
  assert abs(get_traffic_stop_accel_floor(20.0, 67.0116279070, 5.5) + 3.1) < 1e-9
  assert get_traffic_stop_accel_floor(62.0 / 3.6, 50.0, 5.5) == -4.0


def test_obstacle_distance_compensates_for_mpc_standstill_gap():
  # A 40 m ego target becomes a 45.5 m obstacle: the MPC keeps its 6.5 m
  # standstill gap and therefore stops at 39 m, retaining only the 1 m buffer.
  assert get_traffic_stop_obstacle_distance(40.0, 6.5) == 45.5
