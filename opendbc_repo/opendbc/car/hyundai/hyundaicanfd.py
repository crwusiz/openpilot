import copy
import numpy as np
from opendbc.car import CanBusBase
from opendbc.car.crc import CRC16_XMODEM
from opendbc.car.hyundai.values import HyundaiFlags, HyundaiExFlags

from openpilot.common.params import Params
from openpilot.selfdrive.addon.navi_controller import SpeedLimiter

from openpilot.cereal import log

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
TurnDirection = log.Desire

class CanBus(CanBusBase):
  def __init__(self, CP, fingerprint=None, lka_steering=None) -> None:
    super().__init__(CP, fingerprint)

    if lka_steering is None:
      lka_steering = CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG.value if CP is not None else False

    # On the CAN-FD platforms, the LKAS camera is on both A-CAN and E-CAN. LKA steering cars
    # have a different harness than the LFA steering variants in order to split
    # a different bus, since the steering is done by different ECUs.
    self._a, self._e = 1, 0
    if lka_steering and not Params().get_bool("CameraSccEnable"):  #배선개조는 무조건 Bus0가 ECAN임.
      self._a, self._e = 0, 1

    self._a += self.offset
    self._e += self.offset
    self._cam = 2 + self.offset

  @property
  def ECAN(self):
    return self._e

  @property
  def ACAN(self):
    return self._a

  @property
  def CAM(self):
    return self._cam


def create_steering_messages(packer, CP, CC, CS, CAN, frame, lat_active, apply_torque, apply_angle, angle_max_torque):
  enabled = CC.enabled
  angle_control = CP.flags & HyundaiFlags.CANFD_ANGLE_STEER_MSG
  camera_scc = CP.flags & HyundaiFlags.CANFD_CAMERA_SCC

  common_values = {
    "LKA_OptUsmSta": 2,
    "LKA_ICON": 2 if enabled else 1,
    "StrTqReqVal": apply_torque,
    "LKA_SysWrn": 0,
    "ActToiSta": 1 if lat_active else 0,
    "LKA_UsmMod": 0,  # hide LKAS settings
    "LKA_RcgSta": 0,
    "Damping_Gain": 100,  # can potentially tuned for better perf [3, 200]
  }

  lfa_values = copy.copy(common_values)

  lkas_values = copy.copy(common_values)

  # For cars with an ADAS ECU (commonly HDA2), by sending LKAS actuation messages we're
  # telling the ADAS ECU to forward our steering and disable stock LFA lane centering.
  ret = []

  values = copy.copy(CS.mdps_info)
  if angle_control:
    if CS.lfa_alt_info is not None:
      values["ADAS_ActiveStat_Lv2"] = CS.lfa_alt_info["ADAS_AngleActiveStat_Lv2"]
  else:
    if CS.lfa_info is not None:
      values["LKA_RcgSta"] = 1 if CS.lfa_info["ActToiSta"] == 1 else 0

  if frame % 1000 < 40:
    values["OutTorque"] += 220
  ret.append(packer.make_can_msg("MDPS", CAN.CAM, values))

  if frame % 10 == 0:
    if CP.exFlags & HyundaiExFlags.HOD:
      values = copy.copy(CS.hod_info)
      if frame % 1000 < 40:
        values["TOUCH_DETECT"] = 3
        values["TOUCH1"] = 50
        values["TOUCH2"] = 50
        values["CHECKSUM_"] = 0
        dat = packer.make_can_msg("HANDS_ON_DETECTION", 0, values)[1]
        values["CHECKSUM_"] = hyundai_crc8(dat[1:8])

      ret.append(packer.make_can_msg("HANDS_ON_DETECTION", CAN.CAM, values))

  if angle_control:
    if camera_scc:
      lfa_values |= {
        "LKA_OptUsmSta": 0,  # TODO: not used by the stock system
        "StrTqReqVal": 0,  # we don't use torque
        "ActToiSta": 0,  # we don't use torque
        # this goes 0 when LFA lane changes, 3 when LKA_ICON is >=green
        "LKA_RcgSta": 3 if lat_active else 0,
        #"ADAS_AngleReq": 0,
        #"ADAS_AngleActiveStat_Lv2": 0,
        #"ADAS_AngleTorqueGain": 0,
      }

      values = {
        "ADAS_AngleReq": apply_angle,
        "ADAS_AngleActiveStat_Lv2": 2 if lat_active else 1,
        "ADAS_AngleTorqueGain": angle_max_torque if lat_active else 0,
      }
      ret.append(packer.make_can_msg("LFA_ALT", CAN.ECAN, values))

    else:
      lkas_values |= {
        "LKA_OptUsmSta": 0,  # TODO: not used by the stock system
        "StrTqReqVal": 0,  # we don't use torque
        "ActToiSta": 0,  # we don't use torque
        # this goes 0 when LFA lane changes, 3 when LKA_ICON is >=green
        "LKA_RcgSta": 3 if lat_active else 0,
        "ADAS_AngleReq": apply_angle,
        "ADAS_AngleActiveStat_Lv2": 2 if lat_active else 1,
        "ADAS_AngleTorqueGain": angle_max_torque if lat_active else 0,
      }

    if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
      lkas_msg = "LKAS_ALT" if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT else "LKAS"
      if CP.openpilotLongitudinalControl:
        ret.append(packer.make_can_msg("LFA", CAN.ECAN, lfa_values))
      if not (CP.flags & HyundaiFlags.CANFD_CAMERA_SCC):
        ret.append(packer.make_can_msg(lkas_msg, CAN.ACAN, lkas_values))
    else:
      ret.append(packer.make_can_msg("LFA", CAN.ECAN, lfa_values))

  return ret


def create_suppress_lfa(packer, CP, CC, CS, CAN):
  enabled = CC.enabled
  #lfa_block_msg = CS.lfa_block_msg
  #lka_steering_alt = CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT
  #suppress_msg = "CAM_0x362" if lka_steering_alt else "CAM_0x2a4"
  #msg_bytes = 32 if lka_steering_alt else 24
  #values = {f"BYTE{i}": lfa_block_msg[f"BYTE{i}"] for i in range(3, msg_bytes) if i != 7}

  if CS.msg_0x362 is not None:
    suppress_msg = "CAM_0x362"
    lfa_block_msg = CS.msg_0x362
  elif CS.msg_0x2a4 is not None:
    suppress_msg = "CAM_0x2a4"
    lfa_block_msg = CS.msg_0x2a4
  else:
    return []

  values = copy.copy(lfa_block_msg)
  values["COUNTER"] = lfa_block_msg["COUNTER"]
  values["SET_ME_0"] = 0
  values["SET_ME_0_2"] = 0
  values["LEFT_LANE_LINE"] = 0 if enabled else 3
  values["RIGHT_LANE_LINE"] = 0 if enabled else 3
  return [packer.make_can_msg(suppress_msg, CAN.ACAN, values)]


def create_buttons(packer, CP, CAN, cnt, btn):
  values = {
    "COUNTER": cnt,
    "SET_ME_1": 1,
    "CRUISE_BUTTONS": btn,
  }
  bus = CAN.ECAN if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else CAN.CAM
  return packer.make_can_msg("CRUISE_BUTTONS", bus, values)


def create_buttons_canfd_alt(packer, CP, CAN, cnt, btn):
  values = {
    "COUNTER": cnt % 256,
    "CRUISE_BUTTONS": btn,
  }
  bus = CAN.ECAN if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else CAN.CAM
  return packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, values)


def create_acc_cancel(packer, CP, CS, CAN):
  cruise_info_copy = CS.cruise_info
  camera_scc = CP.flags & HyundaiFlags.CANFD_CAMERA_SCC

  # CAN FD camera-based SCC requires additional signals to be preserved
  # verbatim from the previous SCC_CONTROL frame to avoid checksum or
  # state validation faults. Classic CAN SCC only validates a subset.
  if camera_scc:
    values = {s: cruise_info_copy[s] for s in [
      "COUNTER",
      "CHECKSUM",
      "SysFailStat",
      "MainMode_ACC",
      "ACCMode",
      "TakeoverReq",
      "InfoDisplay",
      "AlertDisplay",
      "DistanceGapSet",
      "VSetDis",
    ]}
  else:
    values = {s: cruise_info_copy[s] for s in [
      "COUNTER",
      "CHECKSUM",
      "ACCMode",
      "VSetDis",
      "InfoDisplay",
    ]}
  values.update({
    "ACCMode": 4,
    "AccelRequestRaw": 0.0,
    "AccelRequest": 0.0,
  })
  return packer.make_can_msg("SCC_CONTROL", CAN.ECAN, values)


def create_lfahda_cluster(packer, CC, CS, CAN):
  if CS.lfahda_cluster_info is not None:
    values = {
      "HDA_CntrlModSta": 2 if CC.longActive else 0,
      "HDA_LFA_SymSta": 2 if CC.latActive else 0,
    }
  else:
    return []
  return [packer.make_can_msg("LFAHDA_CLUSTER", CAN.ECAN, values)]


def create_acc_control(packer, CP, CC, CS, CAN, accel_last, accel, stopping, set_speed, hud):
  enabled = CC.enabled
  gas_override = CC.cruiseControl.override
  camera_scc = CP.flags & HyundaiFlags.CANFD_CAMERA_SCC

  jerk = 5
  jn = jerk / 50
  if not enabled or gas_override:
    a_val, a_raw = 0, 0
  else:
    a_raw = accel
    a_val = np.clip(accel, accel_last - jn, accel_last + jn)

  if camera_scc:
    values = copy.copy(CS.cruise_info)
    values |= {
      "ACCMode": 0 if not enabled else (2 if gas_override else 1),
      "MainMode_ACC": 1,
      "StopReq": 1 if stopping else 0,
      "AccelRequest": a_val,
      "AccelRequestRaw": a_raw,
      "VSetDis": set_speed,
      "JerkUpperLimit": jerk if enabled else 1,
      "JerkLowerLimit": 3.0,

      #"ObjectDistance": 1,
      "NSCC_MainStat": 2,
      "SET_ME_TMP_64": 0x64,
      "DistanceGapSet": hud.leadDistanceBars,
      "InfoDisplay": 4 if stopping and CS.out.aEgo > -0.3 else 0,

      "AlertDisplay": 0,
      "TakeoverReq": 0,
      "AccelLimitBandUpper": 0,
      "AccelLimitBandLower": 0,
      "SysFailStat": 0,
    }

    hud_lead_info = 0
    if hud.leadVisible:
      hud_lead_info = 1 if values["ObjectRelativeSpeed"] > 0 else 2
    values["ObjectStat"] = hud_lead_info

  else:
    values = {
      "ACCMode": 0 if not enabled else (2 if gas_override else 1),
      "MainMode_ACC": 1,
      "StopReq": 1 if stopping else 0,
      "AccelRequest": a_val,
      "AccelRequestRaw": a_raw,
      "VSetDis": set_speed,
      "JerkUpperLimit": jerk if enabled else 1,
      "JerkLowerLimit": 3.0,

      "ObjectStat": 2,
      "NSCC_MainStat": 2,
      "ObjectRelativeSpeed": 0,
      "SET_ME_TMP_64": 0x64,
      "DistanceGapSet": hud.leadDistanceBars,
      "InfoDisplay": 4 if stopping and CS.out.cruiseState.standstill else 0,
    }

    # fixes auto regen stuck on max for hybrids, should probably apply to all cars
    values.update(
      {"ObjectDistance": 1} if CS.cruise_info is None else {s: CS.cruise_info[s] for s in ["ObjectDistance", "ObjectRelativeSpeed"]})

  return packer.make_can_msg("SCC_CONTROL", CAN.ECAN, values)


def create_spas_messages(packer, CC, CAN):
  ret = []
  ret.append(packer.make_can_msg("SPAS1", CAN.ECAN, {}))
  blink = 0
  if CC.leftBlinker: blink = 3
  elif CC.rightBlinker: blink = 4
  ret.append(packer.make_can_msg("SPAS2", CAN.ECAN, {"BLINKER_CONTROL": blink}))
  return ret


def create_fca_warning_light(packer, CP, CAN, frame):
  ret = []
  if CP.flags & HyundaiFlags.CANFD_CAMERA_SCC.value:
    return ret
  if frame % 2 == 0:
    values = {
      'AEB_SETTING': 0x1,  # show AEB disabled icon
      'SET_ME_2': 0x2,
      'SET_ME_FF': 0xff,
      'SET_ME_FC': 0xfc,
      'SET_ME_9': 0x9,
    }
    ret.append(packer.make_can_msg("ADRV_0x160", CAN.ECAN, values))
  return ret


def create_adrv_messages(packer, CP, CC, CS, md, CAN, frame, set_speed, hud):
  main_enabled = CS.out.cruiseState.available
  cruise_enabled = CC.enabled
  lat_active = CC.latActive
  ccnc = CP.exFlags & HyundaiExFlags.CCNC
  speed_limiter = SpeedLimiter.instance()
  speed_limiter.recv()
  nav_active = speed_limiter.get_active()
  hdp_active = cruise_enabled and nav_active
  enable_corner_radar = CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG
  desire, lane_changing = _get_desire_and_lane_changing(md)

  # messages needed to car happy after disabling
  # the ADAS Driving ECU to do longitudinal control

  ret = []
  if CP.flags & HyundaiFlags.CANFD_CAMERA_SCC:
    HDA_CntrlModSta = 0
    if CS.lfahda_cluster_info is not None:
      HDA_CntrlModSta = CS.lfahda_cluster_info["HDA_CntrlModSta"]

    if frame % 2 == 0 and CS.cruise_buttons_msg is not None:
      values = copy.copy(CS.cruise_buttons_msg)
      if CS.lfahda_cluster_info is not None and CS.lfahda_cluster_info["HDA_LFA_SymSta"] == 0 and 0 < frame % 200 < 12:
        values["LDA_BTN"] = 1

      if CC.enabled and not CS.MainMode_ACC and 10 < frame % 200 <= 16 and CS.out.vEgo > 3.:
        values["ADAPTIVE_CRUISE_MAIN_BTN"] = 1
      elif CS.adrv_msg_1ea is not None and CS.adrv_msg_1ea["HDA_MODE2"] == 0:  # if corner radar is disabled, send main btn
        if 10 < frame % 1000 <= 16 and CS.out.vEgo > 3:
          values["ADAPTIVE_CRUISE_MAIN_BTN"] = 1
      else:
        values["ADAPTIVE_CRUISE_MAIN_BTN"] = 0

      ret.append(packer.make_can_msg(CS.cruise_btns_msg_canfd, CAN.CAM, values))

    if frame % 2 == 0 and CS.adrv_msg_160 is not None:
        values = copy.copy(CS.adrv_msg_160)
        ret.append(packer.make_can_msg("ADRV_0x160", CAN.ECAN, values))

    if frame % 5 == 0 and CS.ccnc_msg_161 is not None and ccnc:
      values = copy.copy(CS.ccnc_msg_161)
      values |= {
        "SETSPEED": 6 if hdp_active else 3 if main_enabled else 0,
        "SETSPEED_HUD": 5 if hdp_active else 2 if cruise_enabled else 1,
        "vSetDis": set_speed,

        "DISTANCE": 4 if hdp_active else hud.leadDistanceBars,
        "DISTANCE_LEAD": 2 if cruise_enabled and hud.leadVisible else 0,
        "DISTANCE_CAR": 3 if hdp_active else 2 if cruise_enabled else 1 if main_enabled else 0,
        "DISTANCE_SPACING": 5 if hdp_active else 1 if cruise_enabled else 0,

        "TARGET": 1 if cruise_enabled else 0,
        "TARGET_DISTANCE": int(hud.leadDistance),

        "BACKGROUND": 1 if cruise_enabled else 3 if main_enabled else 7,
        "CENTERLINE": 1 if HDA_CntrlModSta > 0 else 0, #lat_active else 0,
        "CAR_CIRCLE": 2 if hdp_active else 1 if lat_active else 0,

        "NAV_ICON": 2 if nav_active else 1,
        "HDA_ICON": 5 if hdp_active else 2 if lat_active else 1,
        "LFA_ICON": 5 if hdp_active else 2 if lat_active else 1,
        "LKA_ICON": 4 if lat_active else 3,
        "FCA_ALT_ICON": 0,
        "DAW_ICON": 0,

        "LCA_LEFT_ARROW": 2 if CS.out.leftBlinker else 0,
        "LCA_RIGHT_ARROW": 2 if CS.out.rightBlinker else 0,

        "LCA_LEFT_ICON": 1 if CS.out.leftBlindspot else 2,
        "LCA_RIGHT_ICON": 1 if CS.out.rightBlindspot else 2,

        "SOUNDS_2": 0,
      }

      if md is not None:
        desire_raw = md.meta.desire.raw
        values["LANE_LEFT"] = 1 if desire_raw in (1, 3) else 0
        values["LANE_RIGHT"] = 1 if desire_raw in (2, 4) else 0

      alerts_disable_map = {
        "ALERTS_2": [1, 2, 5],
        "ALERTS_3": [17, 26],
        "ALERTS_5": [1, 4, 5],
      }

      for key, reset_values in alerts_disable_map.items():
        if values.get(key) in reset_values:
          values[key] = 0

      _make_ccnc_values(
        values, CS, lat_active, frame, hud,
        lane_line=True,
        corner_radar=False,
        desire=0
      )

      curvature = round(CS.out.steeringAngleDeg / 3)
      values["LANELINE_CURVATURE"] = (min(abs(curvature), 15) + (-1 if curvature < 0 else 0)) if lat_active else 0
      values["LANELINE_CURVATURE_DIRECTION"] = 1 if curvature < 0 and lat_active else 0

      values["LANELINE_LEFT"] = _get_lane_value(CS.out.leftLaneLine, CS.out.leftBlindspot, hud.leftLaneDepart, hud.leftLaneVisible, frame)
      values["LANELINE_RIGHT"] = _get_lane_value(CS.out.rightLaneLine, CS.out.rightBlindspot, hud.rightLaneDepart, hud.rightLaneVisible, frame)

      if lat_active and (CS.out.leftBlinker or CS.out.rightBlinker):
        left_lane_raw, right_lane_raw = CS.leftLnPosition, CS.rightLnPosition

        scale_per_m = 15 / 1.7
        left_lane = abs(int(round(15 + (left_lane_raw - 1.7) * scale_per_m)))
        right_lane = abs(int(round(15 + (right_lane_raw - 1.7) * scale_per_m)))

        if CS.leftLnQualStat not in (2, 3):
          left_lane = 0
        if CS.rightLnQualStat not in (2, 3):
          right_lane = 0

        if left_lane_raw == -2.0248375:
          left_lane = 30 - right_lane
        if right_lane_raw == 2.0248375:
          right_lane = 30 - left_lane

        if left_lane_raw == right_lane_raw == 0:
          left_lane = right_lane = 15
        elif left_lane_raw == 0:
          left_lane = 30 - right_lane
        elif right_lane_raw == 0:
          right_lane = 30 - left_lane

        total = left_lane + right_lane
        if total == 0:
          left_lane = right_lane = 15
        else:
          left_lane = round((left_lane / total) * 30)
          right_lane = 30 - left_lane

        values["LANELINE_LEFT_POSITION"] = left_lane
        values["LANELINE_RIGHT_POSITION"] = right_lane

      ret.append(packer.make_can_msg("CCNC_0x161", CAN.ECAN, values))

    if frame % 5 == 0 and CS.ccnc_msg_162 is not None and ccnc:
      values = copy.copy(CS.ccnc_msg_162)
      for f in {"FAULT_FCA", "FAULT_LSS", "FAULT_DAS", "FAULT_LFA"}:
        values[f] = 0

      _make_ccnc_values(
        values, CS, lat_active, frame, hud,
        lane_line=False,
        corner_radar=True,
        desire=0,
        blink_pairs=[('LR_DETECT', 'LR_DETECT_DISTANCE'),
                     ('RR_DETECT', 'RR_DETECT_DISTANCE')],
        blink_t=1.0
      )

      if hud.leftLaneDepart or hud.rightLaneDepart:
        values["VIBRATE"] = 1

      ret.append(packer.make_can_msg("CCNC_0x162", CAN.ECAN, values))

    if frame % 5 == 0 and CS.adrv_msg_1ea is not None:
      values = copy.copy(CS.adrv_msg_1ea)
      values['LEFT_BLINK_HOLD'] = 1 if lane_changing == 3 else 0
      values['RIGHT_BLINK_HOLD'] = 1 if lane_changing == 4 else 0

      _make_ccnc_values(
        values, CS, lat_active, frame, hud,
        lane_line=True,
        corner_radar=True,
        desire=desire,
        blink_pairs=[('LR_DETECT', 'LR_DETECT_DISTANCE'),
                     ('RR_DETECT', 'RR_DETECT_DISTANCE')],
        blink_t=1.0
      )

      ret.append(packer.make_can_msg("ADRV_0x1ea", CAN.ECAN, values))

    if enable_corner_radar > 0 and HDA_CntrlModSta == 0:
      if frame % 500 in [10, 20, 30]:
        values = {
          'BYTE_1': 0,
          'BYTE_2': 0,
          'BYTE_3': 0x80,
          'BYTE_4': 0x8A,
          'BYTE_5': 0x32,
          'BYTE_6': 0x30,
          'BYTE_7': 0x01,
          'BYTE_8': 0x00,
        }
        ret.append(packer.make_can_msg("Hud_Navi_V2_SEG_E", CAN.CAM, values))
      elif frame % 500 in [40, 50, 60]:
        values = {
          'BYTE_1': 0xff,
          'BYTE_2': 0xff,
          'BYTE_3': 0xff,
          'BYTE_4': 0xff,
          'BYTE_5': 0xff,
          'BYTE_6': 0xff,
          'BYTE_7': 0xff,
          'BYTE_8': 0xff,
        }
        ret.append(packer.make_can_msg("Hud_Navi_V2_SEG_E", CAN.CAM, values))

    return ret

  else:

    ret.extend(create_fca_warning_light(packer, CP, CAN, frame))
    if frame % 5 == 0:
      values = {
        #'HDA_MODE1': 0x8,
        'HDA_MODE2': 0x1,
        #'SET_ME_FF': 0xff,
      }
      ret.append(packer.make_can_msg("ADRV_0x1ea", CAN.ECAN, values))

      values = {
        'SET_ME_E1': 0xe1,
        #'SET_ME_3A': 0x3a,
      }
      ret.append(packer.make_can_msg("ADRV_0x200", CAN.ECAN, values))

    if frame % 20 == 0:
      values = {
        'SET_ME_15': 0x15,
      }
      ret.append(packer.make_can_msg("ADRV_0x345", CAN.ECAN, values))

    if frame % 100 == 0:
      values = {
        'SET_ME_22': 0x22,
        'SET_ME_41': 0x41,
      }
      ret.append(packer.make_can_msg("ADRV_0x1da", CAN.ECAN, values))

    return ret


def hkg_can_fd_checksum(address: int, sig, d: bytearray) -> int:
  crc = 0
  for i in range(2, len(d)):
    crc = ((crc << 8) ^ CRC16_XMODEM[(crc >> 8) ^ d[i]]) & 0xFFFF
  crc = ((crc << 8) ^ CRC16_XMODEM[(crc >> 8) ^ ((address >> 0) & 0xFF)]) & 0xFFFF
  crc = ((crc << 8) ^ CRC16_XMODEM[(crc >> 8) ^ ((address >> 8) & 0xFF)]) & 0xFFFF
  offsets = {8: 0x5F29, 16: 0x041D, 24: 0x819D, 32: 0x9F5B}
  return crc ^ offsets.get(len(d), 0)


def hyundai_crc8(data: bytes) -> int:
  poly, crc = 0x2F, 0xFF
  for byte in data:
    crc ^= byte
    for _ in range(8):
      crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
  return crc ^ 0xFF


def _clip_int(x, lo, hi):
  return lo if x < lo else hi if x > hi else int(x)

def _get_desire_and_lane_changing(md):
  desire, lane_changing = 0, 0
  if md is not None:
    desire = md.meta.desire.raw
    ds = md.meta.desireState
    for i in range(1, min(len(ds), 5)):
      if ds[i] > 0.3: lane_changing = i
  return desire, lane_changing

def _apply_lane_desire(values, desire):
  if desire == 1:  # 좌회전
    values['LANE_CHANGING'] = 1
    values["LANELINE_CURVATURE"] = 15
    values["LANELINE_CURVATURE_DIRECTION"] = 0

  elif desire == 2:  # 우회전
    values['LANE_CHANGING'] = 2
    values["LANELINE_CURVATURE"] = 15
    values["LANELINE_CURVATURE_DIRECTION"] = 1

  elif desire == 3:  # 좌차선변경
    values['LANE_CHANGING'] = 3

  elif desire == 4:  # 우차선변경
    values['LANE_CHANGING'] = 4

def _apply_radar_blink(values, radar_pairs, frame, *, disp_dist=30.0, min_dist=14.0, max_interval=100, t=1.0):
  for detect_key, dist_key in radar_pairs:
    if dist_key not in values:
      continue
    dist = values[dist_key]
    if dist <= min_dist:
      continue
    d = min(dist, disp_dist)
    interval = _clip_int(int((1 + (max_interval - 1) * (d / disp_dist)) * t), 1, max_interval)
    # 깜빡임 효과 시 차량 아이콘(4: WHITE CAR / 0: HIDDEN) 토글
    values[detect_key] = 4 if (frame // interval) & 1 else 0
    values[dist_key] = min_dist

def _make_ccnc_values(values, CS, lat_active, frame, hud, lane_line=True, corner_radar=True, desire=0, blink_pairs=None, blink_t=1.0):
  SENSORS = {
    'ff': 'FF',
    'lf': 'LF',
    'rf': 'RF',
    'lr': 'LR',
    'rr': 'RR'
  }

  # 1. 차선 정보 처리
  if lane_line:
    curvature = round(CS.out.steeringAngleDeg / 3)
    values["LANELINE_CURVATURE"] = (min(abs(curvature), 15) + (-1 if curvature < 0 else 0)) if lat_active else 0
    values["LANELINE_CURVATURE_DIRECTION"] = (1 if curvature < 0 else 0) if lat_active else 0
    if desire:
      _apply_lane_desire(values, desire)

  # 2. 통합 센서 처리 (코너 레이더)
  if corner_radar:
    for prefix, can_name in SENSORS.items():
      dist = getattr(CS, f"{prefix}_distance", 0)
      detect_key = f"{can_name}_DETECT"
      dist_key = f"{can_name}_DETECT_DISTANCE"

      # 거리 기반 기본 상태 설정 (3: GRAY CAR, 4: WHITE CAR)
      if dist > 0:
        if detect_key in values:
          values[detect_key] = 3 if dist > 30 else 4
        if dist_key in values:
          values[dist_key] = dist

    # 3. 깜빡임 효과 적용
    if blink_pairs:
      _apply_radar_blink(values, blink_pairs, frame, t=blink_t)

def _get_lane_value(prob, blindspot, depart, visible, frame):
  if depart:
    return 4 if (frame // 50) % 2 == 0 else 1

  if not visible:
    return 0

  return 4 if (prob >= 20 or blindspot) else 2
