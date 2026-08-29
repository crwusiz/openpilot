import numpy as np

from openpilot.common.params import Params

# conversions
class CV:
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


ACCELERATION_DUE_TO_GRAVITY = 9.81  # m/s^2


class UnitConverter:
  def __init__(self):
    self.params = Params()
    self.is_metric = self.params.get_bool("IsMetric")

  def clu_to_ms(self, speed_clu: float) -> float:
    return speed_clu * CV.KPH_TO_MS if self.is_metric else speed_clu * CV.MPH_TO_MS

  def ms_to_clu(self, speed_ms: float) -> float:
    return speed_ms * CV.MS_TO_KPH if self.is_metric else speed_ms * CV.MS_TO_MPH

  @staticmethod
  def ms_to_kph(speed_ms: float) -> float:
    return speed_ms * CV.MS_TO_KPH

  @staticmethod
  def kph_to_ms(speed_kph: float) -> float:
    return speed_kph * CV.KPH_TO_MS

  def clu_to_kph(self, speed_clu: float) -> float:
    return speed_clu if self.is_metric else speed_clu * CV.MPH_TO_KPH

  def kph_to_clu(self, speed_kph: float) -> float:
    return speed_kph if self.is_metric else speed_kph * CV.KPH_TO_MPH
