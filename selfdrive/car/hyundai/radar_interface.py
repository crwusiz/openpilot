#!/usr/bin/env python3
import time

from cereal import car
from opendbc.can.parser import CANParser
from selfdrive.car.interfaces import RadarInterfaceBase
from selfdrive.car.hyundai.values import DBC
from common.kalman.simple_kalman import KF1D

def get_radar_can_parser(CP):
  signals = [
    # sig_name, sig_address, default
    ("ObjValid", "SCC11", 0),
    ("ACC_ObjStatus", "SCC11", 0),
    ("ACC_ObjLatPos", "SCC11", 0),
    ("ACC_ObjDist", "SCC11", 0),
    ("ACC_ObjRelSpd", "SCC11", 0),
  ]
  checks = [
    ("SCC11", 50),
  ]
  return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, CP.sccBus)


class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.rcp = get_radar_can_parser(CP)
    self.updated_messages = set()
    self.trigger_msg = 0x420
    self.track_id = 0
    self.radar_off_can = CP.radarOffCan

    self.frame = 0
    self.reset_v_rel()

  def reset_v_rel(self):
    self.lastdRel = None
    self.lastTime = None
    self.v_rel_kf = None

  def update(self, can_strings):
    self.frame += 1
    if self.radar_off_can:
      self.reset_v_rel()
      return super().update(None)

    vls = self.rcp.update_strings(can_strings)
    self.updated_messages.update(vls)

    if self.trigger_msg not in self.updated_messages:
      self.reset_v_rel()
      return None

    rr = self._update(self.updated_messages)
    self.updated_messages.clear()
    return rr

  def _update(self, updated_messages):
    ret = car.RadarData.new_message()
    cpt = self.rcp.vl
    errors = []
    if not self.rcp.can_valid:
      errors.append("canError")
    ret.errors = errors

    valid = cpt["SCC11"]['ACC_ObjStatus']

    for ii in range(1):
      if valid:
        if ii not in self.pts:
          self.pts[ii] = car.RadarData.RadarPoint.new_message()
          self.pts[ii].trackId = self.track_id
          self.track_id += 1

        dRel = cpt["SCC11"]['ACC_ObjDist']

        self.pts[ii].dRel = cpt["SCC11"]['ACC_ObjDist']  # from front of car
        self.pts[ii].yRel = -cpt["SCC11"]['ACC_ObjLatPos']  # in car frame's y axis, left is negative
        self.pts[ii].vRel = cpt["SCC11"]['ACC_ObjRelSpd']
        self.pts[ii].aRel = float('nan')
        self.pts[ii].yvRel = float('nan')
        self.pts[ii].measured = True
        """
        now = time.monotonic()
        if self.lastdRel is not None and self.lastTime is not None:
          dd = dRel - self.lastdRel
          dt = now - self.lastTime

          if dt > 0.:
            v = dd / dt

            if self.v_rel_kf is None:
              self.v_rel_kf = KF1D(x0=[[0.0], [0.0]],
                                   A=[[1.0, self.radar_ts], [0.0, 1.0]],
                                   C=[1.0, 0.0],
                                   K=[[0.12287673], [0.29666309]])

            vRel = self.v_rel_kf.update(v)[0]
            if abs(vRel) > abs(self.pts[ii].vRel):
              self.pts[ii].vRel = vRel

        self.lastdRel = dRel
        self.lastTime = now
        """

      else:
        if ii in self.pts:
          del self.pts[ii]

        self.reset_v_rel()

    ret.points = list(self.pts.values())
    return ret
