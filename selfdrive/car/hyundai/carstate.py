from collections import deque
import copy
import math

from cereal import car
from common.conversions import Conversions as CV
from opendbc.can.parser import CANParser
from opendbc.can.can_define import CANDefine
from selfdrive.car.hyundai.values import HyundaiFlags, DBC, CarControllerParams, Buttons, FEATURES, EV_CAR, HEV_CAR, CAR, CANFD_CAR, FCA11_CAR
from selfdrive.car.interfaces import CarStateBase

PREV_BUTTON_SAMPLES = 8
CLUSTER_SAMPLE_RATE = 20  # frames


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    can_define = CANDefine(DBC[CP.carFingerprint]["pt"])

    self.cruise_buttons = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)
    self.main_buttons = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)

    if CP.carFingerprint in CANFD_CAR:
      self.shifter_values = can_define.dv["GEAR_SHIFTER"]["GEAR"]
    elif self.CP.carFingerprint in FEATURES["use_cluster_gears"]:
      self.shifter_values = can_define.dv["CLU15"]["CF_Clu_Gear"]
    elif self.CP.carFingerprint in FEATURES["use_tcu_gears"]:
      self.shifter_values = can_define.dv["TCU12"]["CUR_GR"]
    else:  # preferred and elect gear methods use same definition
      self.shifter_values = can_define.dv["LVR12"]["CF_Lvr_Gear"]

    #Auto detection for setup
    self.no_radar = CP.sccBus == -1
    self.eps_bus = CP.epsBus
    self.sas_bus = CP.sasBus
    self.scc_bus = CP.sccBus
    self.has_scc13 = CP.hasScc13
    self.has_scc14 = CP.hasScc14
    self.has_lfa_hda = CP.hasLfaHda
    self.aebFcw = CP.aebFcw or CP.carFingerprint in FCA11_CAR
    self.eps_error_cnt = 0
    self.cruise_unavail_cnt = 0

    self.is_metric = False
    self.brake_error = False
    self.buttons_counter = 0

    self.cruise_info = {}

    # scc smoother
    self.acc_mode = False

    # On some cars, CLU15->CF_Clu_VehicleSpeed can oscillate faster than the dash updates. Sample at 5 Hz
    self.cluster_speed = 0
    self.cluster_speed_counter = CLUSTER_SAMPLE_RATE

    self.CCP = CarControllerParams(CP)


  def update(self, cp, cp2, cp_cam):
    if self.CP.carFingerprint in CANFD_CAR:
      return self.update_canfd(cp, cp_cam)

    cp_eps = cp2 if self.eps_bus else cp
    cp_sas = cp2 if self.sas_bus else cp
    cp_scc = cp2 if self.scc_bus == 1 else cp_cam if self.scc_bus == 2 else cp
    cp_cruise = cp

    ret = car.CarState.new_message()
    self.is_metric = cp.vl["CLU11"]["CF_Clu_SPEED_UNIT"] == 0
    self.speed_conv = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    ret.doorOpen = any([cp.vl["CGW1"]["CF_Gway_DrvDrSw"], cp.vl["CGW1"]["CF_Gway_AstDrSw"],
                        cp.vl["CGW2"]["CF_Gway_RLDrSw"], cp.vl["CGW2"]["CF_Gway_RRDrSw"]])

    ret.seatbeltUnlatched = cp.vl["CGW1"]["CF_Gway_DrvSeatBeltSw"] == 0

    ret.wheelSpeeds = self.get_wheel_speeds(cp.vl["WHL_SPD11"]["WHL_SPD_FL"], cp.vl["WHL_SPD11"]["WHL_SPD_FR"],
                                            cp.vl["WHL_SPD11"]["WHL_SPD_RL"], cp.vl["WHL_SPD11"]["WHL_SPD_RR"])

    ret.vEgoRaw = (ret.wheelSpeeds.fl + ret.wheelSpeeds.fr + ret.wheelSpeeds.rl + ret.wheelSpeeds.rr) / 4.
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = ret.vEgoRaw < 0.1

    self.cluster_speed_counter += 1
    if self.cluster_speed_counter > CLUSTER_SAMPLE_RATE:
      self.cluster_speed = cp.vl["CLU15"]["CF_Clu_VehicleSpeed"]
      self.cluster_speed_counter = 0

      # mimic how dash converts to imperial
      if not self.is_metric:
        self.cluster_speed = math.floor(self.cluster_speed * CV.KPH_TO_MPH + CV.KPH_TO_MPH)

    ret.vEgoCluster = self.cluster_speed * self.speed_conv

    ret.steeringAngleDeg = cp_sas.vl["SAS11"]["SAS_Angle"]
    ret.steeringRateDeg = cp_sas.vl["SAS11"]["SAS_Speed"]
    ret.steeringTorque = cp_eps.vl["MDPS12"]["CR_Mdps_StrColTq"]
    ret.steeringTorqueEps = cp_eps.vl["MDPS12"]["CR_Mdps_OutTq"]
    ret.steeringPressed = abs(ret.steeringTorque) > self.CCP.STEER_THRESHOLD
    ret.yawRate = cp.vl["ESP12"]["YAW_RATE"]
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(50, cp.vl["CGW1"]["CF_Gway_TurnSigLh"],
                                                                      cp.vl["CGW1"]["CF_Gway_TurnSigRh"])
    self.eps_error_cnt += 1 if not ret.standstill and cp_eps.vl["MDPS12"]["CF_Mdps_ToiUnavail"] != 0 else -self.eps_error_cnt
    ret.steerFaultTemporary = self.eps_error_cnt > 100

    if self.CP.enableAutoHold:
      ret.autoHold = cp.vl["ESP11"]["AVH_STAT"]

    # scc smoother
    self.acc_mode = cp_scc.vl["SCC12"]["ACCMode"] != 0

    # cruise state
    #if self.CP.openpilotLongitudinalControl:
      # These are not used for engage/disengage since openpilot keeps track of state using the buttons
      #ret.cruiseState.available = cp.vl["TCS13"]["ACCEnable"] == 0
      #ret.cruiseState.enabled = cp.vl["TCS13"]["ACC_REQ"] == 1
      #ret.cruiseState.standstill = False
    if self.no_radar:
      ret.cruiseState.available = cp.vl["EMS16"]["CRUISE_LAMP_M"] != 0
      ret.cruiseState.enabled = cp.vl["LVR12"]["CF_Lvr_CruiseSet"] != 0
      ret.cruiseState.standstill = False
      ret.cruiseState.speed = cp.vl["LVR12"]["CF_Lvr_CruiseSet"] * self.speed_conv if ret.cruiseState.enabled else 0
    else:
      ret.cruiseState.available = cp_scc.vl["SCC11"]["MainMode_ACC"] == 1
      ret.cruiseState.enabled = cp_scc.vl["SCC12"]["ACCMode"] != 0
      ret.cruiseState.standstill = cp_scc.vl["SCC11"]["SCCInfoDisplay"] == 4.
      ret.cruiseState.speed = cp_scc.vl["SCC11"]["VSetDis"] * self.speed_conv if ret.cruiseState.enabled else 0

    ret.cruiseGap = cp_scc.vl["SCC11"]["TauGapSet"]

    self.prev_cruise_buttons = self.cruise_buttons[-1]
    self.cruise_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwState"])
    self.main_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwMain"])

    # TODO: Find brake pressure
    ret.brake = 0
    ret.brakePressed = cp.vl["TCS13"]["DriverBraking"] != 0
    ret.brakeHoldActive = cp.vl["TCS15"]["AVH_LAMP"] == 2  # 0 OFF, 1 ERROR, 2 ACTIVE, 3 READY
    ret.parkingBrake = cp.vl["TCS13"]["PBRAKE_ACT"] == 1
    ret.brakeLights = bool(cp.vl["TCS13"]["BrakeLight"] or ret.brakePressed)

    if self.CP.carFingerprint in (EV_CAR | HEV_CAR):
      if self.CP.carFingerprint in HEV_CAR:
        ret.gas = cp.vl["E_EMS11"]["CR_Vcu_AccPedDep_Pos"] / 254.
      else:
        ret.gas = cp.vl["E_EMS11"]["Accel_Pedal_Pos"] / 254.
      ret.gasPressed = ret.gas > 0
    elif self.CP.hasEms:
      ret.gas = cp.vl["EMS12"]["PV_AV_CAN"] / 100.
      ret.gasPressed = bool(cp.vl["EMS16"]["CF_Ems_AclAct"])
    else:
      ret.gasPressed = cp.vl["TCS13"]["DriverOverride"] == 1

    # Gear Selection via Cluster - For those Kia/Hyundai which are not fully discovered, we can use the Cluster Indicator for Gear Selection,
    # as this seems to be standard over all cars, but is not the preferred method.
    if self.CP.carFingerprint in FEATURES["use_cluster_gears"]:
      gear = cp.vl["CLU15"]["CF_Clu_Gear"]
    elif self.CP.carFingerprint in FEATURES["use_tcu_gears"]:
      gear = cp.vl["TCU12"]["CUR_GR"]
    elif self.CP.carFingerprint in FEATURES["use_elect_gears"]:
      if self.CP.carFingerprint == CAR.NEXO:
        gear = cp.vl["ELECT_GEAR"]["Elect_Gear_Shifter_NEXO"]
      else:
        gear = cp.vl["ELECT_GEAR"]["Elect_Gear_Shifter"]
    else:
      gear = cp.vl["LVR12"]["CF_Lvr_Gear"]

    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))

    if not self.CP.openpilotLongitudinalControl:
      aeb_src = "FCA11" if self.CP.aebFcw else "SCC12"
      aeb_sig = "FCA_CmdAct" if self.CP.aebFcw else "AEB_CmdAct"
      aeb_warning = cp.vl[aeb_src]["CF_VSM_Warn"] != 0
      aeb_braking = cp.vl[aeb_src]["CF_VSM_DecCmdAct"] != 0 or cp.vl[aeb_src][aeb_sig] != 0
      ret.stockFcw = aeb_warning and not aeb_braking
      ret.stockAeb = aeb_warning and aeb_braking

    if self.CP.enableBsm:
      ret.leftBlindspot = cp.vl["LCA11"]["CF_Lca_IndLeft"] != 0
      ret.rightBlindspot = cp.vl["LCA11"]["CF_Lca_IndRight"] != 0

    # save the entire LKAS11, CLU11, MDPS12, LFAHDA_MFC, SCC11, SCC12, SCC13, SCC14
    self.lkas11 = cp_cam.vl["LKAS11"]
    self.clu11 = cp.vl["CLU11"]
    self.mdps12 = cp_eps.vl["MDPS12"]
    self.lfahda_mfc = cp_cam.vl["LFAHDA_MFC"]
    self.scc11 = cp_scc.vl["SCC11"]
    self.scc12 = cp_scc.vl["SCC12"]
    self.scc13 = cp_scc.vl["SCC13"] if self.has_scc13 else 0
    self.scc14 = cp_scc.vl["SCC14"] if self.has_scc14 else 0

    self.steer_state = cp_eps.vl["MDPS12"]["CF_Mdps_ToiActive"]  # 0 NOT ACTIVE, 1 ACTIVE
    self.brake_error = cp.vl["TCS13"]["ACCEnable"] != 0  # 0 ACC CONTROL ENABLED, 1-3 ACC CONTROL DISABLED
    self.cruise_unavail_cnt += 1 if cp.vl["TCS13"]["CF_VSM_Avail"] != 1 and \
                                    cp.vl["TCS13"]["ACCEnable"] != 0 else -self.cruise_unavail_cnt
    self.cruise_unavail = self.cruise_unavail_cnt > 100
    self.lead_distance = cp_scc.vl["SCC11"]["ACC_ObjDist"]

    tpms_unit = cp.vl["TPMS11"]["UNIT"] * 0.725 if int(cp.vl["TPMS11"]["UNIT"]) > 0 else 1.
    ret.tpms.fl = tpms_unit * cp.vl["TPMS11"]["PRESSURE_FL"]
    ret.tpms.fr = tpms_unit * cp.vl["TPMS11"]["PRESSURE_FR"]
    ret.tpms.rl = tpms_unit * cp.vl["TPMS11"]["PRESSURE_RL"]
    ret.tpms.rr = tpms_unit * cp.vl["TPMS11"]["PRESSURE_RR"]

    return ret


  def update_canfd(self, cp, cp_cam):
    ret = car.CarState.new_message()

    if self.CP.carFingerprint in EV_CAR:
      ret.gas = cp.vl["ACCELERATOR"]["ACCELERATOR_PEDAL"] / 255.
    elif self.CP.carFingerprint in HYBRID_CAR:
      ret.gas = cp.vl["ACCELERATOR_ALT"]["ACCELERATOR_PEDAL"] / 1023.
    ret.gasPressed = ret.gas > 1e-5
    ret.brakePressed = cp.vl["BRAKE"]["BRAKE_PRESSED"] == 1

    ret.doorOpen = cp.vl["DOORS_SEATBELTS"]["DRIVER_DOOR_OPEN"] == 1
    ret.seatbeltUnlatched = cp.vl["DOORS_SEATBELTS"]["DRIVER_SEATBELT_LATCHED"] == 0

    gear = cp.vl["GEAR_SHIFTER"]["GEAR"]
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))

    # TODO: figure out positions
    ret.wheelSpeeds = self.get_wheel_speeds(cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_1"], cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_2"],
                                            cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_3"], cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_4"])

    ret.vEgoRaw = (ret.wheelSpeeds.fl + ret.wheelSpeeds.fr + ret.wheelSpeeds.rl + ret.wheelSpeeds.rr) / 4.
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = ret.vEgoRaw < 0.1

    ret.steeringRateDeg = cp.vl["STEERING_SENSORS"]["STEERING_RATE"]
    ret.steeringAngleDeg = cp.vl["STEERING_SENSORS"]["STEERING_ANGLE"] * -1
    ret.steeringTorque = cp.vl["MDPS"]["STEERING_COL_TORQUE"]
    ret.steeringTorqueEps = cp.vl["MDPS"]["STEERING_OUT_TORQUE"]
    ret.steeringPressed = abs(ret.steeringTorque) > self.CCP.STEER_THRESHOLD
    ret.steerFaultTemporary = cp.vl["MDPS"]["LKA_FAULT"] != 0

    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(50, cp.vl["BLINKERS"]["LEFT_LAMP"],
                                                                      cp.vl["BLINKERS"]["RIGHT_LAMP"])
    ret.cruiseState.available = True
    self.is_metric = cp.vl["CLUSTER_INFO"]["DISTANCE_UNIT"] != 1
    self.speed_conv = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS
    if not self.CP.openpilotLongitudinalControl:
      speed_factor = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS
      cp_cruise_info = cp if self.CP.flags & HyundaiFlags.CANFD_HDA2 else cp_cam
      ret.cruiseState.speed = cp_cruise_info.vl["CRUISE_INFO"]["SET_SPEED"] * speed_factor
      ret.cruiseState.standstill = cp_cruise_info.vl["CRUISE_INFO"]["CRUISE_STANDSTILL"] == 1
      ret.cruiseState.enabled = cp_cruise_info.vl["CRUISE_INFO"]["CRUISE_STATUS"] != 0
      self.cruise_info = copy.copy(cp_cruise_info.vl["CRUISE_INFO"])

    cruise_btn_msg = "CRUISE_BUTTONS_ALT" if self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS else "CRUISE_BUTTONS"
    self.prev_cruise_buttons = self.cruise_buttons[-1]
    self.cruise_buttons.extend(cp.vl_all[cruise_btn_msg]["CRUISE_BUTTONS"])
    self.main_buttons.extend(cp.vl_all[cruise_btn_msg]["ADAPTIVE_CRUISE_MAIN_BTN"])
    self.buttons_counter = cp.vl[cruise_btn_msg]["COUNTER"]

    if self.CP.flags & HyundaiFlags.CANFD_HDA2:
      self.cam_0x2a4 = copy.copy(cp_cam.vl["CAM_0x2a4"])

    self.acc_mode = cp.vl["SCC1"]["CRUISE_ACTIVE"] == 1

    return ret


  @staticmethod
  def get_can_parser(CP):
    if CP.carFingerprint in CANFD_CAR:
      return CarState.get_can_parser_canfd(CP)

    signals = [
      # signal_name, signal_address
      ("WHL_SPD_FL", "WHL_SPD11"),
      ("WHL_SPD_FR", "WHL_SPD11"),
      ("WHL_SPD_RL", "WHL_SPD11"),
      ("WHL_SPD_RR", "WHL_SPD11"),

      ("YAW_RATE", "ESP12"),

      ("CF_Gway_DrvSeatBeltInd", "CGW4"),

      ("CF_Gway_DrvSeatBeltSw", "CGW1"),
      ("CF_Gway_DrvDrSw", "CGW1"),       # Driver Door
      ("CF_Gway_AstDrSw", "CGW1"),       # Passenger Door
      ("CF_Gway_RLDrSw", "CGW2"),        # Rear left Door
      ("CF_Gway_RRDrSw", "CGW2"),        # Rear right Door
      ("CF_Gway_TurnSigLh", "CGW1"),
      ("CF_Gway_TurnSigRh", "CGW1"),
      ("CF_Gway_ParkBrakeSw", "CGW1"),   # Parking Brake

      ("CYL_PRES", "ESP12"),

      ("CF_Clu_CruiseSwState", "CLU11"),
      ("CF_Clu_CruiseSwMain", "CLU11"),
      ("CF_Clu_SldMainSW", "CLU11"),
      ("CF_Clu_ParityBit1", "CLU11"),
      ("CF_Clu_VanzDecimal" , "CLU11"),
      ("CF_Clu_Vanz", "CLU11"),
      ("CF_Clu_SPEED_UNIT", "CLU11"),
      ("CF_Clu_DetentOut", "CLU11"),
      ("CF_Clu_RheostatLevel", "CLU11"),
      ("CF_Clu_CluInfo", "CLU11"),
      ("CF_Clu_AmpInfo", "CLU11"),
      ("CF_Clu_AliveCnt1", "CLU11"),

      ("CF_Clu_VehicleSpeed", "CLU15"),

      ("ACCEnable", "TCS13"),
      ("ACC_REQ", "TCS13"),
      ("BrakeLight", "TCS13"),
      ("DriverBraking", "TCS13"),
      ("StandStill", "TCS13"),
      ("PBRAKE_ACT", "TCS13"),
      ("DriverOverride", "TCS13"),
      ("CF_VSM_Avail", "TCS13"),

      ("ESC_Off_Step", "TCS15"),
      ("AVH_LAMP", "TCS15"),

      ("MainMode_ACC", "SCC11"),
      ("SCCInfoDisplay", "SCC11"),
      ("AliveCounterACC", "SCC11"),
      ("VSetDis", "SCC11"),
      ("ObjValid", "SCC11"),
      ("DriverAlertDisplay", "SCC11"),
      ("TauGapSet", "SCC11"),
      ("ACC_ObjStatus", "SCC11"),
      ("ACC_ObjLatPos", "SCC11"),
      ("ACC_ObjDist", "SCC11"),
      ("ACC_ObjRelSpd", "SCC11"),
      ("Navi_SCC_Curve_Status", "SCC11"),
      ("Navi_SCC_Curve_Act", "SCC11"),
      ("Navi_SCC_Camera_Act", "SCC11"),
      ("Navi_SCC_Camera_Status", "SCC11"),

      ("ACCMode", "SCC12"),
      ("CF_VSM_Prefill", "SCC12"),
      ("CF_VSM_DecCmdAct", "SCC12"),
      ("CF_VSM_HBACmd", "SCC12"),
      ("CF_VSM_Warn", "SCC12"),
      ("CF_VSM_Stat", "SCC12"),
      ("CF_VSM_BeltCmd", "SCC12"),
      ("ACCFailInfo", "SCC12"),
      ("StopReq", "SCC12"),
      ("CR_VSM_DecCmd", "SCC12"),
      ("aReqRaw", "SCC12"), #aReqMax
      ("TakeOverReq", "SCC12"),
      ("PreFill", "SCC12"),
      ("aReqValue", "SCC12"), #aReqMin
      ("CF_VSM_ConfMode", "SCC12"),
      ("AEB_Failinfo", "SCC12"),
      ("AEB_Status", "SCC12"),
      ("AEB_CmdAct", "SCC12"),
      ("AEB_StopReq", "SCC12"),
      ("CR_VSM_Alive", "SCC12"),
      ("CR_VSM_ChkSum", "SCC12"),

      ("SCCDrvModeRValue", "SCC13"),
      ("SCC_Equip", "SCC13"),
      ("AebDrvSetStatus", "SCC13"),

      ("JerkUpperLimit", "SCC14"),
      ("JerkLowerLimit", "SCC14"),
      ("SCCMode2", "SCC14"),
      ("ComfortBandUpper", "SCC14"),
      ("ComfortBandLower", "SCC14"),

      ("UNIT", "TPMS11"),
      ("PRESSURE_FL", "TPMS11"),
      ("PRESSURE_FR", "TPMS11"),
      ("PRESSURE_RL", "TPMS11"),
      ("PRESSURE_RR", "TPMS11"),
    ]
    checks = [
      # address, frequency
      ("TCS13", 50),
      ("TCS15", 10),
      ("CLU11", 50),
      ("CLU15", 5),
      ("ESP12", 100),
      ("CGW1", 10),
      ("CGW2", 5),
      ("CGW4", 5),
      ("WHL_SPD11", 50),
    ]

    if not CP.openpilotLongitudinalControl:
      signals += [
        ("MainMode_ACC", "SCC11"),
        ("VSetDis", "SCC11"),
        ("SCCInfoDisplay", "SCC11"),
        ("ACC_ObjDist", "SCC11"),
        ("TauGapSet", "SCC11"),
        ("ACCMode", "SCC12"),
      ]
      checks += [
        ("SCC11", 50),
        ("SCC12", 50),
      ]

      if CP.aebFcw:
        signals += [
          ("FCA_CmdAct", "FCA11"),
          ("CF_VSM_Warn", "FCA11"),
          ("CF_VSM_DecCmdAct", "FCA11"),
        ]
        checks.append(("FCA11", 50))
      else:
        signals += [
          ("AEB_CmdAct", "SCC12"),
          ("CF_VSM_Warn", "SCC12"),
          ("CF_VSM_DecCmdAct", "SCC12"),
        ]

    if CP.epsBus == 0:
      signals += [
        ("CR_Mdps_StrColTq", "MDPS12"),
        ("CF_Mdps_Def", "MDPS12"),
        ("CF_Mdps_ToiActive", "MDPS12"),
        ("CF_Mdps_ToiUnavail", "MDPS12"),
        ("CF_Mdps_ToiFlt", "MDPS12"),
        ("CF_Mdps_MsgCount2", "MDPS12"),
        ("CF_Mdps_Chksum2", "MDPS12"),
        ("CF_Mdps_SErr", "MDPS12"),
        ("CR_Mdps_StrTq", "MDPS12"),
        ("CF_Mdps_FailStat", "MDPS12"),
        ("CR_Mdps_OutTq", "MDPS12")
      ]
      checks.append(("MDPS12", 50))

    if CP.sasBus == 0:
      signals += [
        ("SAS_Angle", "SAS11"),
        ("SAS_Speed", "SAS11"),
      ]
      checks.append(("SAS11", 100))

    if CP.sccBus == -1:
      signals += [
        ("CRUISE_LAMP_M", "EMS16"),
        ("CF_Lvr_CruiseSet", "LVR12"),
      ]

    if CP.enableBsm:
      signals += [
        ("CF_Lca_IndLeft", "LCA11"),
        ("CF_Lca_IndRight", "LCA11"),
      ]
      checks.append(("LCA11", 50))

    if CP.enableAutoHold:
      signals += [
        ("AVH_STAT", "ESP11"),
        ("LDM_STAT", "ESP11"),
      ]
      checks.append(("ESP11", 50))

    if CP.carFingerprint in (EV_CAR | HEV_CAR):
      if CP.carFingerprint in HEV_CAR:
        signals.append(("CR_Vcu_AccPedDep_Pos", "E_EMS11"))
      else:
        signals.append(("Accel_Pedal_Pos", "E_EMS11"))
      checks.append(("E_EMS11", 50))
    else:
      signals += [
        ("PV_AV_CAN", "EMS12"),
        ("CF_Ems_AclAct", "EMS16"),
      ]
      checks += [
        ("EMS12", 100),
        ("EMS16", 100),
      ]

    if CP.carFingerprint in FEATURES["use_cluster_gears"]:
      signals.append(("CF_Clu_Gear", "CLU15"))
    elif CP.carFingerprint in FEATURES["use_tcu_gears"]:
      signals.append(("CUR_GR", "TCU12"))
    elif CP.carFingerprint in FEATURES["use_elect_gears"]:
      signals += [
        ("Elect_Gear_Shifter", "ELECT_GEAR"),
        ("Elect_Gear_Shifter_NEXO", "ELECT_GEAR"),
      ]
    else:
      signals.append(("CF_Lvr_Gear", "LVR12"))

    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, 0, enforce_checks=False)


  @staticmethod
  def get_can2_parser(CP):
    if CP.carFingerprint in CANFD_CAR:
      return None

    signals = []
    checks = []
    if CP.epsBus == 1:
      signals += [
        ("CR_Mdps_StrColTq", "MDPS12"),
        ("CF_Mdps_Def", "MDPS12"),
        ("CF_Mdps_ToiActive", "MDPS12"),
        ("CF_Mdps_ToiUnavail", "MDPS12"),
        ("CF_Mdps_ToiFlt", "MDPS12"),
        ("CF_Mdps_MsgCount2", "MDPS12"),
        ("CF_Mdps_Chksum2", "MDPS12"),
        ("CF_Mdps_SErr", "MDPS12"),
        ("CR_Mdps_StrTq", "MDPS12"),
        ("CF_Mdps_FailStat", "MDPS12"),
        ("CR_Mdps_OutTq", "MDPS12")
      ]
      checks.append(("MDPS12", 50))

    if CP.sasBus == 1:
      signals += [
        ("SAS_Angle", "SAS11"),
        ("SAS_Speed", "SAS11"),
      ]
      checks.append(("SAS11", 100))

    if CP.sccBus == 1:
      signals += [
        ("MainMode_ACC", "SCC11"),
        ("SCCInfoDisplay", "SCC11"),
        ("AliveCounterACC", "SCC11"),
        ("VSetDis", "SCC11"),
        ("ObjValid", "SCC11"),
        ("DriverAlertDisplay", "SCC11"),
        ("TauGapSet", "SCC11"),
        ("ACC_ObjStatus", "SCC11"),
        ("ACC_ObjLatPos", "SCC11"),
        ("ACC_ObjDist", "SCC11"),
        ("ACC_ObjRelSpd", "SCC11"),
        ("Navi_SCC_Curve_Status", "SCC11"),
        ("Navi_SCC_Curve_Act", "SCC11"),
        ("Navi_SCC_Camera_Act", "SCC11"),
        ("Navi_SCC_Camera_Status", "SCC11"),

        ("ACCMode", "SCC12"),
        ("CF_VSM_Prefill", "SCC12"),
        ("CF_VSM_DecCmdAct", "SCC12"),
        ("CF_VSM_HBACmd", "SCC12"),
        ("CF_VSM_Warn", "SCC12"),
        ("CF_VSM_Stat", "SCC12"),
        ("CF_VSM_BeltCmd", "SCC12"),
        ("ACCFailInfo", "SCC12"),
        ("StopReq", "SCC12"),
        ("CR_VSM_DecCmd", "SCC12"),
        ("aReqRaw", "SCC12"), #aReqMax
        ("TakeOverReq", "SCC12"),
        ("PreFill", "SCC12"),
        ("aReqValue", "SCC12"), #aReqMin
        ("CF_VSM_ConfMode", "SCC12"),
        ("AEB_Failinfo", "SCC12"),
        ("AEB_Status", "SCC12"),
        ("AEB_CmdAct", "SCC12"),
        ("AEB_StopReq", "SCC12"),
        ("CR_VSM_Alive", "SCC12"),
        ("CR_VSM_ChkSum", "SCC12"),

        ("SCCDrvModeRValue", "SCC13"),
        ("SCC_Equip", "SCC13"),
        ("AebDrvSetStatus", "SCC13"),

        ("JerkUpperLimit", "SCC14"),
        ("JerkLowerLimit", "SCC14"),
        ("SCCMode2", "SCC14"),
        ("ComfortBandUpper", "SCC14"),
        ("ComfortBandLower", "SCC14"),
      ]
      checks += [
        ("SCC11", 50),
        ("SCC12", 50),
      ]
    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, 1, enforce_checks=False)


  @staticmethod
  def get_cam_can_parser(CP):
    if CP.carFingerprint in CANFD_CAR:
      return CarState.get_cam_can_parser_canfd(CP)

    signals = [
      # signal_name, signal_address
      ("CF_Lkas_LdwsActivemode", "LKAS11"),
      ("CF_Lkas_LdwsSysState", "LKAS11"),
      ("CF_Lkas_SysWarning", "LKAS11"),
      ("CF_Lkas_LdwsLHWarning", "LKAS11"),
      ("CF_Lkas_LdwsRHWarning", "LKAS11"),
      ("CF_Lkas_HbaLamp", "LKAS11"),
      ("CF_Lkas_FcwBasReq", "LKAS11"),
      ("CF_Lkas_ToiFlt", "LKAS11"),
      ("CF_Lkas_HbaSysState", "LKAS11"),
      ("CF_Lkas_FcwOpt", "LKAS11"),
      ("CF_Lkas_HbaOpt", "LKAS11"),
      ("CF_Lkas_FcwSysState", "LKAS11"),
      ("CF_Lkas_FcwCollisionWarning", "LKAS11"),
      ("CF_Lkas_MsgCount", "LKAS11"),
      ("CF_Lkas_FusionState", "LKAS11"),
      ("CF_Lkas_FcwOpt_USM", "LKAS11"),
      ("CF_Lkas_LdwsOpt_USM", "LKAS11"),
    ]
    checks = [
      ("LKAS11", 100)
    ]

    if CP.sccBus == 2:
      signals += [
        ("MainMode_ACC", "SCC11"),
        ("SCCInfoDisplay", "SCC11"),
        ("AliveCounterACC", "SCC11"),
        ("VSetDis", "SCC11"),
        ("ObjValid", "SCC11"),
        ("DriverAlertDisplay", "SCC11"),
        ("TauGapSet", "SCC11"),
        ("ACC_ObjStatus", "SCC11"),
        ("ACC_ObjLatPos", "SCC11"),
        ("ACC_ObjDist", "SCC11"),
        ("ACC_ObjRelSpd", "SCC11"),
        ("Navi_SCC_Curve_Status", "SCC11"),
        ("Navi_SCC_Curve_Act", "SCC11"),
        ("Navi_SCC_Camera_Act", "SCC11"),
        ("Navi_SCC_Camera_Status", "SCC11"),

        ("ACCMode", "SCC12"),
        ("CF_VSM_Prefill", "SCC12"),
        ("CF_VSM_DecCmdAct", "SCC12"),
        ("CF_VSM_HBACmd", "SCC12"),
        ("CF_VSM_Warn", "SCC12"),
        ("CF_VSM_Stat", "SCC12"),
        ("CF_VSM_BeltCmd", "SCC12"),
        ("ACCFailInfo", "SCC12"),
        ("StopReq", "SCC12"),
        ("CR_VSM_DecCmd", "SCC12"),
        ("aReqRaw", "SCC12"), #aReqMax
        ("TakeOverReq", "SCC12"),
        ("PreFill", "SCC12"),
        ("aReqValue", "SCC12"), #aReqMin
        ("CF_VSM_ConfMode", "SCC12"),
        ("AEB_Failinfo", "SCC12"),
        ("AEB_Status", "SCC12"),
        ("AEB_CmdAct", "SCC12"),
        ("AEB_StopReq", "SCC12"),
        ("CR_VSM_Alive", "SCC12"),
        ("CR_VSM_ChkSum", "SCC12"),

        ("SCCDrvModeRValue", "SCC13"),
        ("SCC_Equip", "SCC13"),
        ("AebDrvSetStatus", "SCC13"),

        ("JerkUpperLimit", "SCC14"),
        ("JerkLowerLimit", "SCC14"),
        ("SCCMode2", "SCC14"),
        ("ComfortBandUpper", "SCC14"),
        ("ComfortBandLower", "SCC14"),
      ]
      checks += [
        ("SCC11", 50),
        ("SCC12", 50),
      ]

      if CP.hasLfaHda:
        signals += [
          ("HDA_USM", "LFAHDA_MFC"),
          ("HDA_Active", "LFAHDA_MFC"),
          ("HDA_Icon_State", "LFAHDA_MFC"),
          ("HDA_LdwSysState", "LFAHDA_MFC"),
          ("HDA_Icon_Wheel", "LFAHDA_MFC"),
        ]
        checks.append(("LFAHDA_MFC", 20))

    if not CP.openpilotLongitudinalControl:
      signals += [
        ("MainMode_ACC", "SCC11"),
        ("VSetDis", "SCC11"),
        ("SCCInfoDisplay", "SCC11"),
        ("ACC_ObjDist", "SCC11"),
        ("ACCMode", "SCC12"),
      ]
      checks += [
        ("SCC11", 50),
        ("SCC12", 50),
      ]

      if CP.aebFcw:
        signals += [
          ("FCA_CmdAct", "FCA11"),
          ("CF_VSM_Warn", "FCA11"),
          ("CF_VSM_DecCmdAct", "FCA11"),
        ]
        checks.append(("FCA11", 50))
      else:
        signals += [
          ("AEB_CmdAct", "SCC12"),
          ("CF_VSM_Warn", "SCC12"),
          ("CF_VSM_DecCmdAct", "SCC12"),
        ]

    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, 2, enforce_checks=False)


  @staticmethod
  def get_can_parser_canfd(CP):
    cruise_btn_msg = "CRUISE_BUTTONS_ALT" if CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS else "CRUISE_BUTTONS"
    signals = [
      ("WHEEL_SPEED_1", "WHEEL_SPEEDS"),
      ("WHEEL_SPEED_2", "WHEEL_SPEEDS"),
      ("WHEEL_SPEED_3", "WHEEL_SPEEDS"),
      ("WHEEL_SPEED_4", "WHEEL_SPEEDS"),

      ("GEAR", "GEAR_SHIFTER"),
      ("BRAKE_PRESSED", "BRAKE"),

      ("STEERING_RATE", "STEERING_SENSORS"),
      ("STEERING_ANGLE", "STEERING_SENSORS"),
      ("STEERING_COL_TORQUE", "MDPS"),
      ("STEERING_OUT_TORQUE", "MDPS"),
      ("LKA_FAULT", "MDPS"),

      ("CRUISE_ACTIVE", "SCC1"),
      ("COUNTER", cruise_btn_msg),
      ("CRUISE_BUTTONS", cruise_btn_msg),
      ("ADAPTIVE_CRUISE_MAIN_BTN", cruise_btn_msg),

      ("DISTANCE_UNIT", "CLUSTER_INFO"),

      ("LEFT_LAMP", "BLINKERS"),
      ("RIGHT_LAMP", "BLINKERS"),

      ("DRIVER_DOOR_OPEN", "DOORS_SEATBELTS"),
      ("DRIVER_SEATBELT_LATCHED", "DOORS_SEATBELTS"),
    ]
    checks = [
      ("WHEEL_SPEEDS", 100),
      ("GEAR_SHIFTER", 100),
      ("BRAKE", 100),
      ("STEERING_SENSORS", 100),
      ("MDPS", 100),
      ("SCC1", 50),
      (cruise_btn_msg, 50),
      ("CLUSTER_INFO", 4),
      ("BLINKERS", 4),
      ("DOORS_SEATBELTS", 4),
    ]

    if CP.flags & HyundaiFlags.CANFD_HDA2 and not CP.openpilotLongitudinalControl:
      signals += [
        ("CRUISE_STATUS", "CRUISE_INFO"),
        ("SET_SPEED", "CRUISE_INFO"),
        ("CRUISE_STANDSTILL", "CRUISE_INFO"),
      ]
      checks += [
        ("CRUISE_INFO", 50),
      ]

    if CP.carFingerprint in EV_CAR:
      signals += [
        ("ACCELERATOR_PEDAL", "ACCELERATOR"),
      ]
      checks += [
        ("ACCELERATOR", 100),
      ]
    elif CP.carFingerprint in HYBRID_CAR:
      signals += [
        ("ACCELERATOR_PEDAL", "ACCELERATOR_ALT"),
      ]
      checks += [
        ("ACCELERATOR_ALT", 100),
      ]

    bus = 5 if CP.flags & HyundaiFlags.CANFD_HDA2 else 4
    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, bus, enforce_checks=False)


  @staticmethod
  def get_cam_can_parser_canfd(CP):
    if CP.flags & HyundaiFlags.CANFD_HDA2:
      signals = [(f"BYTE{i}", "CAM_0x2a4") for i in range(3, 24)]
      checks = [("CAM_0x2a4", 20)]
    else:
      signals = [
        ("COUNTER", "CRUISE_INFO"),
        ("NEW_SIGNAL_1", "CRUISE_INFO"),
        ("CRUISE_MAIN", "CRUISE_INFO"),
        ("CRUISE_STATUS", "CRUISE_INFO"),
        ("CRUISE_INACTIVE", "CRUISE_INFO"),
        ("ZEROS_9", "CRUISE_INFO"),
        ("CRUISE_STANDSTILL", "CRUISE_INFO"),
        ("ZEROS_5", "CRUISE_INFO"),
        ("DISTANCE_SETTING", "CRUISE_INFO"),
        ("SET_SPEED", "CRUISE_INFO"),
        ("NEW_SIGNAL_4", "CRUISE_INFO"),
      ]
      checks = [
        ("CRUISE_INFO", 50),
      ]

    return CANParser(DBC[CP.carFingerprint]["pt"], signals, checks, 6, enforce_checks=False)
