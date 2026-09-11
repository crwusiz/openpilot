from enum import IntEnum
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace
from types import ModuleType

import pytest
import numpy as np

from openpilot.common.constants import UnitConverter


class FakeButtonType(IntEnum):
  unknown = 0
  accelCruise = 1
  decelCruise = 2
  gapAdjustCruise = 3
  cancel = 4
  resumeCruise = 5
  mainCruise = 6
  lkas = 7


class FakeGearShifter(IntEnum):
  park = 0
  drive = 1


class FakeButtons(IntEnum):
  NONE = 0
  RES_ACCEL = 1
  SET_DECEL = 2


MISSING_MODULE = object()


def load_cruise_controller():
  structs = SimpleNamespace(CarState=SimpleNamespace(
    ButtonEvent=SimpleNamespace(Type=FakeButtonType),
    GearShifter=FakeGearShifter,
  ))
  opendbc = ModuleType("opendbc")
  opendbc_car = ModuleType("opendbc.car")
  opendbc_car.structs = structs
  hyundai = ModuleType("opendbc.car.hyundai")
  hyundai_values = ModuleType("opendbc.car.hyundai.values")
  hyundai_values.Buttons = FakeButtons

  params_module = ModuleType("openpilot.common.params")
  params_module.Params = type("Params", (), {})

  cruise_module = ModuleType("openpilot.selfdrive.car.cruise")
  cruise_module.V_CRUISE_MIN = 10
  cruise_module.V_CRUISE_MAX = 145
  cruise_module.V_CRUISE_UNSET = 255
  cruise_module.V_CRUISE_INITIAL = 30
  cruise_module.V_CRUISE_INITIAL_EXPERIMENTAL_MODE = 105
  cruise_module.CRUISE_LONG_PRESS = 50
  cruise_module.IMPERIAL_INCREMENT = 1.6

  navi_module = ModuleType("openpilot.selfdrive.addon.navi_controller")
  navi_module.SpeedLimiter = type("SpeedLimiter", (), {})

  stubs = {
    "opendbc": opendbc,
    "opendbc.car": opendbc_car,
    "opendbc.car.hyundai": hyundai,
    "opendbc.car.hyundai.values": hyundai_values,
    "openpilot.common.params": params_module,
    "openpilot.selfdrive.car.cruise": cruise_module,
    "openpilot.selfdrive.addon.navi_controller": navi_module,
  }
  original_modules = {name: sys.modules.get(name, MISSING_MODULE) for name in stubs}
  sys.modules.update(stubs)
  try:
    path = Path(__file__).resolve().parents[1] / "cruise_controller.py"
    spec = spec_from_file_location("_cruise_controller_under_test", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
  finally:
    for name, original in original_modules.items():
      if original is MISSING_MODULE:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = original


cruise_controller = load_cruise_controller()
CruiseController = cruise_controller.CruiseController


def make_controller(applied_speed_clu: float):
  controller = CruiseController.__new__(CruiseController)
  controller.apply_limit_speed_clu = applied_speed_clu
  controller.conv = SimpleNamespace(kph_to_clu=lambda speed: speed)
  return controller


def test_curve_limit_decrease_is_rate_limited():
  controller = make_controller(60.0)

  controller._update_applied_limit(30.0, immediate=False, curve_is_binding=True)

  assert abs(controller.apply_limit_speed_clu - 59.8) < 1e-9


def test_limit_release_increase_is_rate_limited():
  controller = make_controller(30.0)

  controller._update_applied_limit(60.0, immediate=False, curve_is_binding=False)

  assert abs(controller.apply_limit_speed_clu - 30.1) < 1e-9


def test_immediate_limit_event_bypasses_rate_limit():
  controller = make_controller(60.0)

  controller._update_applied_limit(30.0, immediate=True, curve_is_binding=False)

  assert controller.apply_limit_speed_clu == 30.0


def test_double_press_does_not_step_to_curve_limit():
  # 2026-09-09 15:14:31: applied 96 -> 30 with requested 121.
  controller = make_controller(96.0)
  controller._update_applied_limit(30.0, immediate=True, curve_is_binding=True, non_curve_limit_clu=121.0)
  assert controller.apply_limit_speed_clu == pytest.approx(95.8)


@pytest.mark.parametrize("immediate", [False, True])
def test_curve_smoothing_still_honors_other_limits(immediate):
  controller = make_controller(96.0)
  controller._update_applied_limit(30.0, immediate=immediate, curve_is_binding=True, non_curve_limit_clu=50.0)
  assert controller.apply_limit_speed_clu == 50.0


def make_limit_inputs(monkeypatch, *, nda=True, school=True, camera_event=True,
                      camera_target=0.0, road=50.0, stock_speed=30.0, section=0.0):
  controller = make_controller(30.0)
  controller.CP = SimpleNamespace(openpilotLongitudinalControl=True)
  controller.min_set_speed_clu = 10.0
  controller.prev_section_active = False
  controller.prev_section_limit_speed = 0.0
  controller.prev_road_limit_speed = 0.0
  controller.pending_road_limit_speed = 0.0
  controller.limit_change_timer = 0
  controller.pending_road_restore = False
  controller.cruise_just_enabled = False
  controller.limit_speed_updated = False
  controller.ignore_road_limit_temporarily = False
  controller._road_limit_target = lambda speed: speed
  controller._set_limit_speed = lambda speed: setattr(controller, 'requested_speed_clu', speed)
  controller._cal_lead_speed = lambda *args: 255.0
  controller._cal_curve_speed_adaptive = lambda *args: 255.0
  controller._cal_stock_navi_curve_speed = lambda *args: 255.0
  controller._cal_steer_based_speed = lambda *args: 255.0
  debug_state = {}
  controller._debug_limit_state = lambda **kwargs: debug_state.update(kwargs)

  speed_limiter = SimpleNamespace(
    recv=lambda: None,
    get_active=lambda: nda,
    get_section_limit_speed=lambda: (section, 100.0 if section else 0.0),
    get_camera_limit_active=lambda: camera_event,
    get_road_limit_speed=lambda: road if nda else 0.0,
    get_max_speed=lambda speed: (camera_target, False),
    get_in_school_zone=lambda: school,
    get_camera_limit_speed_stock=lambda *args: (camera_target, False),
  )
  monkeypatch.setitem(CruiseController._cal_limit_speed.__globals__, "SpeedLimiter",
                      SimpleNamespace(instance=lambda: speed_limiter))
  car_state = SimpleNamespace(
    naviActive=True, naviSectionActive=section > 0, naviSpeed=section or stock_speed,
    speedLimit=30.0 if camera_event else 0.0, speedLimitDistance=500.0 if camera_event else 0.0,
    naviLimitSpeed=road, schoolZoneActive=school, steeringAngleDeg=0.0,
  )
  sm = {"radarState": SimpleNamespace(leadOne=SimpleNamespace(present=False))}
  return controller, car_state, sm, debug_state


@pytest.mark.parametrize("options, expected_camera, expected_target", [
  ({}, 50.0, 50.0),  # September 7: NDA school flag + camera event, but no target.
  ({"camera_target": 255.0}, 50.0, 50.0),
  ({"road": 30.0}, 30.0, 30.0),
  ({"road": 80.0}, 50.0, 50.0),
  ({"road": 0.0}, 30.0, 30.0),
  ({"nda": False}, 30.0, 30.0),
  ({"nda": False, "stock_speed": 0.0, "road": 40.0}, 40.0, 40.0),
  ({"nda": False, "stock_speed": 0.0, "road": 0.0}, 30.0, 30.0),
  ({"camera_event": False}, 50.0, 50.0),
  ({"camera_target": 80.0}, 80.0, 60.6),  # Preserve a valid camera approach target.
  ({"camera_event": False, "camera_target": 80.0}, 50.0, 50.0),
  ({"section": 60.0}, 60.0, 60.0),  # Preserve section ownership of SET.
  ({"nda": False, "section": 60.0}, 60.0, 60.0),
  ({"school": False}, 0.0, 50.0),  # Ordinary road fallback is unchanged.
])
def test_school_zone_limit_selection(monkeypatch, options, expected_camera, expected_target):
  controller, car_state, sm, debug_state = make_limit_inputs(monkeypatch, **options)

  controller._cal_limit_speed(car_state, sm, 33.0 / 3.6, 33.0, 60.6)

  assert controller.camera_limit_speed_clu == expected_camera
  assert debug_state["calculated_max_speed_clu"] == expected_target


def test_school_zone_missing_camera_target_caps_curve_release(monkeypatch):
  controller, car_state, sm, debug_state = make_limit_inputs(monkeypatch)
  controller._cal_curve_speed_adaptive = lambda *args: 30.0
  controller._cal_limit_speed(car_state, sm, 33.0 / 3.6, 33.0, 60.6)
  assert controller.apply_limit_speed_clu == 30.0

  # The model curve target jumps to 119.9 in the log; the school flag stays on.
  controller._cal_curve_speed_adaptive = lambda *args: 119.9
  for _ in range(400):
    controller._cal_limit_speed(car_state, sm, 33.0 / 3.6, 33.0, 60.6)

  assert debug_state["calculated_max_speed_clu"] == 50.0
  assert controller.apply_limit_speed_clu == 50.0


def make_steer_controller():
  controller = make_controller(30.0)
  controller.conv = SimpleNamespace(kph_to_clu=lambda speed: speed,
                                    clu_to_ms=lambda speed: speed / 3.6,
                                    ms_to_clu=lambda speed: speed * 3.6)
  controller.min_set_speed_clu = 10.0
  controller.max_set_speed_clu = 145.0
  controller.steer_decel_active = False
  controller.steer_decel_entry_speed_ms = None
  controller.prev_steering_angle = 0.0
  return controller


def test_limit_debug_separates_model_stock_and_steer_reference(monkeypatch):
  controller, car_state, sm, _ = make_limit_inputs(monkeypatch)
  controller.conv.ms_to_clu = lambda speed: speed * 3.6
  controller.steer_decel_active = True
  controller.steer_decel_entry_speed_ms = 30.0 / 3.6
  controller.applied_speed_clu = 30.0
  controller.ignore_limit_timer = 0
  controller._debug_last_state = None
  controller._debug_last_time = 0.0
  controller._cal_curve_speed_adaptive = lambda *args: 40.0
  controller._cal_stock_navi_curve_speed = lambda *args: 50.0
  controller._debug_limit_state = CruiseController._debug_limit_state.__get__(controller)
  sm["radarState"].leadOne.dRel = 0.0
  sm["radarState"].leadOne.vRel = 0.0
  messages = []
  monkeypatch.setitem(CruiseController._debug_limit_state.__globals__, "cruise_log",
                      SimpleNamespace(debug=lambda fmt, *args: messages.append(fmt % args)))

  controller._cal_limit_speed(car_state, sm, 33.0 / 3.6, 33.0, 60.6)

  assert len(messages) == 1
  assert "curve_detail[model=40.0 stock=50.0 steer_entry=30.0]" in messages[0]


def test_steer_limit_does_not_compound_as_vehicle_slows():
  controller = make_steer_controller()
  initial_limit = controller._cal_steer_based_speed(40.0 / 3.6, 90.0)
  for speed in (35.0, 30.0, 25.0):
    assert controller._cal_steer_based_speed(speed / 3.6, 90.0) == initial_limit
  assert controller._cal_steer_based_speed(30.0 / 3.6, 0.0) == 255.0
  assert controller.steer_decel_entry_speed_ms is None


@pytest.mark.parametrize("gas_ticks, expected_reference", [(0, 2.0), (101, 30.0)])
def test_accepted_gas_override_refreshes_low_speed_steer_reference(monkeypatch, gas_ticks, expected_reference):
  controller = make_steer_controller()
  controller.CP = SimpleNamespace(openpilotLongitudinalControl=True)
  controller.requested_speed_clu = 30.0
  controller.gas_pressed_count = gas_ticks
  controller._cal_steer_based_speed(2.0 / 3.6, 60.6)
  monkeypatch.setitem(CruiseController._override_speed.__globals__, "CruiseStateManager",
                      SimpleNamespace(instance=lambda: SimpleNamespace(cruise_state_control=False)))
  car_state = SimpleNamespace(gasPressed=True, cruiseState=SimpleNamespace(enabled=True))

  controller._override_speed(car_state, 30.0, 30.0, False)

  assert controller.steer_decel_entry_speed_ms == pytest.approx(expected_reference / 3.6)
  target = controller._cal_steer_based_speed(30.0 / 3.6, 60.6)
  assert target == pytest.approx(max(25.0, expected_reference * 0.9084))


def make_model_sm(x=None, y=None, *, valid=True):
  x = np.linspace(0.0, 160.0, 81) if x is None else np.asarray(x, dtype=float)
  y = np.zeros_like(x) if y is None else np.asarray(y, dtype=float)
  model = SimpleNamespace(position=SimpleNamespace(x=x, y=y))

  class ModelSM(dict):
    logMonoTime = {'modelV2': 1}

    def all_checks(self, services):
      return valid

  return ModelSM(modelV2=model)


def make_stock_curve_controller():
  controller = make_steer_controller()
  controller.conv.kph_to_ms = lambda speed: speed / 3.6
  controller.conv.ms_to_kph = lambda speed: speed * 3.6
  controller._update_vehicle_navi_curve_params = lambda: None
  controller.vehicle_navi_curve_control = True
  controller.vehicle_navi_curve_mpp_control = True
  controller.vehicle_navi_curve_lower_limit = 30.0
  controller.vehicle_navi_curve_speed_factor = 1.0
  controller.vehicle_navi_curve_decel_rate = 2.0
  controller.vehicle_navi_curve_control_end = 3.0
  return controller


def stock_curve_state(**overrides):
  values = {'naviCurveDistance': 20.0, 'naviCurveSpeed': 30.0, 'naviCurveCurvature': 0.02736,
            'naviCurveRouteActive': True, 'naviCurveRouteState': 1}
  return SimpleNamespace(**(values | overrides))


@pytest.mark.parametrize("distance", [4.0, 20.0, 50.0])
@pytest.mark.parametrize("route_active", [True, False])
def test_nearby_tight_map_curve_on_straight_model_path_is_rejected(distance, route_active):
  controller = make_stock_curve_controller()
  car_state = stock_curve_state(naviCurveDistance=distance, naviCurveRouteActive=route_active,
                                naviCurveRouteState=1 if route_active else 0)
  assert controller._cal_stock_navi_curve_speed(car_state, make_model_sm()) == 255.0
  assert controller.stock_curve_rejected


def test_real_upcoming_curve_is_retained_even_before_vehicle_turns():
  controller = make_stock_curve_controller()
  x = np.linspace(0.0, 160.0, 81)
  y = 0.5 * 0.02736 * np.maximum(x - 10.0, 0.0) ** 2
  target = controller._cal_stock_navi_curve_speed(stock_curve_state(), make_model_sm(x, y))
  assert target == pytest.approx(30.0)
  assert not controller.stock_curve_rejected


def test_map_limit_releases_when_ramp_path_straightens():
  controller = make_stock_curve_controller()
  x = np.linspace(0.0, 160.0, 81)
  y = 0.5 * 0.02736 * np.maximum(x - 10.0, 0.0) ** 2
  car_state = stock_curve_state()
  assert controller._cal_stock_navi_curve_speed(car_state, make_model_sm(x, y)) == pytest.approx(30.0)
  assert controller._cal_stock_navi_curve_speed(car_state, make_model_sm()) == 255.0


@pytest.mark.parametrize("sm", [
  make_model_sm(valid=False),
  make_model_sm(x=[0, 1, 2]),
  make_model_sm(x=np.linspace(0, 25, 20)),
  make_model_sm(x=np.zeros(20)),
  make_model_sm(y=[float('nan')] * 81),
  make_model_sm(y=[0.0] * 10),
])
def test_unavailable_or_uncovered_model_keeps_navigation_limit(sm):
  controller = make_stock_curve_controller()
  assert controller._cal_stock_navi_curve_speed(stock_curve_state(), sm) == pytest.approx(30.0)
  assert not controller.stock_curve_rejected


def test_distant_curve_keeps_navigation_preview():
  controller = make_stock_curve_controller()
  target = controller._cal_stock_navi_curve_speed(stock_curve_state(naviCurveDistance=200.0), make_model_sm())
  assert 30.0 < target < 255.0
  assert not controller.stock_curve_rejected


@pytest.mark.parametrize("field", ['naviCurveDistance', 'naviCurveSpeed', 'naviCurveCurvature'])
@pytest.mark.parametrize("value", [float('nan'), float('inf')])
def test_nonfinite_stock_curve_data_is_ignored(field, value):
  controller = make_stock_curve_controller()
  assert controller._cal_stock_navi_curve_speed(stock_curve_state(**{field: value}), make_model_sm()) == 255.0


def test_curve_engagement_starts_at_vehicle_speed(monkeypatch):
  controller, car_state, sm, debug = make_limit_inputs(monkeypatch, school=False, camera_event=False,
                                                      camera_target=0.0, road=0.0)
  controller.apply_limit_speed_clu = 0.0
  controller.cruise_just_enabled = True
  controller._cal_curve_speed_adaptive = lambda *args: 61.9
  controller._cal_limit_speed(car_state, sm, 96.0 / 3.6, 96.0, 100.0)
  assert controller.apply_limit_speed_clu == pytest.approx(95.8)
  assert 'cruise_enable' in debug['immediate_reasons']


def test_zero_confidence_estimates_do_not_create_curve_limit():
  controller = make_steer_controller()
  controller.prev_model_mono_time = 0
  controller.cached_curve_speed_ms = None
  controller._get_model_based_speed = lambda *args: (10.0, 0.0)
  controller._get_acc_based_speed = lambda *args: (12.0, 0.0)
  assert controller._cal_curve_speed_adaptive(make_model_sm(), 30.0, 110.0) == 255.0


@pytest.mark.parametrize("field", ['x', 'y'])
def test_invalid_model_path_does_not_create_curve_limit(field):
  controller = make_steer_controller()
  model = make_model_sm()['modelV2']
  setattr(model.position, field, [float('nan')] * 81)
  assert controller._get_model_based_speed(model, 30.0, 15.0) == (255.0, 0.0)


@pytest.mark.parametrize("orientation, velocity", [([0.1], [30.0, 30.0]), ([float('nan')], [30.0])])
def test_invalid_acceleration_model_does_not_create_curve_limit(orientation, velocity):
  controller = make_steer_controller()
  model = SimpleNamespace(orientationRate=SimpleNamespace(z=orientation), velocity=SimpleNamespace(x=velocity))
  assert controller._get_acc_based_speed(model, 30.0, 15.0) == (255.0, 0.0)


@pytest.mark.parametrize("camera_limit, expected", [(0.0, 95.8), (50.0, 50.0)])
def test_double_press_curve_integration_preserves_enforcement(monkeypatch, camera_limit, expected):
  controller, car_state, sm, _ = make_limit_inputs(monkeypatch, school=False, camera_event=camera_limit > 0,
                                                  camera_target=camera_limit, road=110.0)
  controller.apply_limit_speed_clu = 96.0
  controller._cal_stock_navi_curve_speed = lambda *args: 30.0
  controller._cal_limit_speed(car_state, sm, 104.0 / 3.6, 104.0, 121.0, double_pressed=True)
  assert controller.apply_limit_speed_clu == pytest.approx(expected)


def make_full_controller(monkeypatch, *, stock_long=False, pcm_cruise=True, state_control=True,
                         is_metric=True, nda=True, road=0.0, section=0.0, camera=0.0):
  conv = UnitConverter.__new__(UnitConverter)
  conv.is_metric = is_metric
  params = SimpleNamespace(get_bool=lambda key: key == "CruiseStateControl" and state_control)
  monkeypatch.setattr(cruise_controller, "Params", lambda: params)
  monkeypatch.setattr(cruise_controller, "UnitConverter", lambda: conv)
  manager = cruise_controller.CruiseStateManager()
  monkeypatch.setattr(cruise_controller.CruiseStateManager, "_instance", manager, raising=False)

  navi = SimpleNamespace(roadLimitSpeed=road, sectionLimitSpeed=section, sectionLeftDist=100.0 if section else 0.0,
                         camLimitSpeed=camera, camLimitSpeedLeftDist=100.0 if camera else 0.0)
  limiter = SimpleNamespace(
    conv=conv, naviData=navi, recv=lambda: None, get_active=lambda: nda,
    get_road_limit_speed=lambda: navi.roadLimitSpeed if nda else 0.0,
    get_section_limit_speed=lambda: (navi.sectionLimitSpeed, navi.sectionLeftDist),
    get_camera_limit_active=lambda: camera > 0.0,
    get_max_speed=lambda speed: (camera, False), get_in_school_zone=lambda: False,
    get_camera_limit_speed_stock=lambda *args: (camera, False),
  )
  monkeypatch.setattr(cruise_controller, "SpeedLimiter", SimpleNamespace(instance=lambda: limiter))
  cp = SimpleNamespace(openpilotLongitudinalControl=not stock_long, pcmCruise=pcm_cruise)
  ci = SimpleNamespace(CS=SimpleNamespace(cruise_buttons=[FakeButtons.NONE]), create_buttons=lambda button: button)
  controller = CruiseController(cp, ci)
  car_state = SimpleNamespace(
    vEgo=60.0 / 3.6, vEgoCluster=60.0 / 3.6, gasPressed=False, brakePressed=False,
    buttonEvents=[], gearShifter=FakeGearShifter.drive, steeringAngleDeg=0.0,
    cruiseState=SimpleNamespace(enabled=True, available=True, speed=90.0 / 3.6, standstill=False),
    naviActive=False, naviSectionActive=False, naviSpeed=0.0, naviLimitSpeed=road,
    speedLimit=camera, speedLimitDistance=100.0 if camera else 0.0, schoolZoneActive=False,
  )
  sm = make_model_sm(valid=False)
  sm['radarState'] = SimpleNamespace(leadOne=SimpleNamespace(present=False, dRel=0.0, vRel=0.0))
  return controller, car_state, sm, manager, limiter


@pytest.mark.parametrize("is_metric", [True, False])
@pytest.mark.parametrize("source", ['road', 'section'])
def test_automatic_set_speed_respects_maximum(monkeypatch, is_metric, source):
  controller, cs, sm, manager, limiter = make_full_controller(monkeypatch, is_metric=is_metric)
  if source == 'road':
    limiter.naviData.roadLimitSpeed = controller.conv.kph_to_clu(140.0)
  else:
    limiter.naviData.sectionLimitSpeed = controller.conv.kph_to_clu(160.0)
    limiter.naviData.sectionLeftDist = 100.0

  controller.update_v_cruise(cs, sm, True)

  assert controller.requested_speed_clu == pytest.approx(controller.max_set_speed_clu)
  assert controller.v_cruise_kph <= 145.0 + 1e-9
  assert controller.v_cruise_cluster_kph == pytest.approx(145.0)
  assert manager.speed_ms == pytest.approx(145.0 / 3.6)


@pytest.mark.parametrize("road", [253.0, 254.0, 255.0, float('nan'), float('inf')])
def test_stock_navigation_non_speed_values_do_not_change_set(monkeypatch, road):
  controller, cs, sm, _, limiter = make_full_controller(monkeypatch, nda=False, road=road)

  for _ in range(305):
    controller.update_v_cruise(cs, sm, True)

  assert controller.requested_speed_clu == pytest.approx(90.0)
  assert controller.prev_road_limit_speed == 0.0
  assert controller.road_limit_speed_clu == 255.0
  assert cruise_controller._get_button_limit(limiter, cs) == (0.0, False)


@pytest.mark.parametrize("camera_kph", [20.0, 30.0, 31.0])
@pytest.mark.parametrize("is_metric", [True, False])
def test_stock_cruise_decelerates_to_minimum_supported_speed(monkeypatch, camera_kph, is_metric):
  camera = camera_kph if is_metric else camera_kph / 1.609344
  controller, cs, sm, _, _ = make_full_controller(
    monkeypatch, stock_long=True, state_control=False, nda=False, camera=camera, is_metric=is_metric,
  )

  controller.update_v_cruise(cs, sm, True)
  can_sends = []
  controller.spam_message(cs, can_sends)

  expected_speed = controller.conv.kph_to_clu(max(camera_kph, 30.0))
  assert controller.override_speed_clu == pytest.approx(expected_speed)
  assert controller.v_cruise_kph == pytest.approx(max(camera_kph, 30.0))
  assert can_sends == [FakeButtons.SET_DECEL]


@pytest.mark.parametrize("other_button", [FakeButtonType.mainCruise, FakeButtonType.lkas])
def test_unrelated_button_release_keeps_held_cruise_button(other_button):
  handler = cruise_controller.CruiseButtonHandler()
  handler.update([SimpleNamespace(type=FakeButtonType.decelCruise, pressed=True)])

  assert handler.update([SimpleNamespace(type=other_button, pressed=False)]) == (FakeButtonType.unknown, False, False)
  assert handler.update([SimpleNamespace(type=FakeButtonType.decelCruise, pressed=False)]) == (FakeButtonType.decelCruise, False, False)


@pytest.mark.parametrize("new_target", [70.0, 80.0, 0.0])
def test_button_burst_stops_when_target_changes(monkeypatch, new_target):
  controller, cs, _, _, _ = make_full_controller(monkeypatch, stock_long=True)
  cs.cruiseState.speed = 80.0 / 3.6
  controller.override_speed_clu = 90.0
  first_sends = []
  controller.spam_message(cs, first_sends)
  assert first_sends == [FakeButtons.RES_ACCEL]

  controller.override_speed_clu = new_target
  next_sends = []
  controller.spam_message(cs, next_sends)

  assert next_sends == []
  assert controller.button_spam_count == 0
  assert controller.button_spam_wait_timer > 0


def test_driver_button_interrupts_openpilot_long_button_burst(monkeypatch):
  controller, cs, _, _, _ = make_full_controller(monkeypatch, state_control=False)
  controller.override_speed_clu = 100.0
  controller.spam_message(cs, [])
  controller.CI.CS.cruise_buttons[-1] = FakeButtons.SET_DECEL
  can_sends = []

  controller.spam_message(cs, can_sends)

  assert can_sends == []
  assert controller.button_spam_count == 0


@pytest.mark.parametrize("stock_long", [True, False])
@pytest.mark.parametrize("brake", [True, False])
def test_brake_or_disengagement_stops_buttons_and_clears_limit(monkeypatch, stock_long, brake):
  controller, cs, _, _, _ = make_full_controller(monkeypatch, stock_long=stock_long)
  controller.override_speed_clu = 100.0
  controller.apply_limit_speed_clu = 70.0
  controller.spam_message(cs, [])
  cs.brakePressed = brake
  cs.cruiseState.enabled = brake
  can_sends = []

  controller.spam_message(cs, can_sends)

  assert can_sends == []
  assert controller.button_spam_count == 0
  assert controller.override_speed_clu == 0.0
  assert controller.apply_limit_speed_clu == 0.0


@pytest.mark.parametrize("button", [FakeButtonType.accelCruise, FakeButtonType.decelCruise])
def test_held_brake_prevents_cruise_reengagement(monkeypatch, button):
  _, cs, _, manager, _ = make_full_controller(monkeypatch)
  manager.available = True
  manager.prev_brake_pressed = True
  cs.brakePressed = True
  for pressed in (True, False):
    cs.buttonEvents = [SimpleNamespace(type=button, pressed=pressed)]
    manager.update(cs, [0])

  assert not manager.enabled
  assert not cs.cruiseState.enabled


def test_imperial_state_manager_uses_physical_speed_bounds_and_one_mph_steps(monkeypatch):
  _, cs, _, manager, _ = make_full_controller(monkeypatch, is_metric=False)
  assert manager.speed_ms == pytest.approx(30.0 / 3.6)
  manager.available = True
  manager.enabled = True
  manager.speed_ms = manager.conv.clu_to_ms(50.0)

  manager._button_press(cs, FakeButtonType.accelCruise, False, False)
  assert manager.conv.ms_to_clu(manager.speed_ms) == pytest.approx(51.0)

  manager.speed_ms = 145.0 / 3.6
  manager._button_press(cs, FakeButtonType.accelCruise, True, False)
  assert manager.speed_ms == pytest.approx(145.0 / 3.6)


def test_non_pcm_cruise_uses_own_set_when_stock_set_is_zero(monkeypatch):
  controller, cs, sm, _, _ = make_full_controller(monkeypatch, pcm_cruise=False, state_control=False)
  cs.cruiseState.speed = 0.0
  controller.update_v_cruise(cs, sm, True)
  controller.spam_message(cs, [])
  for pressed in (True, False):
    cs.buttonEvents = [SimpleNamespace(type=FakeButtonType.accelCruise, pressed=pressed)]
    controller.update_v_cruise(cs, sm, True)
    controller.spam_message(cs, [])

  assert controller.requested_speed_clu == pytest.approx(61.0)
  assert controller.v_cruise_kph == pytest.approx(60.1)


def test_non_pcm_resume_retains_previous_set_through_disabled_frames(monkeypatch):
  controller, cs, sm, _, _ = make_full_controller(monkeypatch, pcm_cruise=False)
  controller.update_v_cruise(cs, sm, True)
  controller.requested_speed_clu = 90.0
  cs.cruiseState.enabled = False
  for _ in range(10):
    controller.update_v_cruise(cs, sm, False)

  cs.cruiseState.enabled = True
  cs.buttonEvents = [SimpleNamespace(type=FakeButtonType.accelCruise, pressed=True)]
  controller.update_v_cruise(cs, sm, True)

  assert controller.requested_speed_clu == pytest.approx(90.0)


@pytest.mark.parametrize("previous_set, ego_kph", [
  (60.6, 3.0), (60.6, 0.0), (47.0, 40.0), (59.0, 27.0),
  (57.0, 43.0), (50.0, 46.0), (58.0, 43.0), (55.0, 44.0),
  (60.6, 80.0), (140.0, 150.0),
])
def test_logged_gas_overrides_preserve_set_and_use_current_speed_reference(monkeypatch, previous_set, ego_kph):
  # September 11: eight accelerator entries reduced SET, including 60.6 -> 10.
  controller, cs, _, manager, _ = make_full_controller(monkeypatch)
  controller.requested_speed_clu = previous_set
  controller.gas_pressed_count = 100
  cs.gasPressed = True

  requested = controller._override_speed(cs, ego_kph, previous_set, False)

  override_reference = min(145.0, max(10.0, ego_kph + 3.0))
  assert requested == pytest.approx(max(previous_set, override_reference))
  assert controller.apply_limit_speed_clu == pytest.approx(override_reference)
  assert manager.speed_ms == pytest.approx(requested / 3.6)


def test_restart_gas_release_ramps_back_to_preserved_set(monkeypatch):
  controller, cs, sm, manager, _ = make_full_controller(monkeypatch, road=50.0)
  controller.update_v_cruise(cs, sm, True)
  assert controller.requested_speed_clu == pytest.approx(60.6, abs=0.1)
  original_set = controller.requested_speed_clu
  cs.vEgo = cs.vEgoCluster = 0.0
  cs.gasPressed = True
  cs.cruiseState.speed = manager.speed_ms
  controller.gas_pressed_count = 100
  controller.update_v_cruise(cs, sm, True)
  controller.spam_message(cs, [])
  assert controller.requested_speed_clu == pytest.approx(original_set)
  assert controller.v_cruise_kph == 10.0

  cs.gasPressed = False
  for tick in range(1, 21):
    cs.cruiseState.speed = manager.speed_ms
    controller.update_v_cruise(cs, sm, True)
    controller.spam_message(cs, [])
    assert controller.requested_speed_clu == pytest.approx(original_set)
    assert controller.v_cruise_kph == pytest.approx(10.0 + tick * 0.1)


def test_synthetic_cruise_state_does_not_generate_stock_button_feedback(monkeypatch):
  controller, cs, _, _, _ = make_full_controller(monkeypatch)
  controller.override_speed_clu = 100.0
  controller.apply_limit_speed_clu = 40.0
  can_sends = []

  controller.spam_message(cs, can_sends)

  assert can_sends == []
  assert controller.button_spam_count == 0
  assert controller.override_speed_clu == 0.0
  assert controller.apply_limit_speed_clu == 40.0


def test_lead_limit_does_not_toggle_at_logged_relative_speed_boundary(monkeypatch):
  # September 11, 15:11:09: dRel=19 m, vRel around -1 m/s, SET=60.6, ego=30.
  controller, _, _, _, _ = make_full_controller(monkeypatch)
  lead = SimpleNamespace(present=True, dRel=19.0, vRel=-1.05)
  assert controller._cal_lead_speed(lead, 30.0) < 30.0
  for v_rel in (-0.99, -1.01, -0.98, -1.02):
    lead.vRel = v_rel
    assert controller._cal_lead_speed(lead, 30.0) < 30.0

  lead.vRel = -0.4
  assert controller._cal_lead_speed(lead, 30.0) == 255.0
  lead.vRel = -0.99
  assert controller._cal_lead_speed(lead, 30.0) == 255.0


def test_lead_limit_has_separate_distance_entry_and_release_boundaries(monkeypatch):
  controller, _, _, _, _ = make_full_controller(monkeypatch)
  lead = SimpleNamespace(present=True, dRel=70.0, vRel=-3.0)
  assert controller._cal_lead_speed(lead, 40.0) < 40.0
  lead.dRel = 72.1  # Just beyond the original 22 s boundary.
  assert controller._cal_lead_speed(lead, 40.0) < 40.0
  lead.dRel = 83.0
  assert controller._cal_lead_speed(lead, 40.0) == 255.0
  lead.dRel = 72.1
  assert controller._cal_lead_speed(lead, 40.0) == 255.0


@pytest.mark.parametrize("distance", [5.0, 4.0, 1.0])
def test_closing_lead_inside_distance_buffer_keeps_limit(monkeypatch, distance):
  controller, _, _, _, _ = make_full_controller(monkeypatch)
  lead = SimpleNamespace(present=True, dRel=distance, vRel=-2.0)
  assert controller._cal_lead_speed(lead, 30.0) == controller.min_set_speed_clu


def test_lost_lead_and_reset_clear_lead_hysteresis(monkeypatch):
  controller, _, _, _, _ = make_full_controller(monkeypatch)
  lead = SimpleNamespace(present=True, dRel=19.0, vRel=-1.05)
  controller._cal_lead_speed(lead, 30.0)
  lead.present = False
  assert controller._cal_lead_speed(lead, 30.0) == 255.0
  assert not controller.lead_decel_active

  lead.present = True
  controller._cal_lead_speed(lead, 30.0)
  controller.reset()
  lead.vRel = -0.99
  assert controller._cal_lead_speed(lead, 30.0) == 255.0


def test_curve_estimates_are_logged_and_cleared_with_invalid_model(monkeypatch):
  controller, cs, sm, _, _ = make_full_controller(monkeypatch)
  sm.all_checks = lambda services: True
  model = sm['modelV2']
  model.position.y = 0.001 * np.asarray(model.position.x) ** 2
  model.orientationRate = SimpleNamespace(z=[0.1] * 81)
  model.velocity = SimpleNamespace(x=[20.0] * 81)
  messages = []
  monkeypatch.setattr(cruise_controller, 'cruise_log',
                      SimpleNamespace(info=lambda *args: None, debug=lambda fmt, *args: messages.append(fmt % args)))

  controller._cal_curve_speed_adaptive(sm, 20.0, 90.0)
  assert controller.curve_estimates is not None
  assert controller.model_curve_geometry is not None
  controller.update_v_cruise(cs, sm, True)
  assert any('curve_estimates[path=' in message and 'yaw_conf=' in message and 'tail_k=0.00' in message for message in messages)

  sm.all_checks = lambda services: False
  controller._cal_curve_speed_adaptive(sm, 20.0, 90.0)
  assert controller.curve_estimates is None
  assert controller.model_curve_geometry is None
