from openpilot.common.test import OpenpilotTestCase
from openpilot.common.parameterized import parameterized

from openpilot.cereal import log
from opendbc.car.structs import car
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.nissan.values import CAR as NISSAN
from opendbc.car.gm.values import CAR as GM
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle


class TestLatControl(OpenpilotTestCase):

  def test_angle_control_holds_current_angle_near_standstill(self):
    car_name = NISSAN.NISSAN_LEAF
    CarInterface = interfaces[car_name]
    CP = CarInterface.get_non_essential_params(car_name)
    CI = CarInterface(CP)
    VM = VehicleModel(CP)
    controller = LatControlAngle(CP.as_reader(), CI, DT_CTRL)

    CS = car.CarState.new_message()
    CS.vEgo = 0.2
    CS.steeringAngleDeg = 20.0
    params = log.VehicleParameters.new_message()

    for desired_curvature in (-0.1, 0.1, -0.1, 0.1):
      _, desired_angle, _ = controller.update(True, CS, VM, params, False, desired_curvature, False, 0.2)
      assert abs(desired_angle - CS.steeringAngleDeg) < 1e-6

  @parameterized.expand([(HONDA.HONDA_CIVIC, LatControlPID), (TOYOTA.TOYOTA_RAV4, LatControlTorque),
                         (NISSAN.NISSAN_LEAF, LatControlAngle), (GM.CHEVROLET_BOLT_EUV, LatControlTorque)])
  def test_saturation(self, car_name, controller):
    CarInterface = interfaces[car_name]
    CP = CarInterface.get_non_essential_params(car_name)
    CI = CarInterface(CP)
    VM = VehicleModel(CP)

    controller = controller(CP.as_reader(), CI, DT_CTRL)

    CS = car.CarState.new_message()
    CS.vEgo = 30
    CS.steeringPressed = False

    params = log.VehicleParameters.new_message()

    # Saturate for curvature limited and controller limited
    for _ in range(1000):
      _, _, lac_log = controller.update(True, CS, VM, params, False, 0, True, 0.2)
    assert lac_log.saturated

    for _ in range(1000):
      _, _, lac_log = controller.update(True, CS, VM, params, False, 0, False, 0.2)
    assert not lac_log.saturated

    for _ in range(1000):
      _, _, lac_log = controller.update(True, CS, VM, params, False, 1, False, 0.2)
    assert lac_log.saturated
