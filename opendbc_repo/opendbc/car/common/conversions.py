import numpy as np

from openpilot.common.params import Params

class Conversions:
  # Speed
  MPH_TO_KPH = 1.609344
  KPH_TO_MPH = 1. / MPH_TO_KPH
  MS_TO_KPH = 3.6
  KPH_TO_MS = 1. / MS_TO_KPH
  MS_TO_MPH = MS_TO_KPH * KPH_TO_MPH
  MPH_TO_MS = MPH_TO_KPH * KPH_TO_MS
  MS_TO_KNOTS = 1.9438
  KNOTS_TO_MS = 1. / MS_TO_KNOTS

  # Angle
  DEG_TO_RAD = np.pi / 180.
  RAD_TO_DEG = 1. / DEG_TO_RAD

  # Mass
  LB_TO_KG = 0.453592


class UnitConverter:
  def __init__(self):
    self.params = Params()
    self.is_metric = self.params.get_bool("IsMetric")

  def to_ms(self, speed: float) -> float:
    return speed * Conversions.KPH_TO_MS if self.is_metric else speed * Conversions.MPH_TO_MS

  def to_clu(self, speed: float) -> float:
    return speed * Conversions.MS_TO_KPH if self.is_metric else speed * Conversions.MS_TO_MPH

  def to_current_unit(self, speed_kph: float) -> float:
    return speed_kph if self.is_metric else speed_kph * Conversions.KPH_TO_MPH
