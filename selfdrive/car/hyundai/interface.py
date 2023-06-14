#!/usr/bin/env python3
from cereal import car
from panda import Panda
from common.params import Params
from common.conversions import Conversions as CV
from selfdrive.car.hyundai.hyundaicanfd import CanBus
from selfdrive.car.hyundai.values import HyundaiFlags, CAR, DBC, Buttons, CANFD_CAR, CANFD_EV_CAR, CANFD_HEV_CAR, EV_CAR, HEV_CAR, LEGACY_SAFETY_MODE_CAR, CANFD_RADAR_SCC_CAR
from selfdrive.car.hyundai.radar_interface import RADAR_START_ADDR
from selfdrive.car import STD_CARGO_KG, create_button_event, scale_tire_stiffness, get_safety_config
from selfdrive.car.interfaces import CarInterfaceBase
from selfdrive.controls.lib.desire_helper import LANE_CHANGE_SPEED_MIN
from selfdrive.car.disable_ecu import disable_ecu

Ecu = car.CarParams.Ecu
ButtonType = car.CarState.ButtonEvent.Type
EventName = car.CarEvent.EventName
ENABLE_BUTTONS = (Buttons.RES_ACCEL, Buttons.SET_DECEL, Buttons.CANCEL)
BUTTONS_DICT = {Buttons.RES_ACCEL: ButtonType.accelCruise, Buttons.SET_DECEL: ButtonType.decelCruise,
                Buttons.GAP_DIST: ButtonType.gapAdjustCruise, Buttons.CANCEL: ButtonType.cancel}

class CarInterface(CarInterfaceBase):
  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, experimental_long, docs):
    ret.carName = "hyundai"

    hda2 = Ecu.adas in [fw.ecu for fw in car_fw]
    CAN = CanBus(None, hda2, fingerprint)

    if candidate in CANFD_CAR:
      # detect HDA2 with ADAS Driving ECU
      if hda2:
        ret.flags |= HyundaiFlags.CANFD_HDA2.value
      else:
        # non-HDA2
        if 0x1cf not in fingerprint[CAN.ECAN]: # 463
          ret.flags |= HyundaiFlags.CANFD_ALT_BUTTONS.value
        # ICE cars do not have 0x130; GEARS message on 0x40 or 0x70 instead
        if 0x130 not in fingerprint[CAN.ECAN]: # 304
          if 0x40 not in fingerprint[CAN.ECAN]: # 64
            ret.flags |= HyundaiFlags.CANFD_ALT_GEARS_2.value
          else:
            ret.flags |= HyundaiFlags.CANFD_ALT_GEARS.value
        if candidate not in CANFD_RADAR_SCC_CAR:
          ret.flags |= HyundaiFlags.CANFD_CAMERA_SCC.value
    else:
      # Send LFA message on cars with HDA
      if 0x485 in fingerprint[2]: # 1157
        ret.flags |= HyundaiFlags.SEND_LFA.value

      # these cars use the FCA11 message for the AEB and FCW signals, all others use SCC12
      if 0x38d in fingerprint[0] or 0x38d in fingerprint[2]: # 909
        ret.flags |= HyundaiFlags.USE_FCA.value

    ret.steerActuatorDelay = 0.1
    ret.steerLimitTimer = 0.4

    tire_stiffness_factor = 1.

    # STD_CARGO_KG=136. wheelbase or mass date using wikipedia
    # hyundai
    if candidate == CAR.ELANTRA_I30:
      ret.mass = 1340. + STD_CARGO_KG
      ret.wheelbase = 2.72
      ret.steerRatio = 15.4
      tire_stiffness_factor = 0.385
    elif candidate in [CAR.ELANTRA_CN7, CAR.ELANTRA_CN7_HEV]:
      ret.mass = 1340. + STD_CARGO_KG
      ret.wheelbase = 2.72
      ret.steerRatio = 12.9
      tire_stiffness_factor = 0.65
    elif candidate in [CAR.SONATA_DN8, CAR.SONATA_DN8_HEV]:
      ret.mass = 1615. + STD_CARGO_KG
      ret.wheelbase = 2.84
      ret.steerRatio = 15.2
      tire_stiffness_factor = 0.65
    elif candidate in [CAR.SONATA_LF, CAR.SONATA_LF_HEV]:
      ret.mass = 1640. + STD_CARGO_KG
      ret.wheelbase = 2.80
      ret.steerRatio = 15.2
    elif candidate in [CAR.KONA, CAR.KONA_EV, CAR.KONA_HEV]:
      ret.mass = 1743. + STD_CARGO_KG
      ret.wheelbase = 2.60
      ret.steerRatio = 13.7
      tire_stiffness_factor = 0.385
    elif candidate in [CAR.IONIQ, CAR.IONIQ_EV, CAR.IONIQ_HEV]:
      ret.mass = 1575. + STD_CARGO_KG
      ret.wheelbase = 2.70
      ret.steerRatio = 13.7
      tire_stiffness_factor = 0.385
    elif candidate == CAR.IONIQ5:
      ret.mass = 2012 + STD_CARGO_KG
      ret.wheelbase = 3.0
      ret.steerRatio = 16.
      tire_stiffness_factor = 0.65
    elif candidate == CAR.TUCSON:
      ret.mass = 1596. + STD_CARGO_KG
      ret.wheelbase = 2.67
      ret.steerRatio = 16.0
      tire_stiffness_factor = 0.385
    elif candidate in [CAR.TUCSON_NX4, CAR.TUCSON_NX4_HEV]:
      ret.mass = 1680. + STD_CARGO_KG
      ret.wheelbase = 2.756
      ret.steerRatio = 16.
      tire_stiffness_factor = 0.385
    elif candidate in [CAR.SANTAFE, CAR.SANTAFE_HEV]:
      ret.mass = 1910. + STD_CARGO_KG
      ret.wheelbase = 2.76
      ret.steerRatio = 15.8
      tire_stiffness_factor = 0.82
    elif candidate == CAR.PALISADE:
      ret.mass = 2060. + STD_CARGO_KG
      ret.wheelbase = 2.90
      ret.steerRatio = 15.8
      tire_stiffness_factor = 0.63
    elif candidate == CAR.VELOSTER:
      ret.mass = 1350. + STD_CARGO_KG
      ret.wheelbase = 2.65
      ret.steerRatio = 15.8
      tire_stiffness_factor = 0.5
    elif candidate in [CAR.GRANDEUR_IG, CAR.GRANDEUR_IG_HEV, CAR.GRANDEUR_IGFL, CAR.GRANDEUR_IGFL_HEV]:
      ret.mass = 1719. + STD_CARGO_KG
      ret.wheelbase = 2.88
      ret.steerRatio = 12.5
    elif candidate == CAR.NEXO:
      ret.mass = 1873. + STD_CARGO_KG
      ret.wheelbase = 2.79
      ret.steerRatio = 13.7

    # kia
    elif candidate == CAR.FORTE:
      ret.mass = 1345. + STD_CARGO_KG
      ret.wheelbase = 2.70
      ret.steerRatio = 13.7
      tire_stiffness_factor = 0.5
    elif candidate in [CAR.K5, CAR.K5_HEV, CAR.K5_DL3, CAR.K5_DL3_HEV]:
      ret.mass = 1565. + STD_CARGO_KG
      ret.wheelbase = 2.80
      ret.steerRatio = 15.2
      tire_stiffness_factor = 0.5
    elif candidate in [CAR.K7, CAR.K7_HEV]:
      ret.mass = 1730. + STD_CARGO_KG
      ret.wheelbase = 2.85
      ret.steerRatio = 12.5
    elif candidate == CAR.K9:
      ret.mass = 2005. + STD_CARGO_KG
      ret.wheelbase = 3.15
      ret.steerRatio = 16.5
    elif candidate == CAR.SPORTAGE:
      ret.mass = 1770. + STD_CARGO_KG
      ret.wheelbase = 2.67
      ret.steerRatio = 15.8
    elif candidate == CAR.SORENTO:
      ret.mass = 1885. + STD_CARGO_KG
      ret.wheelbase = 2.81
      ret.steerRatio = 15.8
    elif candidate == CAR.MOHAVE:
      ret.mass = 2305. + STD_CARGO_KG
      ret.wheelbase = 2.89
      ret.steerRatio = 14.1
    elif candidate == CAR.STINGER:
      ret.mass = 1913. + STD_CARGO_KG
      ret.wheelbase = 2.90
      ret.steerRatio = 13.5
    elif candidate in [CAR.NIRO_EV, CAR.NIRO_HEV]:
      ret.mass = 1748. + STD_CARGO_KG
      ret.wheelbase = 2.70
      ret.steerRatio = 13.7
      tire_stiffness_factor = 0.385
    elif candidate == CAR.SOUL_EV:
      ret.mass = 1375. + STD_CARGO_KG
      ret.wheelbase = 2.60
      ret.steerRatio = 13.7
      tire_stiffness_factor = 0.385
    elif candidate == CAR.SELTOS:
      ret.mass = 1510. + STD_CARGO_KG
      ret.wheelbase = 2.63
      ret.steerRatio = 13.0
    elif candidate == CAR.EV6:
      ret.mass = 2055. + STD_CARGO_KG
      ret.wheelbase = 2.90
      ret.steerRatio = 16.0
      tire_stiffness_factor = 0.65
    elif candidate in [CAR.SPORTAGE_NQ5, CAR.SPORTAGE_NQ5_HEV]:
      ret.mass = 1767. + STD_CARGO_KG
      ret.wheelbase = 2.756
      ret.steerRatio = 13.6
    elif candidate in [CAR.SORENTO_MQ4, CAR.SORENTO_MQ4_HEV]:
      ret.mass = 1857. + STD_CARGO_KG # weight from EX and above trims, average of FWD and AWD versions (EX, X-Line EX AWD, SX, SX Pestige, X-Line SX Prestige AWD)
      ret.wheelbase = 2.81
      ret.steerRatio = 13.27 # steering ratio according to Kia News https://www.kiamedia.com/us/en/models/sorento-phev/2022/specifications
    elif candidate in [CAR.NIRO_SG2_EV, CAR.NIRO_SG2_HEV]:
      ret.mass = 1472. + STD_CARGO_KG # weight from EX and above trims
      ret.wheelbase = 2.72
      ret.steerRatio = 13.7 # steering ratio according to Kia News https://www.kiamedia.com/us/en/models/niro/2023/specifications

    # genesis
    elif candidate == CAR.GENESIS:
      ret.mass = 2060. + STD_CARGO_KG
      ret.wheelbase = 3.01
      ret.steerRatio = 16.5
    elif candidate == CAR.GENESIS_G70:
      ret.mass = 1795. + STD_CARGO_KG
      ret.wheelbase = 2.83
      ret.steerRatio = 16.5
    elif candidate == CAR.GENESIS_G80:
      ret.mass = 2035. + STD_CARGO_KG
      ret.wheelbase = 3.01
      ret.steerRatio = 16.5
    elif candidate == CAR.GENESIS_G90:
      ret.mass = 2185. + STD_CARGO_KG
      ret.wheelbase = 3.16
      ret.steerRatio = 12.0
    elif candidate == CAR.GENESIS_GV60:
      ret.mass = 2205 + STD_CARGO_KG
      ret.wheelbase = 2.9
      ret.steerRatio = 12.6 # https://www.motor1.com/reviews/586376/2023-genesis-gv60-first-drive/#:~:text=Relative%20to%20the%20related%20Ioniq,5%2FEV6%27s%2014.3%3A1.
    elif candidate == CAR.GENESIS_GV70:
      ret.mass = 1950. + STD_CARGO_KG
      ret.wheelbase = 2.87
      ret.steerRatio = 13.27 * 1.15  # 15% higher at the center seems reasonable
      tire_stiffness_factor = 0.65
    elif candidate == CAR.GENESIS_GV80:
      ret.mass = 2258. + STD_CARGO_KG
      ret.wheelbase = 2.95
      ret.steerRatio = 14.14

    # *** lateral control ***
    lat_pid = Params().get("LateralControlSelect", encoding='utf8') == "0"
    lat_indi = Params().get("LateralControlSelect", encoding='utf8') == "1"
    lat_lqr = Params().get("LateralControlSelect", encoding='utf8') == "2"
    lat_torque = Params().get("LateralControlSelect", encoding='utf8') == "3"
    # -----------------------------------------------------------------
    if lat_pid:
      ret.lateralTuning.pid.kf = 0.00005
      ret.lateralTuning.pid.kpBP = [0.]
      ret.lateralTuning.pid.kiBP = [0.]

      if candidate == CAR.PALISADE:
        ret.lateralTuning.pid.kpV = [0.3]
        ret.lateralTuning.pid.kiV = [0.05]
      elif candidate in [CAR.GENESIS, CAR.GENESIS_G70, CAR.GENESIS_G80, CAR.GENESIS_G90]:
        ret.lateralTuning.pid.kpV = [0.16]
        ret.lateralTuning.pid.kiV = [0.01]
      else:
        ret.lateralTuning.pid.kpV = [0.25]
        ret.lateralTuning.pid.kiV = [0.05]

    # -----------------------------------------------------------------
    elif lat_indi:
      ret.lateralTuning.init('indi')
      ret.lateralTuning.indi.innerLoopGainBP = [0.]
      ret.lateralTuning.indi.outerLoopGainBP = [0.]
      ret.lateralTuning.indi.timeConstantBP = [0.]
      ret.lateralTuning.indi.actuatorEffectivenessBP = [0.]

      if candidate in [CAR.IONIQ_HEV, CAR.GENESIS_G70]:
        ret.lateralTuning.indi.innerLoopGainV = [2.5]
        ret.lateralTuning.indi.outerLoopGainV = [3.5]
        ret.lateralTuning.indi.timeConstantV = [1.4]
        ret.lateralTuning.indi.actuatorEffectivenessV = [1.8]
      elif candidate == CAR.SELTOS:
        ret.lateralTuning.indi.innerLoopGainV = [4.]
        ret.lateralTuning.indi.outerLoopGainV = [3.]
        ret.lateralTuning.indi.timeConstantV = [1.4]
        ret.lateralTuning.indi.actuatorEffectivenessV = [1.8]
      else:
        ret.lateralTuning.indi.innerLoopGainV = [3.5]
        ret.lateralTuning.indi.outerLoopGainV = [2.0]
        ret.lateralTuning.indi.timeConstantV = [1.4]
        ret.lateralTuning.indi.actuatorEffectivenessV = [2.3]

    # -----------------------------------------------------------------
    elif lat_lqr:
      ret.lateralTuning.init('lqr')
      ret.lateralTuning.lqr.a = [0., 1., -0.22619643, 1.21822268]
      ret.lateralTuning.lqr.b = [-1.92006585e-04, 3.95603032e-05]
      ret.lateralTuning.lqr.c = [1., 0.]

      if candidate in [CAR.GRANDEUR_IG, CAR.GRANDEUR_IG_HEV, CAR.GRANDEUR_IGFL, CAR.GRANDEUR_IGFL_HEV, CAR.K7, CAR.K7_HEV]:
        ret.lateralTuning.lqr.scale = 1650.
        ret.lateralTuning.lqr.ki = 0.03
        ret.lateralTuning.lqr.dcGain = 0.0028
        ret.lateralTuning.lqr.k = [-110., 451.]
        ret.lateralTuning.lqr.l = [0.33, 0.318]
      elif candidate == CAR.IONIQ_EV:
        ret.lateralTuning.lqr.scale = 3000.0
        ret.lateralTuning.lqr.ki = 0.005
        ret.lateralTuning.lqr.dcGain = 0.002237852961363602
        ret.lateralTuning.lqr.k = [-110.73572306, 451.22718255]
        ret.lateralTuning.lqr.l = [0.3233671, 0.3185757]
      elif candidate in [CAR.K5, CAR.K5_HEV]:
        ret.lateralTuning.lqr.scale = 1700.0
        ret.lateralTuning.lqr.ki = 0.016
        ret.lateralTuning.lqr.dcGain = 0.002
        ret.lateralTuning.lqr.k = [-110.0, 451.0]
        ret.lateralTuning.lqr.l = [0.33, 0.318]
      elif candidate == CAR.SELTOS:
        ret.lateralTuning.lqr.scale = 1500.0
        ret.lateralTuning.lqr.ki = 0.05
        ret.lateralTuning.lqr.dcGain = 0.002237852961363602
        ret.lateralTuning.lqr.k = [-110.73572306, 451.22718255]
        ret.lateralTuning.lqr.l = [0.3233671, 0.3185757]
      elif candidate in [CAR.GENESIS, CAR.GENESIS_G70, CAR.GENESIS_G80, CAR.GENESIS_G90]:
        ret.lateralTuning.lqr.scale = 1900.
        ret.lateralTuning.lqr.ki = 0.01
        ret.lateralTuning.lqr.dcGain = 0.0029
        ret.lateralTuning.lqr.k = [-110., 451.]
        ret.lateralTuning.lqr.l = [0.33, 0.318]
      else:
        ret.lateralTuning.lqr.scale = 1700.0
        ret.lateralTuning.lqr.ki = 0.03
        ret.lateralTuning.lqr.dcGain = 0.003
        ret.lateralTuning.lqr.k = [-105.0, 450.0]
        ret.lateralTuning.lqr.l = [0.22, 0.318]

    # -----------------------------------------------------------------
    elif any([lat_torque, candidate in CANFD_CAR]):
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    # *** longitudinal control ***
    if candidate in CANFD_CAR:
      ret.longitudinalTuning.kpV = [0.1]
      ret.longitudinalTuning.kiV = [0.0]
      ret.longitudinalActuatorDelayLowerBound = 0.3
      ret.longitudinalActuatorDelayUpperBound = 0.3
      ret.experimentalLongitudinalAvailable = candidate not in CANFD_RADAR_SCC_CAR
    else:
      ret.longitudinalTuning.kpBP = [0., 5.*CV.KPH_TO_MS, 10.*CV.KPH_TO_MS, 30.*CV.KPH_TO_MS, 130.*CV.KPH_TO_MS]
      ret.longitudinalTuning.kpV = [1.2, 1.05, 1.0, 0.92, 0.55]
      ret.longitudinalTuning.kiBP = [0., 130.*CV.KPH_TO_MS]
      ret.longitudinalTuning.kiV = [0.1, 0.05]
      ret.longitudinalActuatorDelayLowerBound = 0.3
      ret.longitudinalActuatorDelayUpperBound = 0.3
      ret.experimentalLongitudinalAvailable = not ret.radarUnavailable

    # WARNING: disabling radar also disables AEB (and we show the same warning on the instrument cluster as if you manually disabled AEB)
    ret.openpilotLongitudinalControl = experimental_long and ret.experimentalLongitudinalAvailable
    ret.pcmCruise = not ret.openpilotLongitudinalControl

    ret.stoppingControl = True
    ret.stoppingDecelRate = 1.0
    ret.vEgoStopping = 0.3
    ret.stopAccel = -3.5

    ret.startingState = True
    ret.vEgoStarting = 0.3
    ret.startAccel = 2.0

    # *** Params Init ***
    if candidate in CANFD_CAR:
      Params().put_bool("IsCanfd", True)
      Params().put("LateralControlSelect", "3")
      Params().put_bool("SccOnBus2", False)
    else:
      Params().put_bool("IsCanfd", False)
      if candidate == CAR.GENESIS:
        Params().put("MfcSelect", "0")

    # *** feature detection ***
    if candidate in CANFD_CAR:
      ret.enableBsm = 0x1e5 in fingerprint[CAN.ECAN] # 485
      ret.hasNav = 0x1fa in fingerprint[CAN.ECAN] # 506
      ret.radarUnavailable = RADAR_START_ADDR not in fingerprint[1] or DBC[ret.carFingerprint]["radar"] is None
    else:
      ret.enableBsm = 1419 in fingerprint[0]
      ret.hasAutoHold = 1151 in fingerprint[0]
      ret.hasNav = 1348 in fingerprint[0] and Params().get_bool("NavLimitSpeed")
      ret.hasLfa = 913 in fingerprint[0] and Params().get("MfcSelect", encoding='utf8') == "2"

      ret.sccBus = 2 if Params().get_bool("SccOnBus2") else 0

      if ret.sccBus == 2:
        Params().put_bool("ExperimentalLongitudinalEnabled", True)
        ret.hasScc13 = 1290 in fingerprint[0] or 1290 in fingerprint[2]
        ret.hasScc14 = 905 in fingerprint[0] or 905 in fingerprint[2]

      ret.radarUnavailable = ret.sccBus == -1

      if ret.openpilotLongitudinalControl and ret.sccBus == 0:
        ret.pcmCruise = False
      else:
        ret.pcmCruise = True

    # *** panda safety config ***
    if candidate in CANFD_CAR:
      cfgs = [get_safety_config(car.CarParams.SafetyModel.hyundaiCanfd), ]
      if CAN.ECAN >= 4:
        cfgs.insert(0, get_safety_config(car.CarParams.SafetyModel.noOutput))
      ret.safetyConfigs = cfgs

      if ret.flags & HyundaiFlags.CANFD_HDA2:
        ret.safetyConfigs[-1].safetyParam |= Panda.FLAG_HYUNDAI_CANFD_HDA2
      if ret.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
        ret.safetyConfigs[-1].safetyParam |= Panda.FLAG_HYUNDAI_CANFD_ALT_BUTTONS
      if ret.flags & HyundaiFlags.CANFD_CAMERA_SCC:
        ret.safetyConfigs[-1].safetyParam |= Panda.FLAG_HYUNDAI_CAMERA_SCC
    else:
      if candidate in LEGACY_SAFETY_MODE_CAR:
        # these cars require a special panda safety mode due to missing counters and checksums in the messages
        ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.hyundaiLegacy)]
      else:
        ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.hyundai, 0)]

    if ret.openpilotLongitudinalControl:
      ret.safetyConfigs[-1].safetyParam |= Panda.FLAG_HYUNDAI_LONG
    if candidate in (CANFD_HEV_CAR | HEV_CAR):
      ret.safetyConfigs[-1].safetyParam |= Panda.FLAG_HYUNDAI_HYBRID_GAS
    elif candidate in (CANFD_EV_CAR | EV_CAR):
      ret.safetyConfigs[-1].safetyParam |= Panda.FLAG_HYUNDAI_EV_GAS

    ret.centerToFront = ret.wheelbase * 0.4

    # TODO: start from empirically derived lateral slip stiffness for the civic and scale by
    # mass and CG position, so all cars will have approximately similar dyn behaviors
    ret.tireStiffnessFront, ret.tireStiffnessRear = scale_tire_stiffness(ret.mass, ret.wheelbase, ret.centerToFront,
                                                                         tire_stiffness_factor=tire_stiffness_factor)
    return ret


  @staticmethod
  def init(CP, logcan, sendcan):
    radar_track = Params().get_bool("RadarTrackEnable")
    if all([CP.openpilotLongitudinalControl, radar_track]):
      addr, bus = 0x7d0, 0
      if CP.flags & HyundaiFlags.CANFD_HDA2.value:
        addr, bus = 0x730, CanBus(CP).ECAN
      disable_ecu(logcan, sendcan, bus=bus, addr=addr, com_cont_req=b'\x28\x83\x01')

    # for blinkers
    if CP.flags & HyundaiFlags.ENABLE_BLINKERS:
      disable_ecu(logcan, sendcan, bus=CanBus(CP).ECAN, addr=0x7B1, com_cont_req=b'\x28\x83\x01')


  def _update(self, c):
    ret = self.CS.update(self.cp, self.cp_cam)

    if self.frame % 20 == 0:
      if not any([self.cp.can_valid, self.cp_cam.can_valid]):
        print(f'cp = {bool(self.cp.can_valid)}  cp_cam = {bool(self.cp_cam.can_valid)}')
    self.frame += 1

    if self.CS.cruise_buttons[-1] != self.CS.prev_cruise_buttons:
      buttonEvents = [create_button_event(self.CS.cruise_buttons[-1], self.CS.prev_cruise_buttons, BUTTONS_DICT)]
      # Handle CF_Clu_CruiseSwState changing buttons mid-press
      if self.CS.cruise_buttons[-1] != 0 and self.CS.prev_cruise_buttons != 0:
        buttonEvents.append(create_button_event(0, self.CS.prev_cruise_buttons, BUTTONS_DICT))

      ret.buttonEvents = buttonEvents

    # On some newer model years, the CANCEL button acts as a pause/resume button based on the PCM state
    # To avoid re-engaging when openpilot cancels, check user engagement intention via buttons
    # Main button also can trigger an engagement on these cars
    allow_enable = any(btn in ENABLE_BUTTONS for btn in self.CS.cruise_buttons) or any(self.CS.main_buttons) \
                   or self.CP.carFingerprint in CANFD_CAR

    events = self.create_common_events(ret, pcm_enable=self.CS.CP.pcmCruise, allow_enable=allow_enable)

    # turning indicator alert logic
    if any([ret.leftBlinker, ret.rightBlinker, self.CC.turning_signal_timer]) and ret.vEgo < LANE_CHANGE_SPEED_MIN - 1.2:
      self.CC.turning_indicator_alert = True
    else:
      self.CC.turning_indicator_alert = False

    if self.CC.turning_indicator_alert:
      events.add(EventName.turningIndicatorOn)

    # scc smoother
    if self.CC.scc_smoother is not None:
      self.CC.scc_smoother.inject_events(events)

    # low speed steer alert hysteresis logic (only for cars with steer cut off above 10 m/s)
    if ret.vEgo < (self.CP.minSteerSpeed + 2.) and self.CP.minSteerSpeed > 10.:
      self.low_speed_alert = True
    if ret.vEgo > (self.CP.minSteerSpeed + 4.):
      self.low_speed_alert = False
    if self.low_speed_alert:
      events.add(car.CarEvent.EventName.belowSteerSpeed)

    ret.events = events.to_msg()

    return ret


  def apply(self, c, now_nanos, controls):
    return self.CC.update(c, self.CS, now_nanos, controls)
