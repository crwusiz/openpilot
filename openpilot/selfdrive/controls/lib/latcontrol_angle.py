import math
import numpy as np

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.latcontrol import LatControl

# TODO: This is speed dependent
STEER_ANGLE_SATURATION_THRESHOLD = 2.5  # Degrees
LOW_SPEED_CURVATURE_FADE_START = 0.3  # m/s
LOW_SPEED_CURVATURE_FADE_END = 2.0  # m/s


class LatControlAngle(LatControl):
  def __init__(self, CP, CI, dt):
    super().__init__(CP, CI, dt)
    self.sat_check_min_speed = 5.0
    self.use_steer_limited_by_safety = CP.brand == "tesla"

    self.filtered_curvature = 0.0
    # Reduce response near standstill, where small curvature changes can create large steering-angle changes.
    self.filter_speed_matrix = [0., 0.3, 1., 2., 5., 10., 20., 30.]
    self.filter_alpha_matrix = [0.05, 0.05, 0.08, 0.15, 0.5, 0.7, 0.9, 1.0]

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited, lat_delay):
    angle_log = log.ControlsState.LateralAngleState.new_message()
    angle_log.active = bool(active)

    if not active:
      angle_steers_des = float(CS.steeringAngleDeg)
      self.filtered_curvature = desired_curvature
    else:
      speed = abs(CS.vEgo)
      actual_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg),
                                            CS.vEgo, params.roll)

      # Fade model curvature toward the current vehicle curvature near standstill. This prevents noisy low-speed
      # model actions from moving the wheel while preserving the current steering angle on curved roads.
      model_curvature_weight = float(np.interp(speed,
                                               [LOW_SPEED_CURVATURE_FADE_START, LOW_SPEED_CURVATURE_FADE_END],
                                               [0.0, 1.0]))
      target_curvature = actual_curvature + model_curvature_weight * (desired_curvature - actual_curvature)

      if speed <= LOW_SPEED_CURVATURE_FADE_START:
        self.filtered_curvature = actual_curvature
      else:
        adjusted_alpha = float(np.interp(speed, self.filter_speed_matrix, self.filter_alpha_matrix))
        self.filtered_curvature = adjusted_alpha * target_curvature + (1.0 - adjusted_alpha) * self.filtered_curvature

      # Convert the smoothed curvature to a steering angle
      angle_steers_des = math.degrees(VM.get_steer_from_curvature(-self.filtered_curvature, CS.vEgo, params.roll))
      angle_steers_des += params.angleOffsetDeg

    # Check for angle control saturation
    if self.use_steer_limited_by_safety:
      # these cars' carcontrollers calculate max lateral accel and jerk, so we can rely on carOutput for saturation
      angle_control_saturated = steer_limited_by_safety
    else:
      # For other cars relying on torque signals or EPS, manually check saturation threshold
      angle_control_saturated = abs(angle_steers_des - CS.steeringAngleDeg) > STEER_ANGLE_SATURATION_THRESHOLD

    angle_log.saturated = bool(self._check_saturation(angle_control_saturated, CS, False, curvature_limited))
    angle_log.steeringAngleDeg = float(CS.steeringAngleDeg)
    angle_log.steeringAngleDesiredDeg = float(angle_steers_des)

    return 0.0, float(angle_steers_des), angle_log
