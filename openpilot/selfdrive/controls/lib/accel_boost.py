import math

import numpy as np
from openpilot.cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL

PLAN_GATE = [-0.5, 1.0]  # m/s the plan runs ahead of holding the current speed: no boost at 0.5 behind, full boost at 1 ahead
ACCEL_BOOST_BIAS = {  # m/s^2
  log.LongitudinalPersonality.aggressive: 0.15,
  log.LongitudinalPersonality.standard: 0.10,
  log.LongitudinalPersonality.relaxed: 0.075,
  log.LongitudinalPersonality.morerelaxed: 0.05,
}


def get_accel_boost(personality=log.LongitudinalPersonality.standard):
  bias = ACCEL_BOOST_BIAS.get(getattr(personality, 'raw', personality), 0.0)
  # Scale the proportional gain with each bias using upstream's 0.2 to 0.3 ratio.
  return bias, bias * (2.0 / 3.0)


class AccelBoost:
  def __init__(self, dt=DT_MDL):
    self.plan_ahead_filter = FirstOrderFilter(0.0, 0.3, dt, initialized=False)  # smooths 1-3 frame blips in the plan
    self.bias_filter = FirstOrderFilter(0.0, 1.0, dt)  # the bias drops at once when the plan slows and comes back over ~1 s

  def reset(self):
    self.plan_ahead_filter.initialized = False
    self.bias_filter.x = 0.0

  def update(self, sm, output_a_target_e2e, curve_offset):
    pos = sm['modelV2'].position
    distances, times = pos.x, pos.t
    if not len(times) or len(distances) != len(times):
      self.reset()
      return output_a_target_e2e
    horizon = times[-1]
    if not math.isfinite(horizon) or horizon <= 0.0:
      self.reset()
      return output_a_target_e2e

    # m/s the plan's average speed over the next 10 s (distance / time) runs ahead of the current speed
    plan_ahead = distances[-1] / horizon - max(sm['carState'].vEgo, 1.0)
    if not math.isfinite(plan_ahead):
      self.reset()
      return output_a_target_e2e

    bias, gain = get_accel_boost(sm['selfdriveState'].personality)
    filtered_plan_ahead = self.plan_ahead_filter.update(plan_ahead)
    gate = np.interp(filtered_plan_ahead, PLAN_GATE, [0.0, 1.0]) if curve_offset == 0.0 else 0.0
    gated_bias = bias * gate
    self.bias_filter.x = min(self.bias_filter.x, gated_bias)  # drop immediately as the gate closes
    filtered_bias = self.bias_filter.update(gated_bias)

    gas_boost = gate * gain * max(output_a_target_e2e, 0.0)
    return output_a_target_e2e + gas_boost + filtered_bias
