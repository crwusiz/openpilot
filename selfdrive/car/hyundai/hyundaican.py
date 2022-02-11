import copy

import crcmod
from selfdrive.car.hyundai.values import CAR, CHECKSUM, EV_HYBRID_CAR
from common.params import Params

hyundai_checksum = crcmod.mkCrcFun(0x11D, initCrc=0xFD, rev=False, xorOut=0xdf)


def create_lkas11(packer, frame, car_fingerprint, apply_steer, steer_req, lkas11, sys_warning, sys_state, enabled,
                  left_lane, right_lane, left_lane_depart, right_lane_depart, bus):
  values = copy.copy(lkas11)
  values["CF_Lkas_LdwsSysState"] = sys_state
  values["CF_Lkas_SysWarning"] = 3 if sys_warning else 0
  values["CF_Lkas_LdwsLHWarning"] = left_lane_depart
  values["CF_Lkas_LdwsRHWarning"] = right_lane_depart
  values["CR_Lkas_StrToqReq"] = apply_steer
  values["CF_Lkas_ActToi"] = steer_req
  values["CF_Lkas_ToiFlt"] = 0
  values["CF_Lkas_MsgCount"] = frame % 0x10
  values["CF_Lkas_Chksum"] = 0

  if car_fingerprint == CAR.GENESIS:
    values["CF_Lkas_LdwsActivemode"] = 2
    values["CF_Lkas_SysWarning"] = lkas11["CF_Lkas_SysWarning"]

  elif car_fingerprint in [CAR.K5, CAR.K5_HEV, CAR.K7, CAR.K7_HEV]:
    values["CF_Lkas_LdwsActivemode"] = 0

  if Params().get("MfcSelect", encoding='utf8') == "1":
    # This field is LDWS Mfc car ( qt ui toggle set )
    values["CF_Lkas_LdwsActivemode"] = 0
    values["CF_Lkas_LdwsOpt_USM"] = 3
    values["CF_Lkas_FcwOpt_USM"] = 2 if enabled else 1
    # values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

  if Params().get("MfcSelect", encoding='utf8') == "2":
    # This field is LFA Mfc car ( qt ui toggle set )
    values["CF_Lkas_LdwsActivemode"] = int(left_lane) + (int(right_lane) << 1)
    values["CF_Lkas_LdwsOpt_USM"] = 2
    values["CF_Lkas_FcwOpt_USM"] = 2 if enabled else 1
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

    # ---------------------------------------------------------------------------------------
    # FcwOpt_USM 0 = No car + lanes
    #            1 = White car + lanes
    #            2 = Green car + lanes
    #            3 = Green blinking car + lanes
    #            4 = Orange car + lanes
    #            5 = Orange blinking car + lanes
    # SysWarning 4 = keep hands on wheel
    #            5 = keep hands on wheel (red)
    #            6 = keep hands on wheel (red) + beep
    # Note: the warning is hidden while the blinkers are on
    # ---------------------------------------------------------------------------------------

  dat = packer.make_can_msg("LKAS11", 0, values)[2]

  if car_fingerprint in CHECKSUM["crc8"]: # CRC Checksum
    dat = dat[:6] + dat[7:8]
    checksum = hyundai_checksum(dat)
  elif car_fingerprint in CHECKSUM["6B"]: # Checksum of first 6 Bytes
    checksum = sum(dat[:6]) % 256
  else:                                   # Checksum of first 6 Bytes and last Byte
    checksum = (sum(dat[:6]) + dat[7]) % 256
  values["CF_Lkas_Chksum"] = checksum

  return packer.make_can_msg("LKAS11", bus, values)

def create_clu11(packer, bus, clu11, button, speed):
  values = copy.copy(clu11)
  values["CF_Clu_CruiseSwState"] = button
  values["CF_Clu_Vanz"] = speed
  values["CF_Clu_AliveCnt1"] = (values["CF_Clu_AliveCnt1"] + 1) % 0x10
  return packer.make_can_msg("CLU11", bus, values)


def create_lfahda_mfc(packer, enabled, active):
  values = {
    "LFA_Icon_State": 2 if enabled else 0,
    "HDA_Active": 1 if active > 0 else 0,
    "HDA_Icon_State": 2 if active > 0 else 0,
    # "HDA_VSetReq": 0,

    # ---------------------------------------------------------------------------------------
    # LFA_Icon_State 0 = no_wheel
    #                1 = white_wheel
    #                2 = green_wheel
    #                3 = green_wheel_blink
    # LFA_SysWarning 0 = no_message
    #                1 = switching_to_hda
    #                2 = switching_to_scc
    #                3 = lfa_error
    #                4 = check_hda
    #                5 = keep_hands_on_wheel_orange
    #                6 = keep_hands_on_wheel_red
    # HDA_Icon_State 0 = no_hda
    #                1 = white_hda
    #                2 = green_hda
    # HDA_SysWarning 0 = no_message
    #                1 = driving_convenience_systems_cancelled
    #                2 = highway_drive_assist_system_cancelled
    # ---------------------------------------------------------------------------------------
  }

  return packer.make_can_msg("LFAHDA_MFC", 0, values)

def create_hda_mfc(packer, active, CS, left_lane, right_lane):
  values = copy.copy(CS.lfahda_mfc)

  ldwSysState = 0
  if left_lane:
    ldwSysState += 1
  if right_lane:
    ldwSysState += 2

  values["HDA_LdwSysState"] = ldwSysState
  values["HDA_USM"] = 2
  values["HDA_VSetReq"] = 100
  values["HDA_Icon_Wheel"] = 1 if active > 1 and CS.out.cruiseState.enabledAcc else 0
  values["HDA_Icon_State"] = 2 if active > 1 else 0
  values["HDA_Chime"] = 1 if active > 1 else 0

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

def create_scc11(packer, frame, enabled, set_speed, lead_visible, scc_live, scc11, active_cam):
  values = copy.copy(scc11)
  values["AliveCounterACC"] = frame // 2 % 0x10

  if not scc_live:
    values["MainMode_ACC"] = 1
    values["VSetDis"] = set_speed
    values["ObjValid"] = 1 if enabled else 0
    values["DriverAlertDisplay"] = 0
    # values["ACC_ObjStatus"] = lead_visible

  return packer.make_can_msg("SCC11", 0, values)


def create_scc12(packer, apply_accel, enabled, cnt, scc_live, scc12, gaspressed, brakepressed, standstill, car_fingerprint):
  values = copy.copy(scc12)
  # from xps-genesis

  if car_fingerprint in EV_HYBRID_CAR:
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


def create_acc_commands(packer, enabled, accel, jerk, idx, lead_visible, set_speed, stopping):
  commands = []

  scc11_values = {
    "MainMode_ACC": 1,
    "TauGapSet": 4,
    "VSetDis": set_speed if enabled else 0,
    "AliveCounterACC": idx % 0x10,
    "ObjValid": 1 if lead_visible else 0,
    "ACC_ObjStatus": 1 if lead_visible else 0,
    "ACC_ObjLatPos": 0,
    "ACC_ObjRelSpd": 0,
    "ACC_ObjDist": 0,
  }
  commands.append(packer.make_can_msg("SCC11", 0, scc11_values))

  scc12_values = {
    "ACCMode": 1 if enabled else 0,
    "StopReq": 1 if enabled and stopping else 0,
    "aReqRaw": accel if enabled else 0,
    "aReqValue": accel if enabled else 0, # stock ramps up and down respecting jerk limit until it reaches aReqRaw
    "CR_VSM_Alive": idx % 0xF,
  }
  scc12_dat = packer.make_can_msg("SCC12", 0, scc12_values)[2]
  scc12_values["CR_VSM_ChkSum"] = 0x10 - sum(sum(divmod(i, 16)) for i in scc12_dat) % 0x10

  commands.append(packer.make_can_msg("SCC12", 0, scc12_values))

  scc14_values = {
    "ComfortBandUpper": 0.0, # stock usually is 0 but sometimes uses higher values
    "ComfortBandLower": 0.0, # stock usually is 0 but sometimes uses higher values
    "JerkUpperLimit": max(jerk, 1.0) if (enabled and not stopping) else 0, # stock usually is 1.0 but sometimes uses higher values
    "JerkLowerLimit": max(-jerk, 1.0) if enabled else 0, # stock usually is 0.5 but sometimes uses higher values
    "ACCMode": 1 if enabled else 4, # stock will always be 4 instead of 0 after first disengage
    "ObjGap": 2 if lead_visible else 0, # 5: >30, m, 4: 25-30 m, 3: 20-25 m, 2: < 20 m, 0: no lead
  }
  commands.append(packer.make_can_msg("SCC14", 0, scc14_values))

  fca11_values = {
    # seems to count 2,1,0,3,2,1,0,3,2,1,0,3,2,1,0,repeat...
    # (where first value is aligned to Supplemental_Counter == 0)
    # test: [(idx % 0xF, -((idx % 0xF) + 2) % 4) for idx in range(0x14)]
    "CR_FCA_Alive": ((-((idx % 0xF) + 2) % 4) << 2) + 1,
    "Supplemental_Counter": idx % 0xF,
    "PAINT1_Status": 1,
    "FCA_DrvSetStatus": 1,
    "FCA_Status": 1, # AEB disabled
  }
  fca11_dat = packer.make_can_msg("FCA11", 0, fca11_values)[2]
  fca11_values["CR_FCA_ChkSum"] = 0x10 - sum(sum(divmod(i, 16)) for i in fca11_dat) % 0x10
  commands.append(packer.make_can_msg("FCA11", 0, fca11_values))

  return commands


def create_acc_opt(packer):
  commands = []

  scc13_values = {
    "SCCDrvModeRValue": 2,
    "SCC_Equip": 1,
    "Lead_Veh_Dep_Alert_USM": 2,
  }
  commands.append(packer.make_can_msg("SCC13", 0, scc13_values))

  fca12_values = {
    "FCA_DrvSetState": 2,
    "FCA_USM": 1, # AEB disabled
  }
  commands.append(packer.make_can_msg("FCA12", 0, fca12_values))

  return commands


def create_frt_radar_opt(packer):
  frt_radar11_values = {
    "CF_FCA_Equip_Front_Radar": 1,
  }
  return packer.make_can_msg("FRT_RADAR11", 0, frt_radar11_values)
