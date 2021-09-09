import copy

import crcmod
from selfdrive.car.hyundai.values import CAR, CHECKSUM, FEATURES, EV_HYBRID_CAR

hyundai_checksum = crcmod.mkCrcFun(0x11D, initCrc=0xFD, rev=False, xorOut=0xdf)


def create_lkas11(packer, frame, car_fingerprint, apply_steer, steer_req,
                  lkas11, sys_warning, sys_state, enabled,
                  left_lane, right_lane,
                  left_lane_depart, right_lane_depart, bus, ldws_opt):
  values = copy.copy(lkas11)
  values["CF_Lkas_LdwsSysState"] = sys_state
  values["CF_Lkas_SysWarning"] = 3 if sys_warning else 0
  values["CF_Lkas_LdwsLHWarning"] = left_lane_depart
  values["CF_Lkas_LdwsRHWarning"] = right_lane_depart
  values["CR_Lkas_StrToqReq"] = apply_steer
  values["CF_Lkas_ActToi"] = steer_req
  values["CF_Lkas_ToiFlt"] = 0
  values["CF_Lkas_MsgCount"] = frame % 0x10

  if car_fingerprint in FEATURES["send_lfa_mfa"]:
    values["CF_Lkas_LdwsActivemode"] = int(left_lane) + (int(right_lane) << 1)
    values["CF_Lkas_LdwsOpt_USM"] = 2

    # FcwOpt_USM 5 = Orange blinking car + lanes
    # FcwOpt_USM 4 = Orange car + lanes
    # FcwOpt_USM 3 = Green blinking car + lanes
    # FcwOpt_USM 2 = Green car + lanes
    # FcwOpt_USM 1 = White car + lanes
    # FcwOpt_USM 0 = No car + lanes
    values["CF_Lkas_FcwOpt_USM"] = 2 if enabled else 1

    # SysWarning 4 = keep hands on wheel
    # SysWarning 5 = keep hands on wheel (red)
    # SysWarning 6 = keep hands on wheel (red) + beep
    # Note: the warning is hidden while the blinkers are on
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

  elif car_fingerprint == CAR.GENESIS:
    # This field is actually LdwsActivemode
    # Genesis and Optima fault when forwarding while engaged
    values["CF_Lkas_LdwsActivemode"] = 2
    values["CF_Lkas_SysWarning"] = lkas11["CF_Lkas_SysWarning"]

  elif car_fingerprint == CAR.SONATA_LF_TURBO:
    values["CF_Lkas_LdwsOpt_USM"] = 2
    values["CF_Lkas_FcwOpt_USM"] = 2 if enabled else 1
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

  if ldws_opt:
    values["CF_Lkas_LdwsOpt_USM"] = 3

  dat = packer.make_can_msg("LKAS11", 0, values)[2]

  if car_fingerprint in CHECKSUM["crc8"]:
    # CRC Checksum as seen on 2019 Hyundai Santa Fe
    dat = dat[:6] + dat[7:8]
    checksum = hyundai_checksum(dat)
  elif car_fingerprint in CHECKSUM["6B"]:
    # Checksum of first 6 Bytes, as seen on 2018 Kia Sorento
    checksum = sum(dat[:6]) % 256
  else:
    # Checksum of first 6 Bytes and last Byte as seen on 2018 Kia Stinger
    checksum = (sum(dat[:6]) + dat[7]) % 256

  values["CF_Lkas_Chksum"] = checksum

  return packer.make_can_msg("LKAS11", bus, values)

def create_clu11(packer, frame, bus, clu11, button, speed):
  values = copy.copy(clu11)
  values["CF_Clu_CruiseSwState"] = button
  values["CF_Clu_Vanz"] = speed
  values["CF_Clu_AliveCnt1"] = frame
  return packer.make_can_msg("CLU11", bus, values)

def create_lfahda_mfc(packer, enabled, active):
  values = {
    "LFA_Icon_State": 2 if enabled else 0,
    "HDA_Active": 1 if active > 0 else 0,
    "HDA_Icon_State": 2 if active > 0 else 0,
    # "HDA_VSetReq": 0,
  }

  # VAL_ 1157 LFA_Icon_State 0 "no_wheel" 1 "white_wheel" 2 "green_wheel" 3 "green_wheel_blink";
  # VAL_ 1157 LFA_SysWarning 0 "no_message" 1 "switching_to_hda" 2 "switching_to_scc" 3 "lfa_error" 4 "check_hda" 5 "keep_hands_on_wheel_orange" 6 "keep_hands_on_wheel_red";
  # VAL_ 1157 HDA_Icon_State 0 "no_hda" 1 "white_hda" 2 "green_hda";
  # VAL_ 1157 HDA_SysWarning 0 "no_message" 1 "driving_convenience_systems_cancelled" 2 "highway_drive_assist_system_cancelled";

  return packer.make_can_msg("LFAHDA_MFC", 0, values)

def create_hda_mfc(packer, active, state):
  values = {
    "HDA_USM": 2,
    "HDA_Active": 1 if active > 0 else 0,
    "HDA_Icon_State": state if active > 0 else 0,
  }

  return packer.make_can_msg("LFAHDA_MFC", 0, values)

def create_mdps12(packer, frame, mdps12):
  values = copy.copy(mdps12)
  values["CF_Mdps_ToiActive"] = 0
  values["CF_Mdps_ToiUnavail"] = 1
  values["CF_Mdps_MsgCount2"] = frame % 0x100
  values["CF_Mdps_Chksum2"] = 0

  dat = packer.make_can_msg("MDPS12", 2, values)[2]
  checksum = sum(dat) % 256
  values["CF_Mdps_Chksum2"] = checksum

  return packer.make_can_msg("MDPS12", 2, values)

def create_scc11(packer, frame, enabled, set_speed, lead_visible, scc_live, scc11, active_cam, stock_cam):
  values = copy.copy(scc11)
  values["AliveCounterACC"] = frame // 2 % 0x10

  if not stock_cam:
    values["Navi_SCC_Camera_Act"] = 2 if active_cam else 0
    values["Navi_SCC_Camera_Status"] = 2 if active_cam else 0

  if not scc_live:
    values["MainMode_ACC"] = 1
    values["VSetDis"] = set_speed
    values["ObjValid"] = 1 if enabled else 0
#  values["ACC_ObjStatus"] = lead_visible

  return packer.make_can_msg("SCC11", 0, values)

def create_scc12(packer, apply_accel, enabled, cnt, scc_live, scc12, gaspressed, brakepressed,
                 standstill, car_fingerprint):
  values = copy.copy(scc12)

  if car_fingerprint in EV_HYBRID_CAR:
    # from xps-genesis
    if enabled and not brakepressed:
      values["ACCMode"] = 2 if gaspressed and (apply_accel > -0.2) else 1
      if apply_accel < 0.0 and standstill:
        values["StopReq"] = 1
      values["aReqRaw"] = apply_accel
      values["aReqValue"] = apply_accel
    else:
      values["ACCMode"] = 0
      values["aReqRaw"] = 0
      values["aReqValue"] = 0

    if not scc_live:
      values["CR_VSM_Alive"] = cnt

  else:
    values["aReqRaw"] = apply_accel if enabled else 0  # aReqMax
    values["aReqValue"] = apply_accel if enabled else 0  # aReqMin
    values["CR_VSM_Alive"] = cnt
    if not scc_live:
      values["ACCMode"] = 1 if enabled else 0  # 2 if gas padel pressed

  values["CR_VSM_ChkSum"] = 0
  dat = packer.make_can_msg("SCC12", 0, values)[2]
  values["CR_VSM_ChkSum"] = 16 - sum([sum(divmod(i, 16)) for i in dat]) % 16

  return packer.make_can_msg("SCC12", 0, values)

def create_scc13(packer, scc13):
  values = copy.copy(scc13)
  return packer.make_can_msg("SCC13", 0, values)

def create_scc14(packer, enabled, e_vgo, standstill, accel, gaspressed, objgap, scc14):
  values = copy.copy(scc14)

  # from xps-genesis
  if enabled:
    values["ACCMode"] = 2 if gaspressed and (accel > -0.2) else 1
    values["ObjGap"] = objgap
    if standstill:
      values["JerkUpperLimit"] = 0.5
      values["JerkLowerLimit"] = 10.
      values["ComfortBandUpper"] = 0.
      values["ComfortBandLower"] = 0.
      if e_vgo > 0.27:
        values["ComfortBandUpper"] = 2.
        values["ComfortBandLower"] = 0.
    else:
      values["JerkUpperLimit"] = 50.
      values["JerkLowerLimit"] = 50.
      values["ComfortBandUpper"] = 50.
      values["ComfortBandLower"] = 50.

  return packer.make_can_msg("SCC14", 0, values)

