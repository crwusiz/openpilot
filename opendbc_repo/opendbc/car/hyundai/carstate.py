from collections import deque
import copy
import math
import ast

from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, create_button_events, structs, DT_CTRL
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import HyundaiFlags, CAR, DBC, Buttons, CarControllerParams, HyundaiExFlags
from opendbc.car.interfaces import CarStateBase

from openpilot.selfdrive.addon.cruise_controller import CruiseStateManager
from openpilot.common.params import Params

ButtonType = structs.CarState.ButtonEvent.Type

PREV_BUTTON_SAMPLES = 8
CLUSTER_SAMPLE_RATE = 20  # frames
STANDSTILL_THRESHOLD = 12 * 0.03125

# Cancel button can sometimes be ACC pause/resume button, main button can also enable on some cars
ENABLE_BUTTONS = (Buttons.RES_ACCEL, Buttons.SET_DECEL, Buttons.CANCEL)
BUTTONS_DICT = {Buttons.RES_ACCEL: ButtonType.accelCruise, Buttons.SET_DECEL: ButtonType.decelCruise,
                Buttons.GAP_DIST: ButtonType.gapAdjustCruise, Buttons.CANCEL: ButtonType.cancel,
                Buttons.LFA_BUTTON: ButtonType.lfaButton}


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])

    self.cruise_buttons: deque = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)
    self.main_buttons: deque = deque([Buttons.NONE] * PREV_BUTTON_SAMPLES, maxlen=PREV_BUTTON_SAMPLES)
    self.lda_button = 0

    # CANFD Gear Message
    self.gear_msg_canfd = "ACCELERATOR" if CP.flags & HyundaiFlags.EV else \
                          "GEAR_ALT" if CP.flags & HyundaiFlags.CANFD_ALT_GEARS else \
                          "GEAR_ALT_2" if CP.flags & HyundaiFlags.CANFD_ALT_GEARS_2 else \
                          "GEAR_SHIFTER"
    if CP.flags & HyundaiFlags.CANFD:
      self.shifter_values = can_define.dv[self.gear_msg_canfd]["GEAR"]
    elif CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
      self.shifter_values = can_define.dv["ELECT_GEAR"]["Elect_Gear_Shifter"]
    elif self.CP.flags & HyundaiFlags.CLUSTER_GEARS:
      self.shifter_values = can_define.dv["CLU15"]["CF_Clu_Gear"]
    elif self.CP.flags & HyundaiFlags.TCU_GEARS:
      self.shifter_values = can_define.dv["TCU12"]["CUR_GR"]
    elif CP.flags & HyundaiFlags.FCEV:
      self.shifter_values = can_define.dv["EMS20"]["HYDROGEN_GEAR_SHIFTER"]
    else:
      self.shifter_values = can_define.dv["LVR12"]["CF_Lvr_Gear"]

    # Non-CANFD Gear Message Setup (Pre-calculation)
    if self.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV):
      self.gear_msg_pt, self.gear_sig_pt = "ELECT_GEAR", "Elect_Gear_Shifter"
    elif self.CP.flags & HyundaiFlags.FCEV:
      self.gear_msg_pt, self.gear_sig_pt = "EMS20", "HYDROGEN_GEAR_SHIFTER"
    elif self.CP.flags & HyundaiFlags.CLUSTER_GEARS:
      self.gear_msg_pt, self.gear_sig_pt = "CLU15", "CF_Clu_Gear"
    elif self.CP.flags & HyundaiFlags.TCU_GEARS:
      self.gear_msg_pt, self.gear_sig_pt = "TCU12", "CUR_GR"
    else:
      self.gear_msg_pt, self.gear_sig_pt = "LVR12", "CF_Lvr_Gear"

    self.accelerator_msg_canfd = "ACCELERATOR" if CP.flags & HyundaiFlags.EV else \
                                 "ACCELERATOR_ALT" if CP.flags & HyundaiFlags.HYBRID else \
                                 "ACCELERATOR_BRAKE_ALT"
    self.cruise_btns_msg_canfd = "CRUISE_BUTTONS_ALT" if CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS else \
                                 "CRUISE_BUTTONS"
    self.is_metric = False
    self.buttons_counter = 0

    self.cruise_info = {}

    self.eps_error_cnt = 0

    self.lfa_info = {}
    self.lfa_alt_info = {}
    self.lfahda_cluster_info = None
    self.mdps_info = {}
    self.hod_info = {}
    self.ccnc_msg_1b5 = {}

    self.ccnc_msg_161 = None
    self.ccnc_msg_162 = None
    self.adrv_msg_200 = None
    self.adrv_msg_1ea = None
    self.adrv_msg_160 = None
    self.navi_msg_4a3 = None
    self.msg_0x362 = None
    self.msg_0x2a4 = None
    self.tcs_info_373 = None

    self.cruise_buttons_msg = None
    self.low_speed_alert = None

    # On some cars, CLU15->CF_Clu_VehicleSpeed can oscillate faster than the dash updates. Sample at 5 Hz
    self.cluster_speed = 0
    self.cluster_speed_counter = CLUSTER_SAMPLE_RATE

    self.params = CarControllerParams(CP)

    self.lfa_enabled = False
    self.main_enabled = False

    self.canfd_buttons = None

    self.MainMode_ACC = False
    self.LFA_ICON = 0

    self.ff_distance = 0
    self.lf_distance = 0
    self.rf_distance = 0
    self.lr_distance = 0
    self.rr_distance = 0

    self.leftLnPosition = 0.0
    self.leftLnQualStat = 0
    self.rightLnPosition = 0.0
    self.rightLnQualStat = 0

    self.totalDistance = 0.0
    self.speedLimitDistance = 0

    self.DistanceGapSet = 0

    self.regen_level = 0
    self.regen_level_auto = False
    self.i_pedal_max = False
    self.i_pedel_stop = False

    cam_bus = CanBus(CP).CAM
    pt_bus = CanBus(CP).ECAN
    alt_bus = CanBus(CP).ACAN

    fingerprints_str = Params().get("FingerPrints")
    fingerprints = ast.literal_eval(fingerprints_str)

    self.NAVI_MSG_4A3 = 0x4a3 in fingerprints[pt_bus]

    self.CCNC_MSG_161 = 0x161 in fingerprints[cam_bus]
    self.CCNC_MSG_162 = 0x162 in fingerprints[cam_bus]
    self.CCNC_MSG_1B5 = 0x1b5 in fingerprints[pt_bus]
    self.ADRV_MSG_200 = 0x200 in fingerprints[cam_bus]
    self.ADRV_MSG_1EA = 0x1ea in fingerprints[cam_bus]
    self.ADRV_MSG_160 = 0x160 in fingerprints[cam_bus]
    self.LFAHDA_CLUSTER = 0x1e0 in fingerprints[cam_bus]

    self.CAM_0x362 = 0x362 in fingerprints[alt_bus]
    self.CAM_0x2a4 = 0x2a4 in fingerprints[alt_bus]

    self.controls_ready_cnt = 0

  def recent_button_interaction(self) -> bool:
    return any(btn in ENABLE_BUTTONS for btn in self.cruise_buttons) or any(self.main_buttons)

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]
    cp_alt = can_parsers[Bus.alt] if Bus.alt in can_parsers else None

    if self.controls_ready_cnt <= 100:
      self.controls_ready_cnt += 1
    elif self.controls_ready_cnt == 100:
      print("cp.seen_addresses =", cp.seen_addresses)
      print("cp_cam.seen_addresses =", cp_cam.seen_addresses)
      if cp_alt is not None:
        print("cp_alt.seen_addresses =", cp_alt.seen_addresses)

    if self.CP.flags & HyundaiFlags.CANFD:
      return self.update_canfd(can_parsers)

    ret = structs.CarState()
    cp_cruise = cp_cam if self.CP.flags & HyundaiFlags.CAMERA_SCC else cp
    self.is_metric = cp.vl["CLU11"]["CF_Clu_SPEED_UNIT"] == 0
    speed_factor = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    ret.doorOpen = any([cp.vl["CGW1"]["CF_Gway_DrvDrSw"], cp.vl["CGW1"]["CF_Gway_AstDrSw"],
                        cp.vl["CGW2"]["CF_Gway_RLDrSw"], cp.vl["CGW2"]["CF_Gway_RRDrSw"]])

    ret.seatbeltUnlatched = cp.vl["CGW1"]["CF_Gway_DrvSeatBeltSw"] == 0

    cluSpeed = cp.vl["CLU11"]["CF_Clu_Vanz"]
    decimal = cp.vl["CLU11"]["CF_Clu_VanzDecimal"]
    if 0. < decimal < 0.5:
      cluSpeed += decimal

    ret.vEgoCluster = cluSpeed * speed_factor

    self.parse_wheel_speeds(ret,
      cp.vl["WHL_SPD11"]["WHL_SPD_FL"],
      cp.vl["WHL_SPD11"]["WHL_SPD_FR"],
      cp.vl["WHL_SPD11"]["WHL_SPD_RL"],
      cp.vl["WHL_SPD11"]["WHL_SPD_RR"],
    )
    ret.standstill = cp.vl["WHL_SPD11"]["WHL_SPD_FL"] <= STANDSTILL_THRESHOLD and cp.vl["WHL_SPD11"]["WHL_SPD_RR"] <= STANDSTILL_THRESHOLD

    ret.exState.vCluRatio = (ret.vEgo / ret.vEgoClu) if (ret.vEgoClu > 3. and ret.vEgo > 3.) else 1.0

    self.cluster_speed_counter += 1
    if self.cluster_speed_counter > CLUSTER_SAMPLE_RATE:
      self.cluster_speed = cp.vl["CLU15"]["CF_Clu_VehicleSpeed"]
      self.cluster_speed_counter = 0

      if not self.is_metric:
        self.cluster_speed = math.floor(self.cluster_speed * CV.KPH_TO_MPH + CV.KPH_TO_MPH)

    ret.steeringAngleDeg = cp.vl["SAS11"]["SAS_Angle"]
    ret.steeringRateDeg = cp.vl["SAS11"]["SAS_Speed"]
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(50, cp.vl["CGW1"]["CF_Gway_TurnSigLh"],
                                                                      cp.vl["CGW1"]["CF_Gway_TurnSigRh"])
    ret.steeringTorque = cp.vl["MDPS12"]["CR_Mdps_StrColTq"]
    ret.steeringTorqueEps = cp.vl["MDPS12"]["CR_Mdps_OutTq"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > self.params.STEER_THRESHOLD, 5)
    self.eps_error_cnt += 1 if not ret.standstill and cp.vl["MDPS12"]["CF_Mdps_ToiUnavail"] != 0 else -self.eps_error_cnt
    #ret.steerFaultTemporary = cp.vl["MDPS12"]["CF_Mdps_ToiUnavail"] != 0 or cp.vl["MDPS12"]["CF_Mdps_ToiFlt"] != 0
    ret.steerFaultTemporary = self.eps_error_cnt > 100

    # cruise state
    if self.CP.openpilotLongitudinalControl:
      # These are not used for engage/disengage since openpilot keeps track of state using the buttons
      ret.cruiseState.available = cp.vl["TCS13"]["ACCEnable"] == 0
      ret.cruiseState.enabled = cp.vl["TCS13"]["ACC_REQ"] == 1
      ret.cruiseState.standstill = False
      ret.cruiseState.nonAdaptive = False
    else:
      ret.cruiseState.available = cp_cruise.vl["SCC11"]["MainMode_ACC"] == 1
      ret.cruiseState.enabled = cp_cruise.vl["SCC12"]["ACCMode"] != 0
      ret.cruiseState.standstill = cp_cruise.vl["SCC11"]["SCCInfoDisplay"] == 4.
      ret.cruiseState.nonAdaptive = cp_cruise.vl["SCC11"]["SCCInfoDisplay"] == 2.  # Shows 'Cruise Control' on dash
      ret.cruiseState.speed = cp_cruise.vl["SCC11"]["VSetDis"] * speed_factor

    ret.brakePressed = cp.vl["TCS13"]["DriverOverride"] == 2  # 2 includes regen braking by user on HEV/EV
    ret.brakeHoldActive = cp.vl["TCS15"]["AVH_LAMP"] == 2  # 0 OFF, 1 ERROR, 2 ACTIVE, 3 READY
    ret.parkingBrake = cp.vl["TCS13"]["PBRAKE_ACT"] == 1
    ret.espDisabled = cp.vl["TCS11"]["TCS_PAS"] == 1
    ret.espActive = cp.vl["TCS11"]["ABS_ACT"] == 1
    ret.accFaulted = cp.vl["TCS13"]["ACCEnable"] != 0  # 0 ACC CONTROL ENABLED, 1-3 ACC CONTROL DISABLED

    if self.CP.flags & (HyundaiFlags.HYBRID | HyundaiFlags.EV | HyundaiFlags.FCEV):
      if self.CP.flags & HyundaiFlags.FCEV:
        ret.gasPressed = cp.vl["FCEV_ACCELERATOR"]["ACCELERATOR_PEDAL"] > 0
      elif self.CP.flags & HyundaiFlags.HYBRID:
        ret.gasPressed = cp.vl["E_EMS11"]["CR_Vcu_AccPedDep_Pos"] > 0
      else:
        ret.gasPressed = cp.vl["E_EMS11"]["Accel_Pedal_Pos"] > 0
    else:
      ret.gasPressed = bool(cp.vl["EMS16"]["CF_Ems_AclAct"])

    # Gear Selection via Cluster
    gear = cp.vl[self.gear_msg_pt][self.gear_sig_pt]
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))

    if not self.CP.openpilotLongitudinalControl or self.CP.flags & HyundaiFlags.CAMERA_SCC:
      aeb_src = "FCA11" if self.CP.flags & HyundaiFlags.USE_FCA.value else "SCC12"
      aeb_sig = "FCA_CmdAct" if self.CP.flags & HyundaiFlags.USE_FCA.value else "AEB_CmdAct"
      aeb_warning = cp_cruise.vl[aeb_src]["CF_VSM_Warn"] != 0
      scc_warning = cp_cruise.vl["SCC12"]["TakeOverReq"] == 1  # sometimes only SCC system shows an FCW
      aeb_braking = cp_cruise.vl[aeb_src]["CF_VSM_DecCmdAct"] != 0 or cp_cruise.vl[aeb_src][aeb_sig] != 0
      ret.stockFcw = (aeb_warning or scc_warning) and not aeb_braking
      ret.stockAeb = aeb_warning and aeb_braking

    if self.CP.enableBsm:
      ret.leftBlindspot = cp.vl["LCA11"]["CF_Lca_IndLeft"] != 0
      ret.rightBlindspot = cp.vl["LCA11"]["CF_Lca_IndRight"] != 0

    # save the entire LKAS11, CLU11, MDPS12, LFAHDA_MFC, SCC11, SCC12, SCC13, SCC14
    self.lkas11 = copy.copy(cp_cam.vl["LKAS11"])
    self.clu11 = copy.copy(cp.vl["CLU11"])
    self.mdps12 = copy.copy(cp.vl["MDPS12"])
    self.scc11 = copy.copy(cp_cruise.vl["SCC11"]) if "SCC11" in cp_cruise.vl else None
    self.scc12 = copy.copy(cp_cruise.vl["SCC12"]) if "SCC12" in cp_cruise.vl else None
    self.scc13 = copy.copy(cp_cruise.vl["SCC13"]) if self.CP.exFlags & HyundaiExFlags.SCC13 else None
    self.scc14 = copy.copy(cp_cruise.vl["SCC14"]) if self.CP.exFlags & HyundaiExFlags.SCC14 else None

    #self.fca11 = copy.copy(cp_cruise.vl["FCA11"]) if "FCA11" in cp_cruise.vl else None
    #self.fca12 = copy.copy(cp_cruise.vl["FCA12"]) if "FCA12" in cp_cruise.vl else None

    self.steer_state = cp.vl["MDPS12"]["CF_Mdps_ToiActive"]  # 0 NOT ACTIVE, 1 ACTIVE
    prev_cruise_buttons = self.cruise_buttons[-1]
    #self.cruise_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwState"])
    prev_lda_button = self.lda_button

    if self.CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
      self.lda_button = cp.vl["BCM_PO_11"]["LDA_BTN"]
      if cp.vl["BCM_PO_11"]["LDA_BTN"]:
        cruise_button = [Buttons.LFA_BUTTON]
      else:
        cruise_button = cp.vl_all["CLU11"]["CF_Clu_CruiseSwState"]
    else:
      cruise_button = cp.vl_all["CLU11"]["CF_Clu_CruiseSwState"]
    self.cruise_buttons.extend(cruise_button)

    prev_main_buttons = self.main_buttons[-1]
    self.main_buttons.extend(cp.vl_all["CLU11"]["CF_Clu_CruiseSwMain"])

    ret.buttonEvents = [*create_button_events(self.cruise_buttons[-1], prev_cruise_buttons, BUTTONS_DICT),
                        *create_button_events(self.main_buttons[-1], prev_main_buttons, {1: ButtonType.mainCruise}),
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas})]

    ret.blockPcmEnable = not self.recent_button_interaction()
    ret.blockPcmEnable = False

    # low speed steer alert hysteresis logic (only for cars with steer cut off above 10 m/s)
    if ret.vEgo < (self.CP.minSteerSpeed + 2.) and self.CP.minSteerSpeed > 10.:
      self.low_speed_alert = True
    if ret.vEgo > (self.CP.minSteerSpeed + 4.):
      self.low_speed_alert = False
    ret.lowSpeedAlert = self.low_speed_alert

    if self.CP.exFlags & HyundaiExFlags.TPMS:
      tpms = ret.exState.tpms
      tpms_unit = cp.vl["TPMS11"]["UNIT"] * 0.725 if int(cp.vl["TPMS11"]["UNIT"]) > 0 else 1.
      tpms.fl = tpms_unit * cp.vl["TPMS11"]["PRESSURE_FL"]
      tpms.fr = tpms_unit * cp.vl["TPMS11"]["PRESSURE_FR"]
      tpms.rl = tpms_unit * cp.vl["TPMS11"]["PRESSURE_RL"]
      tpms.rr = tpms_unit * cp.vl["TPMS11"]["PRESSURE_RR"]

    if self.CP.exFlags & HyundaiExFlags.AUTOHOLD:
      ret.exState.autoHold = cp.vl["ESP11"]["AVH_STAT"]

    if self.CP.exFlags & HyundaiExFlags.NAVI:
      ret.exState.navLimitSpeed = cp.vl["Navi_HU"]["SpeedLim_Nav_Clu"]
      speedLimit = cp.vl["Navi_HU"]["SpeedLim_Nav_Clu"]
      speedLimitCam = cp.vl["Navi_HU"]["SpeedLim_Nav_Cam"]
      ret.speedLimit = speedLimit if speedLimit < 255 and speedLimitCam == 1 else 0
      speed_limit_cam = speedLimitCam == 1
    else:
      ret.speedLimit = 0
      ret.speedLimitDistance = 0
      speed_limit_cam = False

    self.update_speed_limit(ret, speed_limit_cam)

    if self.CP.openpilotLongitudinalControl and CruiseStateManager.instance().cruise_state_control:
      if self.CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
        if prev_lda_button != 1 and self.lda_button == 1:
          CruiseStateManager.instance().available = not CruiseStateManager.instance().available
      CruiseStateManager.instance().update(ret, self.main_buttons)
    else:
      if self.CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
        if prev_lda_button != 1 and self.lda_button == 1:
          self.lfa_enabled = not self.lfa_enabled
        ret.cruiseState.available = self.lfa_enabled

      if prev_main_buttons == 0 and self.main_buttons[-1] != 0:
        self.main_enabled = not self.main_enabled
        ret.cruiseState.available = self.main_enabled

    return ret

  def update_speed_limit(self, ret, speed_limit_cam):
    self.totalDistance += ret.vEgo * DT_CTRL
    if ret.speedLimit > 0 and not ret.gasPressed and speed_limit_cam:
      if self.speedLimitDistance <= self.totalDistance:
        self.speedLimitDistance = self.totalDistance + ret.speedLimit * 6
      self.speedLimitDistance = max(self.totalDistance + 1, self.speedLimitDistance)
    else:
      self.speedLimitDistance = self.totalDistance
    ret.speedLimitDistance = round(self.speedLimitDistance - self.totalDistance, 2)

  def update_canfd(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]
    cp_alt = can_parsers[Bus.alt] if Bus.alt in can_parsers else None

    ret = structs.CarState()

    self.is_metric = cp.vl["CRUISE_BUTTONS_ALT"]["DISTANCE_UNIT"] != 1
    speed_factor = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    if self.CP.flags & (HyundaiFlags.EV | HyundaiFlags.HYBRID):
      ret.gasPressed = cp.vl[self.accelerator_msg_canfd]["ACCELERATOR_PEDAL"] > 1e-5
    else:
      ret.gasPressed = bool(cp.vl[self.accelerator_msg_canfd]["ACCELERATOR_PEDAL_PRESSED"])

    ret.brakePressed = cp.vl["TCS"]["DriverBraking"] == 1

    doors = ["FL_DOOR", "FR_DOOR", "RL_DOOR", "RR_DOOR"]
    ret.doorOpen = any(cp.vl["DOORS_SEATBELTS"][door] == 1 for door in doors)

    ret.seatbeltUnlatched = cp.vl["DOORS_SEATBELTS"]["FL_SEATBELT"] == 0

    gear = cp.vl[self.gear_msg_canfd]["GEAR"]
    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(gear))

    cluSpeed = cp.vl["CRUISE_BUTTONS_ALT"]["CLUSTER_SPEED_KPH"]
    ret.vEgoCluster = cluSpeed * speed_factor

    wheel_speeds = [cp.vl["WHEEL_SPEEDS"][key] for key in ["WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal"]]
    self.parse_wheel_speeds(ret, *wheel_speeds)
    ret.standstill = all(speed <= STANDSTILL_THRESHOLD for speed in wheel_speeds)

    ret.exState.vCluRatio = (ret.vEgo / ret.vEgoClu) if (ret.vEgoClu > 3. and ret.vEgo > 3.) else 1.0

    ret.steeringRateDeg = cp.vl["STEERING_SENSORS"]["STEERING_RATE"]
    ret.steeringAngleDeg = cp.vl["STEERING_SENSORS"]["STEERING_ANGLE"]
    ret.steeringTorque = cp.vl["MDPS"]["SteerTorqueSensor"]
    ret.steeringTorqueEps = cp.vl["MDPS"]["OutTorque"]
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > self.params.STEER_THRESHOLD, 5)
    ret.steerFaultTemporary = cp.vl["MDPS"]["LKA_ToiFailStat"] or cp.vl["MDPS"]["ADAS_AciFault_Lv2"] != 0

    left_blinker_sig = cp.vl["BLINKERS"]["LEFT_LAMP"] or cp.vl["BLINKERS"]["LEFT_LAMP_ALT"]
    right_blinker_sig = cp.vl["BLINKERS"]["RIGHT_LAMP"] or cp.vl["BLINKERS"]["RIGHT_LAMP_ALT"]
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_lamp(50, left_blinker_sig, right_blinker_sig)

    if self.CP.enableBsm:
      cp_bsm_info = cp_cam if (
          self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC and self.CP.exFlags & HyundaiExFlags.CCNC_HDA2.value) else cp
      bsm_msg = cp_bsm_info.vl["BLINDSPOTS_REAR_CORNERS"]

      if self.CP.exFlags & HyundaiExFlags.CCNC_HDA2.value:
        ret.leftBlindspot = bool(bsm_msg["OSMrrLamp_LeftIndSta"])
        ret.rightBlindspot = bool(bsm_msg["OSMrrLamp_RightIndSta"])
      else:
        ret.leftBlindspot = bool(bsm_msg["BCW_LeftIndSta"])
        ret.rightBlindspot = bool(bsm_msg["BCW_RightIndSta"])

    # cruise state
    # CAN FD cars enable on main button press, set available if no TCS faults preventing engagement
    ret.cruiseState.available = cp.vl["TCS"]["ACCEnable"] == 0
    if self.CP.openpilotLongitudinalControl:
      # These are not used for engage/disengage since openpilot keeps track of state using the buttons
      ret.cruiseState.enabled = cp.vl["TCS"]["ACC_REQ"] == 1
      ret.cruiseState.standstill = False
      if self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC.value:
        self.MainMode_ACC = cp_cam.vl["SCC_CONTROL"]["MainMode_ACC"] == 1
        self.LFA_ICON = cp_cam.vl["LFAHDA_CLUSTER"]["HDA_LFA_SymSta"] if self.LFAHDA_CLUSTER else 0
    else:
      cp_cruise_info = cp_cam if self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC else cp
      ret.cruiseState.available = cp_cruise_info.vl["SCC_CONTROL"]["MainMode_ACC"] == 1
      ret.cruiseState.enabled = cp_cruise_info.vl["SCC_CONTROL"]["ACCMode"] in (1, 2)
      ret.cruiseState.standstill = cp_cruise_info.vl["SCC_CONTROL"]["InfoDisplay"] >= 4
      ret.cruiseState.speed = cp_cruise_info.vl["SCC_CONTROL"]["VSetDis"] * speed_factor

      self.cruise_info = copy.copy(cp_cruise_info.vl["SCC_CONTROL"])
      ret.brakeHoldActive = cp.vl["ESP_STATUS"]["AUTO_HOLD"] == 1 and cp_cruise_info.vl["SCC_CONTROL"]["ACCMode"] not in (1, 2)

    speed_limit_cam = False
    if self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC.value:
      self.cruise_info = copy.copy(cp_cam.vl["SCC_CONTROL"])
      self.lfa_info = copy.copy(cp_cam.vl["LFA"])
      if self.CP.flags & HyundaiFlags.CANFD_ANGLE_STEER_MSG.value:
        self.lfa_alt_info = copy.copy(cp_cam.vl["LFA_ALT"])
      self.mdps_info = copy.copy(cp.vl["MDPS"])

      if self.CP.exFlags & HyundaiExFlags.HOD:
        self.hod_info = cp.vl["HANDS_ON_DETECTION"]

    self.lfahda_cluster_info = cp_cam.vl["LFAHDA_CLUSTER"] if self.LFAHDA_CLUSTER else None
    self.DistanceGapSet = cp_cam.vl["SCC_CONTROL"]["DistanceGapSet"]

    if self.CP.exFlags & HyundaiExFlags.CCNC.value:
      corner_detected = False
      self.ccnc_msg_161 = cp_cam.vl["CCNC_0x161"] if self.CCNC_MSG_161 else None
      self.ccnc_msg_162 = cp_cam.vl["CCNC_0x162"] if self.CCNC_MSG_162 else None
      self.adrv_msg_160 = cp_cam.vl["ADRV_0x160"] if self.ADRV_MSG_160 else None
      self.adrv_msg_200 = cp_cam.vl["ADRV_0x200"] if self.ADRV_MSG_200 else None
      self.adrv_msg_1ea = cp_cam.vl["ADRV_0x1ea"] if self.ADRV_MSG_1EA else None

      if self.ccnc_msg_162 is not None:
        self.ff_distance = self.ccnc_msg_162["FF_DETECT_DISTANCE"]
        ret.leftLongDist = self.lf_distance = self.ccnc_msg_162["LF_DETECT_DISTANCE"]
        ret.rightLongDist = self.rf_distance = self.ccnc_msg_162["RF_DETECT_DISTANCE"]
        self.lr_distance = self.ccnc_msg_162["LR_DETECT_DISTANCE"]
        self.rr_distance = self.ccnc_msg_162["RR_DETECT_DISTANCE"]
        ret.leftLatDist = self.ccnc_msg_162["LF_DETECT_LATERAL"]
        ret.rightLatDist = self.ccnc_msg_162["RF_DETECT_LATERAL"]
        corner_detected = True

      if self.adrv_msg_1ea is not None:
        if not corner_detected:
          ret.leftLongDist = self.adrv_msg_1ea["LF_DETECT_DISTANCE"]
          ret.rightLongDist = self.adrv_msg_1ea["RF_DETECT_DISTANCE"]
          self.lr_distance = self.adrv_msg_1ea["LR_DETECT_DISTANCE"]
          self.rr_distance = self.adrv_msg_1ea["RR_DETECT_DISTANCE"]
          ret.leftLatDist = self.adrv_msg_1ea["LF_DETECT_LATERAL"]
          ret.rightLatDist = self.adrv_msg_1ea["RF_DETECT_LATERAL"]
          corner_detected = True
      if corner_detected:
        left_block = True if 0 < ret.leftLongDist < 7.0 or 0 < self.lr_distance < 7.0 else False
        right_block = True if 0 < ret.rightLongDist < 7.0 or 0 < self.rr_distance < 7.0 else False
        if left_block:
          ret.leftBlindspot = True
        if right_block:
          ret.rightBlindspot = True
      self.navi_msg_4a3 = cp.vl["Hud_Navi_ISLW_PE"] if self.NAVI_MSG_4A3 else None
      if self.CCNC_MSG_1B5:
        self.ccnc_msg_1b5 = cp.vl["CCNC_0x1b5"]
        self.leftLnPosition = self.ccnc_msg_1b5["LeftLnPosition"]
        self.leftLnQualStat = self.ccnc_msg_1b5["LeftLnQualStat"]
        self.rightLnPosition = self.ccnc_msg_1b5["RightLnPosition"]
        self.rightLnQualStat = self.ccnc_msg_1b5["RightLnQualStat"]

      self.tcs_info_373 = cp.vl["TCS"]

      if cp_alt and self.CP.flags & HyundaiFlags.CAMERA_SCC:
        lane_info = None
        if self.CAM_0x362:
          lane_info = cp_alt.vl["CAM_0x362"]
        elif self.CAM_0x2a4:
          lane_info = cp_alt.vl["CAM_0x2a4"]

        if lane_info is not None:
          left_lane_prob = lane_info["LEFT_LANE_PROB"]
          right_lane_prob = lane_info["RIGHT_LANE_PROB"]
          left_lane_type = lane_info["LEFT_LANE_TYPE"]
          # 0: dashed, 1: solid, 2: undecided, 3: road edge, 4: DLM Inner Solid, 5: DLM InnerDashed, 6:DLM Inner Undecided, 7: Botts Dots, 8: Barrier
          right_lane_type = lane_info["RIGHT_LANE_TYPE"]
          left_lane_color = lane_info["LEFT_LANE_COLOR"]
          right_lane_color = lane_info["RIGHT_LANE_COLOR"]
          left_lane_info = left_lane_color * 10 + left_lane_type
          right_lane_info = right_lane_color * 10 + right_lane_type
          ret.leftLaneLine = left_lane_info
          ret.rightLaneLine = right_lane_info

      if self.NAVI_MSG_4A3:
        speedLimit = self.navi_msg_4a3["SpeedLimit"]
        ret.speedLimit = speedLimit if speedLimit < 255 else 0
        if int(self.navi_msg_4a3["MapSource"]) == 2:
          speed_limit_cam = True
        self.update_speed_limit(ret, speed_limit_cam)

    if self.CP.flags & HyundaiFlags.EV:
      ret.cruiseState.nonAdaptive = cp.vl["MANUAL_SPEED_LIMIT_ASSIST"]["MSLA_ENABLED"] == 1
      self.regen_level = cp.vl["MANUAL_SPEED_LIMIT_ASSIST"]["REGEN_LEVEL"]
      self.regen_level_auto = cp.vl["MANUAL_SPEED_LIMIT_ASSIST"]["REGEN_LEVEL_AUTO"] == 1
      self.i_pedal_max = cp.vl["MANUAL_SPEED_LIMIT_ASSIST"]["I_PEDAL_MAX"] == 1
      self.i_pedel_stop = cp.vl["MANUAL_SPEED_LIMIT_ASSIST"]["I_PEDAL_STOP"] == 1

    prev_cruise_buttons = self.cruise_buttons[-1]
    #self.cruise_buttons.extend(cp.vl_all[self.cruise_btns_msg_canfd]["CRUISE_BUTTONS"])
    prev_lda_button = self.lda_button

    if cp.vl[self.cruise_btns_msg_canfd]["LDA_BTN"]:
      cruise_button = [Buttons.LFA_BUTTON]
    else:
      cruise_button = cp.vl_all[self.cruise_btns_msg_canfd]["CRUISE_BUTTONS"]
    self.cruise_buttons.extend(cruise_button)

    if self.cruise_btns_msg_canfd in cp.vl:
      self.cruise_buttons_msg = copy.copy(cp.vl[self.cruise_btns_msg_canfd])

    prev_main_buttons = self.main_buttons[-1]
    self.main_buttons.extend(cp.vl_all[self.cruise_btns_msg_canfd]["ADAPTIVE_CRUISE_MAIN_BTN"])
    self.lda_button = cp.vl[self.cruise_btns_msg_canfd]["LDA_BTN"]

    self.buttons_counter = cp.vl[self.cruise_btns_msg_canfd]["COUNTER"]
    ret.accFaulted = cp.vl["TCS"]["ACCEnable"] != 0  # 0 ACC CONTROL ENABLED, 1-3 ACC CONTROL DISABLED

    if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG and not self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC:
      if self.msg_0x362 is not None or 0x362 in cp_cam.seen_addresses:
        self.msg_0x362 = cp_cam.vl["CAM_0x362"]
      elif self.msg_0x2a4 is not None or 0x2a4 in cp_cam.seen_addresses:
        self.msg_0x2a4 = cp_cam.vl["CAM_0x2a4"]

    ret.buttonEvents = [*create_button_events(self.cruise_buttons[-1], prev_cruise_buttons, BUTTONS_DICT),
                        *create_button_events(self.main_buttons[-1], prev_main_buttons, {1: ButtonType.mainCruise}),
                        *create_button_events(self.lda_button, prev_lda_button, {1: ButtonType.lkas})]

    ret.blockPcmEnable = not self.recent_button_interaction()
    ret.blockPcmEnable = False

    if self.CP.exFlags & HyundaiExFlags.TPMS:
      tpms = ret.exState.tpms
      tpms_unit = cp.vl["TPMS"]["UNIT"] * 0.725 if int(cp.vl["TPMS"]["UNIT"]) > 0 else 1.
      tpms.fl = tpms_unit * cp.vl["TPMS"]["PRESSURE_FL"]
      tpms.fr = tpms_unit * cp.vl["TPMS"]["PRESSURE_FR"]
      tpms.rl = tpms_unit * cp.vl["TPMS"]["PRESSURE_RL"]
      tpms.rr = tpms_unit * cp.vl["TPMS"]["PRESSURE_RR"]

    if self.CP.exFlags & HyundaiExFlags.AUTOHOLD:
      ret.exState.autoHold = cp.vl["ESP_STATUS"]["AUTO_HOLD"]

    if self.CP.exFlags & HyundaiExFlags.NAVI:
      if self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC and self.CP.exFlags & HyundaiExFlags.CCNC.value:
        ret.exState.roadSigns = cp_cam.vl["CCNC_0x162"]["ROAD_SIGNS"]
        ret.exState.navLimitSpeed = cp_cam.vl["CCNC_0x162"]["SPEEDLIMIT"]
      else:
        ret.exState.roadSigns = cp.vl["CLUSTER_SPEED_LIMIT"]["ROAD_SIGNS"]
        ret.exState.navLimitSpeed = cp.vl["CLUSTER_SPEED_LIMIT"]["NavSpeedLimit"]

    self.canfd_buttons = cp.vl[self.cruise_btns_msg_canfd]

    if self.CP.openpilotLongitudinalControl and CruiseStateManager.instance().cruise_state_control:
      if self.CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
        if prev_lda_button != 1 and self.lda_button == 1:
          CruiseStateManager.instance().available = not CruiseStateManager.instance().available
      CruiseStateManager.instance().update(ret, self.main_buttons)
    else:
      if self.CP.flags & HyundaiFlags.HAS_LDA_BUTTON:
        if prev_lda_button != 1 and self.lda_button == 1:
          self.lfa_enabled = not self.lfa_enabled
        ret.cruiseState.available = self.lfa_enabled

      if self.main_buttons[-1] != prev_main_buttons and not self.main_buttons[-1]:
        self.main_enabled = not self.main_enabled
        ret.cruiseState.available = self.main_enabled

    return ret

  def get_can_parsers_canfd(self, CP):
    msgs = []
    if not CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
      # TODO: this can be removed once we add dynamic support to vl_all
      msgs += [
        # this message is 50Hz but the ECU frequently stops transmitting for ~0.5s
        ("CRUISE_BUTTONS", 1)
      ]

    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], msgs, CanBus(CP).ECAN),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], CanBus(CP).CAM),
      Bus.alt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], CanBus(CP).ACAN),
    }

  def get_can_parsers(self, CP):
    if CP.flags & HyundaiFlags.CANFD:
      return self.get_can_parsers_canfd(CP)

    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }
