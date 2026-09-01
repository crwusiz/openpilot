from enum import IntEnum
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace
from types import ModuleType


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
