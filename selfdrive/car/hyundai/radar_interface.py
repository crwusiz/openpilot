import math

from cereal import car
from opendbc.can.parser import CANParser
from openpilot.selfdrive.car.interfaces import RadarInterfaceBase
from openpilot.selfdrive.car.hyundai.values import DBC, CANFD_CAR
from openpilot.common.params import Params
from openpilot.common.filter_simple import StreamingMovingAverage

RADAR_START_ADDR = 0x500
RADAR_MSG_COUNT = 32

# POC for parsing corner radars: https://github.com/commaai/openpilot/pull/24221/

def get_radar_can_parser(CP):
  radar_tracks_enable = Params().get_bool("RadarTrackEnable")

  if DBC[CP.carFingerprint]['radar'] is None or not radar_tracks_enable:
    return None

  print()
  print("RadarInterface: RadarTracks...")
  print()
  messages = [(f"RADAR_TRACK_{addr:x}", 50) for addr in range(RADAR_START_ADDR, RADAR_START_ADDR + RADAR_MSG_COUNT)]
  return CANParser(DBC[CP.carFingerprint]['radar'], messages, 1)

def get_radar_can_parser_scc(CP):
  scc2 = Params().get_bool("SccOnBus2")

  if CP.carFingerprint in CANFD_CAR:
    return None
  print()
  print("RadarInterface: SCC Radar (Bus{})".format( 2 if scc2 else 0))
  print()
  messages = [("SCC11", 50)]
  return CANParser(DBC[CP.carFingerprint]['pt'], messages, 2 if scc2 else 0)

class RadarInterface(RadarInterfaceBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.updated_messages = set()
    self.trigger_msg = RADAR_START_ADDR + RADAR_MSG_COUNT - 1
    self.track_id = 1

    self.radar_off_can = CP.radarUnavailable
    self.rcp = get_radar_can_parser(CP)

    self.rcp_scc = get_radar_can_parser_scc(CP)
    self.updated_messages_scc = set()
    self.trigger_msg_scc = 0x420
    self.dRelFilter = StreamingMovingAverage(2)
    self.vRelFilter = StreamingMovingAverage(4)

  def update(self, can_strings):
    if self.radar_off_can or (self.rcp is None) and (self.rcp_scc is None):
      return super().update(None)

    if self.rcp is not None:
      vls = self.rcp.update_strings(can_strings)
      self.updated_messages.update(vls)

    if self.rcp_scc is not None:
      vls_scc = self.rcp_scc.update_strings(can_strings)
      self.updated_messages_scc.update(vls_scc)

    trigger_msg_radar = True if self.trigger_msg in self.updated_messages else False
    trigger_msg_scc = True if self.trigger_msg_scc in self.updated_messages_scc else False

    if trigger_msg_radar or trigger_msg_scc:
      rr = self._update(self.updated_messages, trigger_msg_radar, trigger_msg_scc)
    else:
      return None

    self.updated_messages.clear()
    self.updated_messages_scc.clear()

    return rr

  def _update(self, updated_messages, trigger_msg_radar, trigger_msg_scc):
    ret = car.RadarData.new_message()
    if self.rcp is None and self.rcp_scc is None:
      return ret

    errors = []

    if self.rcp is not None and trigger_msg_radar:
      if not self.rcp.can_valid:
        errors.append("canError")

      for addr in range(RADAR_START_ADDR, RADAR_START_ADDR + RADAR_MSG_COUNT):
        msg = self.rcp.vl[f"RADAR_TRACK_{addr:x}"]

        if addr not in self.pts:
          self.pts[addr] = car.RadarData.RadarPoint.new_message()
          self.pts[addr].trackId = self.track_id
          self.track_id += 1

        valid = msg['STATE'] in (3, 4)
        if valid:
          azimuth = math.radians(msg['AZIMUTH'])
          self.pts[addr].measured = True
          self.pts[addr].dRel = math.cos(azimuth) * msg['LONG_DIST']
          self.pts[addr].yRel = 0.5 * -math.sin(azimuth) * msg['LONG_DIST']
          self.pts[addr].vRel = msg['REL_SPEED']
          self.pts[addr].aRel = msg['REL_ACCEL']
          self.pts[addr].yvRel = float('nan')

        else:
          del self.pts[addr]

    if self.rcp_scc is not None and trigger_msg_scc:
      if not self.rcp_scc.can_valid:
        errors.append("canError")

      cpt = self.rcp_scc.vl
      dRel = cpt["SCC11"]['ACC_ObjDist']
      vRel = cpt["SCC11"]['ACC_ObjRelSpd']
      valid = cpt["SCC11"]['ACC_ObjStatus'] and dRel < 150
      if valid:
        target_id = 0
        if target_id not in self.pts:
          self.pts[target_id] = car.RadarData.RadarPoint.new_message()
          self.pts[target_id].trackId = 0 #self.track_id
          #self.track_id += 1
          dRel = self.dRelFilter.set(dRel)
          vRel = self.vRelFilter.set(vRel)
        else:
          dRel = self.dRelFilter.process(dRel)
          vRel = self.vRelFilter.process(vRel)
          self.pts[target_id].dRel = dRel
          self.pts[target_id].yRel = -cpt["SCC11"]['ACC_ObjLatPos']  # in car frame's y axis, left is negative
          self.pts[target_id].vRel = vRel
          self.pts[target_id].aRel = float('nan')
          self.pts[target_id].yvRel = float('nan')
          self.pts[target_id].measured = True
      else:
        self.pts.clear()

    ret.points = list(self.pts.values())
    ret.errors = errors
    return ret