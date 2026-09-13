from opendbc.car import Bus, get_safety_config, structs, uds
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import HyundaiFlags, CAR, DBC, HyundaiExFlags, HyundaiSafetyFlags
from opendbc.car.hyundai.radar_interface import RADAR_START_ADDR
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.disable_ecu import disable_ecu
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.radar_interface import RadarInterface

import copy
from openpilot.common.params import Params

ButtonType = structs.CarState.ButtonEvent.Type
Ecu = structs.CarParams.Ecu

# Cancel button can sometimes be ACC pause/resume button, main button can also enable on some cars
ENABLE_BUTTONS = (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.cancel, ButtonType.mainCruise, ButtonType.lfaButton)


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  DRIVABLE_GEARS = (structs.CarState.GearShifter.sport, structs.CarState.GearShifter.manumatic)

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "hyundai"

    camera_scc = Params().get_bool("CameraSccEnable")

    if camera_scc:
      ret.flags |= HyundaiFlags.CAMERA_SCC.value

    if ret.flags & HyundaiFlags.CANFD:
      # "LKA steering" if LKAS or LKAS_ALT messages are seen coming from the camera.
      # Generally means our LKAS message is forwarded to another ECU (commonly ADAS ECU)
      # that finally retransmits our steering command in LFA or LFA_ALT to the MDPS.
      # "LFA steering" if camera directly sends LFA to the MDPS
      cam_can = CanBus(None, fingerprint).CAM if camera_scc == 0 else 1
      lka_steering = any((0x50 in fingerprint[cam_can], 0x110 in fingerprint[cam_can], Params().get_bool("IsHda2")))
      CAN = CanBus(None, fingerprint, lka_steering)

      #ret.enableBsm = 0x1e5 in fingerprint[CAN.ECAN]
      ret.enableBsm = 0x1ba in fingerprint[CAN.ECAN]

      if 0x60 in fingerprint[CAN.ECAN]:
        ret.exFlags |= HyundaiExFlags.AUTOHOLD.value
      if 0x3a0 in fingerprint[CAN.ECAN]:
        ret.exFlags |= HyundaiExFlags.TPMS.value
      if 0x1fa in fingerprint[CAN.ECAN]:
        ret.exFlags |= HyundaiExFlags.NAVI.value
      if {0x1AA, 0x1CF} & set(fingerprint[CAN.ECAN]):
        ret.flags |= HyundaiFlags.HAS_LDA_BUTTON.value

      # Check if the car is hybrid. Only HEV/PHEV cars have 0xFA on E-CAN.
      if 0xFA in fingerprint[CAN.ECAN]:
        ret.flags |= HyundaiFlags.HYBRID.value

      if lka_steering:
        # detect LKA steering
        ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG.value
        if camera_scc:
          if 0x110 in fingerprint[CAN.ACAN]:
            ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG_ALT.value
        else:
          if 0x110 in fingerprint[CAN.CAM]: # 0x110(272): LKAS_ALT
            ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG_ALT.value
          if 0x2a4 not in fingerprint[CAN.CAM]: # 0x2a4(676): CAM_0x2a4
            ret.flags |= HyundaiFlags.CANFD_LKA_STEER_MSG_ALT.value
      else:
        # no LKA steering
        if not ret.flags & HyundaiFlags.CANFD_RADAR_SCC:
          ret.flags |= HyundaiFlags.CANFD_CAMERA_SCC.value

      if 0x1cf not in fingerprint[CAN.ECAN]:
        ret.flags |= HyundaiFlags.CANFD_ALT_BUTTONS.value
      if 0x161 in fingerprint[CAN.ECAN]:
        ret.exFlags |= HyundaiExFlags.CCNC.value
        if lka_steering:
          ret.exFlags |= HyundaiExFlags.CCNC_HDA2.value
        if 0x2af in fingerprint[CAN.ECAN]:
          ret.exFlags |= HyundaiExFlags.HOD.value
        if 0x4a3 in fingerprint[CAN.ECAN]:
          ret.exFlags |= HyundaiExFlags.MSG_4A3.value

      if 0xCB in fingerprint[CAN.CAM]: # LFA_ALT
        ret.flags |= HyundaiFlags.CANFD_ANGLE_STEER_MSG.value

      if 0x210 in fingerprint[CAN.ACAN]:
        ret.extFlags |= HyundaiExFlags.RADAR_GROUP1.value

      if 0x105 in fingerprint[CAN.ECAN]:
        ret.flags |= HyundaiFlags.HYBRID.value

      if 0x362 in fingerprint[CAN.ACAN]:
        ret.exFlags |= HyundaiExFlags.ACAN.value

      if 0x210 in fingerprint[CAN.ACAN]:
        ret.exFlags |= HyundaiExFlags.RADAR_GROUP1.value

      # Some LKA steering cars have alternative messages for gear checks
      # ICE cars do not have 0x130; GEARS message on 0x40 or 0x70 instead
      if 0x130 not in fingerprint[CAN.ECAN]:
        if 0x40 not in fingerprint[CAN.ECAN]:
          ret.flags |= HyundaiFlags.CANFD_ALT_GEARS_2.value
        else:
          ret.flags |= HyundaiFlags.CANFD_ALT_GEARS.value

      cfgs = [get_safety_config(structs.CarParams.SafetyModel.hyundaiCanfd), ]
      if CAN.ECAN >= 4:
        cfgs.insert(0, get_safety_config(structs.CarParams.SafetyModel.noOutput))
      ret.safetyConfigs = cfgs

      if ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_LKA_STEER_MSG.value
        if ret.flags & HyundaiFlags.CANFD_LKA_STEER_MSG_ALT:
          ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_LKA_STEER_MSG_ALT.value
      if ret.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_ALT_BUTTONS.value
      if ret.flags & HyundaiFlags.CANFD_CAMERA_SCC:
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CAMERA_SCC.value
      if ret.flags & HyundaiFlags.CANFD_ANGLE_STEER_MSG:
        ret.steerControlType = structs.CarParams.SteerControlType.angle
        ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.CANFD_ANGLE_STEER_MSG.value

    else:
      # Shared configuration for non CAN-FD cars
      ret.enableBsm = 0x58b in fingerprint[0]

      if 0x47f in fingerprint[0]:
        ret.exFlags |= HyundaiExFlags.AUTOHOLD.value
      if 0x593 in fingerprint[0]:
        ret.exFlags |= HyundaiExFlags.TPMS.value
      if 0x544 in fingerprint[0]:
        ret.exFlags |= HyundaiExFlags.NAVI.value

      if camera_scc:
        if any(0x50a in fingerprint[i] for i in [0, 2]):
          ret.exFlags |= HyundaiExFlags.SCC13.value
        if any(0x389 in fingerprint[i] for i in [0, 2]):
          ret.exFlags|=  HyundaiExFlags.SCC14.value

      # Send LFA message on cars with HDA
      if 0x485 in fingerprint[2]:
        ret.flags |= HyundaiFlags.SEND_LFA.value

      # These cars use the FCA11 message for the AEB and FCW signals, all others use SCC12
      if 0x38d in fingerprint[0] or 0x38d in fingerprint[2]:
        ret.flags |= HyundaiFlags.USE_FCA.value
      #if 0x483 in fingerprint[0] or 0x483 in fingerprint[2]:
      #  ret.flags |= HyundaiFlags.SEND_FCA12.value

      if ret.flags & HyundaiFlags.LEGACY:
        # these cars require a special panda safety mode due to missing counters and checksums in the messages
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.hyundaiLegacy)]
      else:
        ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.hyundai, 0)]

      if ret.flags & HyundaiFlags.CAMERA_SCC:
        ret.safetyConfigs[0].safetyParam |= HyundaiSafetyFlags.CAMERA_SCC.value

      # These cars have the LFA button on the steering wheel
      if 0x391 in fingerprint[0]:
        ret.flags |= HyundaiFlags.HAS_LDA_BUTTON.value

    # Common lateral control setup

    ret.centerToFront = ret.wheelbase * 0.4
    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.4

    if not ret.flags & HyundaiFlags.CANFD_ANGLE_STEER_MSG:
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    if ret.flags & HyundaiFlags.ALT_LIMITS:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.ALT_LIMITS.value

    if ret.flags & HyundaiFlags.ALT_LIMITS_2:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.ALT_LIMITS_2.value

    # Common longitudinal control setup

    ret.alphaLongitudinalAvailable = True  # candidate not in (CANFD_UNSUPPORTED_LONGITUDINAL_CAR | CANFD_CANFD_RADAR_SCC_CAR)
    ret.pcmCruise = Params().get_bool("PcmCruiseEnable")

    ret.radarUnavailable = RADAR_START_ADDR not in fingerprint[1] or Bus.radar not in DBC[ret.carFingerprint]
    ret.openpilotLongitudinalControl = (alpha_long and ret.alphaLongitudinalAvailable) or camera_scc
    ret.longitudinalActuatorDelay = 0.5

    if ret.openpilotLongitudinalControl:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.LONG.value
    if ret.flags & HyundaiFlags.HYBRID:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.HYBRID_GAS.value
    elif ret.flags & HyundaiFlags.EV:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.EV_GAS.value
    elif ret.flags & HyundaiFlags.FCEV:
      ret.safetyConfigs[-1].safetyParam |= HyundaiSafetyFlags.FCEV_GAS.value

    return ret


  @staticmethod
  def init(CP, can_recv, can_send, communication_control=None):
    # 0x80 silences response
    if communication_control is None:
      communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, 0x80 | uds.CONTROL_TYPE.DISABLE_RX_DISABLE_TX, uds.MESSAGE_TYPE.NORMAL])
    radar_track = Params().get_bool("RadarTrackEnable")
    if all([CP.openpilotLongitudinalControl, radar_track]):
      addr, bus = 0x7d0, CanBus(CP).ECAN if CP.flags & HyundaiFlags.CANFD else 0
      if CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG.value:
        addr, bus = 0x730, CanBus(CP).ECAN
      disable_ecu(can_recv, can_send, bus=bus, addr=addr, com_cont_req=communication_control)

    # for blinkers
    if CP.flags & HyundaiFlags.CANFD_ENABLE_BLINKERS:
      disable_ecu(can_recv, can_send, bus=CanBus(CP).ECAN, addr=0x7B1, com_cont_req=communication_control)

  @staticmethod
  def deinit(CP, can_recv, can_send):
    communication_control = bytes([uds.SERVICE_TYPE.COMMUNICATION_CONTROL, 0x80 | uds.CONTROL_TYPE.ENABLE_RX_ENABLE_TX, uds.MESSAGE_TYPE.NORMAL])
    CarInterface.init(CP, can_recv, can_send, communication_control)

  def create_buttons(self, button):
    if self.CP.flags & HyundaiFlags.CANFD:
      if self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
        return self.create_buttons_canfd_alt(button)
      return self.create_buttons_canfd(button)
    else:
      return self.create_buttons_can(button)

  def create_buttons_can(self, button):
    values = copy.copy(self.CS.clu11)
    values["CF_Clu_CruiseSwState"] = button
    values["CF_Clu_AliveCnt1"] = (values["CF_Clu_AliveCnt1"] + 1) % 0x10
    bus = 2 if self.CP.flags & HyundaiFlags.CAMERA_SCC else 0
    return self.CC.packer.make_can_msg("CLU11", bus, values)

  def create_buttons_canfd(self, button):
    values = {
      "COUNTER": self.CS.buttons_counter + 1,
      "SET_ME_1": 1,
      "CRUISE_BUTTONS": button,
    }
    bus = self.CC.CAN.ECAN if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else self.CC.CAN.CAM
    return self.CC.packer.make_can_msg("CRUISE_BUTTONS", bus, values)

  def create_buttons_canfd_alt(self, button):
    values = copy.copy(self.CS.canfd_buttons)
    values["CRUISE_BUTTONS"] = button
    values["COUNTER"] = (values["COUNTER"] + 1) % 256
    bus = self.CC.CAN.ECAN if self.CP.flags & HyundaiFlags.CANFD_LKA_STEER_MSG else self.CC.CAN.CAM
    return self.CC.packer.make_can_msg("CRUISE_BUTTONS_ALT", bus, values)

