import numpy as np


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
    from openpilot.common.params import Params

    self.params = Params()
    self.is_metric = self.params.get_bool("IsMetric")

  def clu_to_ms(self, speed_clu: float) -> float:
    return speed_clu * Conversions.KPH_TO_MS if self.is_metric else speed_clu * Conversions.MPH_TO_MS

  def ms_to_clu(self, speed_ms: float) -> float:
    return speed_ms * Conversions.MS_TO_KPH if self.is_metric else speed_ms * Conversions.MS_TO_MPH

  @staticmethod
  def ms_to_kph(speed_ms: float) -> float:
    return speed_ms * Conversions.MS_TO_KPH

  @staticmethod
  def kph_to_ms(speed_kph: float) -> float:
    return speed_kph * Conversions.KPH_TO_MS

  def clu_to_kph(self, speed_clu: float) -> float:
    return speed_clu if self.is_metric else speed_clu * Conversions.MPH_TO_KPH

  def kph_to_clu(self, speed_kph: float) -> float:
    return speed_kph if self.is_metric else speed_kph * Conversions.KPH_TO_MPH
