import math
import numpy as np

from cereal import log
from openpilot.selfdrive.controls.lib.latcontrol import LatControl

# TODO This is speed dependent
STEER_ANGLE_SATURATION_THRESHOLD = 2.5  # Degrees


class LatControlAngle(LatControl):
  def __init__(self, CP, CI, dt):
    super().__init__(CP, CI, dt)
    self.sat_check_min_speed = 5.
    self.use_steer_limited_by_safety = CP.brand == "tesla"

    # Initialize the filtered curvature to zero (or an appropriate initial value)
    self.filtered_curvature = 0.0
    # Filter coefficient: adjust between 0 (very smooth) and 1 (no filtering)
    self.filter_speed_matrox = [0, 2.5, 8.3, 13.8, 22.22]
    self.filter_alpha_matrix = [0.05, 0.1, 0.3, 0.6, 1]

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited):
    angle_log = log.ControlsState.LateralAngleState.new_message()

    if not active:
      angle_log.active = False
      angle_steers_des = float(CS.steeringAngleDeg)
    else:
      angle_log.active = True
      # Apply exponential smoothing to the curvature
      adjusted_alpha = float(np.interp(CS.vEgo, self.filter_speed_matrox, self.filter_alpha_matrix))
      self.filtered_curvature = (adjusted_alpha * desired_curvature + (1 - adjusted_alpha) * self.filtered_curvature)
      # Convert the smoothed curvature to a steering angle
      angle_steers_des = math.degrees(VM.get_steer_from_curvature(-self.filtered_curvature, CS.vEgo, params.roll))
      angle_steers_des += params.angleOffsetDeg

    if self.use_steer_limited_by_safety:
      # these cars' carcontrollers calculate max lateral accel and jerk, so we can rely on carOutput for saturation
      angle_control_saturated = steer_limited_by_safety
    else:
      # for cars which use a method of limiting torque such as a torque signal (Nissan and Toyota)
      # or relying on EPS (Ford Q3), carOutput does not capture maxing out torque  # TODO: this can be improved
      angle_control_saturated = abs(angle_steers_des - CS.steeringAngleDeg) > STEER_ANGLE_SATURATION_THRESHOLD
    angle_log.saturated = bool(self._check_saturation(angle_control_saturated, CS, False, curvature_limited))
    angle_log.steeringAngleDeg = float(CS.steeringAngleDeg)
    angle_log.steeringAngleDesiredDeg = float(angle_steers_des)
    return 0, float(angle_steers_des), angle_log
