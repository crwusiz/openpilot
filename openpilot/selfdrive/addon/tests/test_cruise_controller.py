from enum import IntEnum
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace
from types import ModuleType

import pytest


class FakeButtonType(IntEnum):
  unknown = 0
  accelCruise = 1
  decelCruise = 2
  gapAdjustCruise = 3
  cancel = 4
  resumeCruise = 5


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
  cruise_module.IMPERIAL_INCREMENT = 1

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
    return module.CruiseController
  finally:
    for name, original in original_modules.items():
      if original is MISSING_MODULE:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = original


CruiseController = load_cruise_controller()


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
  controller.ignore_road_limit_temporarily = False
  controller._road_limit_target = lambda speed: speed
  controller._set_limit_speed = lambda speed: None
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
