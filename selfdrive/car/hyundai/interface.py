#!/usr/bin/env python3
from cereal import car
from common.params import Params
from common.numpy_fast import interp
from common.conversions import Conversions as CV
from selfdrive.car.hyundai.values import CAR, Buttons, CarControllerParams, HDA2_CAR
from selfdrive.car import STD_CARGO_KG, create_button_enable_events, create_button_event, scale_rot_inertia, scale_tire_stiffness, gen_empty_fingerprint, get_safety_config
from selfdrive.car.interfaces import CarInterfaceBase
from selfdrive.controls.lib.latcontrol_torque import set_torque_tune
from selfdrive.controls.lib.desire_helper import LANE_CHANGE_SPEED_MIN

ButtonType = car.CarState.ButtonEvent.Type
EventName = car.CarEvent.EventName
ENABLE_BUTTONS = (Buttons.RES_ACCEL, Buttons.SET_DECEL, Buttons.CANCEL)
BUTTONS_DICT = {Buttons.RES_ACCEL: ButtonType.accelCruise, Buttons.SET_DECEL: ButtonType.decelCruise,
                Buttons.GAP_DIST: ButtonType.gapAdjustCruise, Buttons.CANCEL: ButtonType.cancel}

class CarInterface(CarInterfaceBase):
  @staticmethod
  def get_pid_accel_limits(CP, current_speed, cruise_speed):
    v_current_kph = current_speed * CV.MS_TO_KPH

    gas_max_bp = [10., 20., 50., 70., 130., 150.]
    gas_max_v = [1.4, 1.2, 0.63, 0.44, 0.15, 0.1]

    return CarControllerParams.ACCEL_MIN, interp(v_current_kph, gas_max_bp, gas_max_v)

  @staticmethod
  def get_params(candidate, fingerprint=gen_empty_fingerprint(), car_fw=[], disable_radar=False):  # pylint: disable=dangerous-default-value
    ret = CarInterfaceBase.get_std_params(candidate, fingerprint)
    ret.openpilotLongitudinalControl = Params().get("LongControlSelect", encoding='utf8') == "1"

    ret.carName = "hyundai"
    ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.hyundaiCommunity, 0)]

    ret.maxSteeringAngleDeg = 1000.
    ret.steerFaultMaxAngle = 85
    ret.steerFaultMaxFrames = 90

    tire_stiffness_factor = 0.8

    # STD_CARGO_KG=136. wheelbase or mass date using wikipedia
    # hyundai
    if candidate in [CAR.ELANTRA_I30, CAR.ELANTRA21, CAR.ELANTRA21_HEV]:
      ret.mass = 1340. + STD_CARGO_KG
      ret.wheelbase = 2.720
      ret.steerRatio = 15.4
    elif candidate in [CAR.SONATA, CAR.SONATA_HEV]:
      ret.mass = 1615. + STD_CARGO_KG
      ret.wheelbase = 2.840
      ret.steerRatio = 15.2
      ret.maxLateralAccel = 2.5
    elif candidate in [CAR.SONATA_LF, CAR.SONATA_LF_HEV]:
      ret.mass = 1640. + STD_CARGO_KG
      ret.wheelbase = 2.805
      ret.steerRatio = 15.2
      ret.maxLateralAccel = 1.8
    elif candidate in [CAR.KONA, CAR.KONA_EV, CAR.KONA_HEV]:
      ret.mass = 1743. + STD_CARGO_KG
      ret.wheelbase = 2.600
      ret.steerRatio = 13.7
    elif candidate in [CAR.IONIQ_EV, CAR.IONIQ_HEV]:
      ret.mass = 1575. + STD_CARGO_KG
      ret.wheelbase = 2.700
      ret.steerRatio = 13.7
      ret.maxLateralAccel = 3.0
    elif candidate in [CAR.SANTA_FE, CAR.SANTA_FE_HEV]:
      ret.mass = 1910. + STD_CARGO_KG
      ret.wheelbase = 2.765
      ret.steerRatio = 15.8
      ret.maxLateralAccel = 3.2
    elif candidate == CAR.PALISADE:
      ret.mass = 2060. + STD_CARGO_KG
      ret.wheelbase = 2.900
      ret.steerRatio = 15.8
      ret.maxLateralAccel = 2.5
    elif candidate == CAR.VELOSTER:
      ret.mass = 1350. + STD_CARGO_KG
      ret.wheelbase = 2.650
      ret.steerRatio = 15.8
    elif candidate in [CAR.GRANDEUR, CAR.GRANDEUR_HEV, CAR.GRANDEUR20, CAR.GRANDEUR20_HEV]:
      ret.mass = 1719. + STD_CARGO_KG
      ret.wheelbase = 2.885
      ret.steerRatio = 12.5
      ret.maxLateralAccel = 2.5
    elif candidate == CAR.NEXO:
      ret.mass = 1873. + STD_CARGO_KG
      ret.wheelbase = 2.790
      ret.steerRatio = 13.7

    # kia
    elif candidate == CAR.FORTE:
      ret.mass = 1345. + STD_CARGO_KG
      ret.wheelbase = 2.700
      ret.steerRatio = 13.7
    elif candidate in [CAR.K5, CAR.K5_HEV, CAR.K5_DL3, CAR.K5_DL3_HEV]:
      ret.mass = 1565. + STD_CARGO_KG
      ret.wheelbase = 2.805
      ret.steerRatio = 15.2
      ret.maxLateralAccel = 2.1
    elif candidate in [CAR.K7, CAR.K7_HEV]:
      ret.mass = 1730. + STD_CARGO_KG
      ret.wheelbase = 2.855
      ret.steerRatio = 12.5
    elif candidate == CAR.K9:
      ret.mass = 2005. + STD_CARGO_KG
      ret.wheelbase = 3.15
      ret.steerRatio = 16.5
    elif candidate == CAR.SPORTAGE:
      ret.mass = 1770. + STD_CARGO_KG
      ret.wheelbase = 2.670
      ret.steerRatio = 15.8
    elif candidate == CAR.SORENTO:
      ret.mass = 1885. + STD_CARGO_KG
      ret.wheelbase = 2.815
      ret.steerRatio = 15.8
    elif candidate == CAR.MOHAVE:
      ret.mass = 2305. + STD_CARGO_KG
      ret.wheelbase = 2.895
      ret.steerRatio = 14.1
    elif candidate == CAR.STINGER:
      ret.mass = 1913. + STD_CARGO_KG
      ret.wheelbase = 2.905
      ret.steerRatio = 13.5
    elif candidate in [CAR.NIRO_EV, CAR.NIRO_HEV]:
      ret.mass = 1748. + STD_CARGO_KG
      ret.wheelbase = 2.700
      ret.steerRatio = 13.7
      ret.maxLateralAccel = 2.9
    elif candidate == CAR.SOUL_EV:
      ret.mass = 1375. + STD_CARGO_KG
      ret.wheelbase = 2.600
      ret.steerRatio = 13.7
    elif candidate == CAR.SELTOS:
      ret.mass = 1510. + STD_CARGO_KG
      ret.wheelbase = 2.630
      ret.steerRatio = 13.0
    elif candidate == CAR.EV6:
      ret.mass = 2055 + STD_CARGO_KG
      ret.wheelbase = 2.9
      ret.steerRatio = 16.
      ret.maxLateralAccel = 2.
      ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.noOutput),
                           get_safety_config(car.CarParams.SafetyModel.hyundaiHDA2)]

    # genesis
    elif candidate == CAR.GENESIS:
      ret.mass = 2060. + STD_CARGO_KG
      ret.wheelbase = 3.010
      ret.steerRatio = 16.5
    elif candidate == CAR.GENESIS_G70:
      ret.mass = 1795. + STD_CARGO_KG
      ret.wheelbase = 2.835
      ret.steerRatio = 16.5
    elif candidate == CAR.GENESIS_G80:
      ret.mass = 2035. + STD_CARGO_KG
      ret.wheelbase = 3.010
      ret.steerRatio = 16.5
    elif candidate == CAR.GENESIS_G90:
      ret.mass = 2185. + STD_CARGO_KG
      ret.wheelbase = 3.160
      ret.steerRatio = 12.0

    # Pid -----------------------------------------------------------------
    if Params().get("LateralControlSelect", encoding='utf8') == "0":
      if candidate in [CAR.GENESIS, CAR.GENESIS_G70, CAR.GENESIS_G80, CAR.GENESIS_G90]:
        ret.lateralTuning.pid.kf = 0.00005
        ret.lateralTuning.pid.kpBP = [0.]
        ret.lateralTuning.pid.kpV = [0.16]
        ret.lateralTuning.pid.kiBP = [0.]
        ret.lateralTuning.pid.kiV = [0.01]
      elif candidate == CAR.PALISADE:
        ret.lateralTuning.pid.kf = 0.00005
        ret.lateralTuning.pid.kpBP = [0.]
        ret.lateralTuning.pid.kpV = [0.3]
        ret.lateralTuning.pid.kiBP = [0.]
        ret.lateralTuning.pid.kiV = [0.05]
      else:
        ret.lateralTuning.pid.kf = 0.00005
        ret.lateralTuning.pid.kpBP = [0.]
        ret.lateralTuning.pid.kpV = [0.25]
        ret.lateralTuning.pid.kiBP = [0.]
        ret.lateralTuning.pid.kiV = [0.05]

    # Indi -----------------------------------------------------------------
    elif Params().get("LateralControlSelect", encoding='utf8') == "1":
      ret.lateralTuning.init('indi')
      if candidate == CAR.GENESIS:
        ret.lateralTuning.indi.innerLoopGainBP = [0.]
        ret.lateralTuning.indi.innerLoopGainV = [3.5]
        ret.lateralTuning.indi.outerLoopGainBP = [0.]
        ret.lateralTuning.indi.outerLoopGainV = [2.0]
        ret.lateralTuning.indi.timeConstantBP = [0.]
        ret.lateralTuning.indi.timeConstantV = [1.4]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0.]
        ret.lateralTuning.indi.actuatorEffectivenessV = [2.3]
      elif candidate  == CAR.GENESIS_G70:
        ret.lateralTuning.indi.innerLoopGainBP = [0.]
        ret.lateralTuning.indi.innerLoopGainV = [2.5]
        ret.lateralTuning.indi.outerLoopGainBP = [0.]
        ret.lateralTuning.indi.outerLoopGainV = [3.5]
        ret.lateralTuning.indi.timeConstantBP = [0.]
        ret.lateralTuning.indi.timeConstantV = [1.4]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0.]
        ret.lateralTuning.indi.actuatorEffectivenessV = [1.8]
      elif candidate == CAR.SELTOS:
        ret.lateralTuning.indi.innerLoopGainBP = [0.]
        ret.lateralTuning.indi.innerLoopGainV = [4.]
        ret.lateralTuning.indi.outerLoopGainBP = [0.]
        ret.lateralTuning.indi.outerLoopGainV = [3.]
        ret.lateralTuning.indi.timeConstantBP = [0.]
        ret.lateralTuning.indi.timeConstantV = [1.4]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0.]
        ret.lateralTuning.indi.actuatorEffectivenessV = [1.8]
      else:
        ret.lateralTuning.indi.innerLoopGainBP = [0.]
        ret.lateralTuning.indi.innerLoopGainV = [3.5]
        ret.lateralTuning.indi.outerLoopGainBP = [0.]
        ret.lateralTuning.indi.outerLoopGainV = [2.0]
        ret.lateralTuning.indi.timeConstantBP = [0.]
        ret.lateralTuning.indi.timeConstantV = [1.4]
        ret.lateralTuning.indi.actuatorEffectivenessBP = [0.]
        ret.lateralTuning.indi.actuatorEffectivenessV = [2.3]

    # Lqr -----------------------------------------------------------------
    elif Params().get("LateralControlSelect", encoding='utf8') == "2":
      ret.lateralTuning.init('lqr')
      if candidate in [CAR.GENESIS, CAR.GENESIS_G70, CAR.GENESIS_G80, CAR.GENESIS_G90]:
        ret.lateralTuning.lqr.scale = 1900.
        ret.lateralTuning.lqr.ki = 0.01
        ret.lateralTuning.lqr.dcGain = 0.0029
        ret.lateralTuning.lqr.a = [0., 1., -0.22619643, 1.21822268]
        ret.lateralTuning.lqr.b = [-1.92006585e-04, 3.95603032e-05]
        ret.lateralTuning.lqr.c = [1., 0.]
        ret.lateralTuning.lqr.k = [-110., 451.]
        ret.lateralTuning.lqr.l = [0.33, 0.318]
      elif candidate in [CAR.K5, CAR.K5_HEV]:
        ret.lateralTuning.lqr.scale = 1700.0
        ret.lateralTuning.lqr.ki = 0.016
        ret.lateralTuning.lqr.dcGain = 0.002
        ret.lateralTuning.lqr.a = [0., 1., -0.22619643, 1.21822268]
        ret.lateralTuning.lqr.b = [-1.92006585e-04, 3.95603032e-05]
        ret.lateralTuning.lqr.c = [1., 0.]
        ret.lateralTuning.lqr.k = [-110.0, 451.0]
        ret.lateralTuning.lqr.l = [0.33, 0.318]
      elif candidate in [CAR.GRANDEUR, CAR.GRANDEUR_HEV, CAR.GRANDEUR20, CAR.GRANDEUR20_HEV, CAR.K7, CAR.K7_HEV]:
        ret.lateralTuning.lqr.scale = 1600.
        ret.lateralTuning.lqr.ki = 0.01
        ret.lateralTuning.lqr.dcGain = 0.0027
        ret.lateralTuning.lqr.a = [0., 1., -0.22619643, 1.21822268]
        ret.lateralTuning.lqr.b = [-1.92006585e-04, 3.95603032e-05]
        ret.lateralTuning.lqr.c = [1., 0.]
        ret.lateralTuning.lqr.k = [-110., 451.]
        ret.lateralTuning.lqr.l = [0.33, 0.318]
      elif candidate == CAR.SELTOS:
        ret.lateralTuning.lqr.scale = 1500.0
        ret.lateralTuning.lqr.ki = 0.05
        ret.lateralTuning.lqr.dcGain = 0.002237852961363602
        ret.lateralTuning.lqr.a = [0., 1., -0.22619643, 1.21822268]
        ret.lateralTuning.lqr.b = [-1.92006585e-04, 3.95603032e-05]
        ret.lateralTuning.lqr.c = [1., 0.]
        ret.lateralTuning.lqr.k = [-110.73572306, 451.22718255]
        ret.lateralTuning.lqr.l = [0.3233671, 0.3185757]
      elif candidate == CAR.IONIQ_EV:
        ret.lateralTuning.lqr.scale = 3000.0
        ret.lateralTuning.lqr.ki = 0.005
        ret.lateralTuning.lqr.dcGain = 0.002237852961363602
        ret.lateralTuning.lqr.a = [0., 1., -0.22619643, 1.21822268]
        ret.lateralTuning.lqr.b = [-1.92006585e-04, 3.95603032e-05]
        ret.lateralTuning.lqr.c = [1., 0.]
        ret.lateralTuning.lqr.k = [-110.73572306, 451.22718255]
        ret.lateralTuning.lqr.l = [0.3233671, 0.3185757]
      else:
        ret.lateralTuning.lqr.scale = 1700.0
        ret.lateralTuning.lqr.ki = 0.03
        ret.lateralTuning.lqr.dcGain = 0.003
        ret.lateralTuning.lqr.a = [0., 1., -0.22619643, 1.21822268]
        ret.lateralTuning.lqr.b = [-1.92006585e-04, 3.95603032e-05]
        ret.lateralTuning.lqr.c = [1., 0.]
        ret.lateralTuning.lqr.k = [-105.0, 450.0]
        ret.lateralTuning.lqr.l = [0.22, 0.318]

    # Torque -----------------------------------------------------------------
    elif Params().get("LateralControlSelect", encoding='utf8') == "3":
      set_torque_tune(ret.lateralTuning, ret.maxLateralAccel, 0.01, 0.0)

    ret.centerToFront = ret.wheelbase * 0.4
    ret.radarTimeStep = 0.05

    ret.steerActuatorDelay = 0.2
    ret.steerLimitTimer = 2.5

    # longitudinal
    ret.longitudinalTuning.kpBP = [0., 5. * CV.KPH_TO_MS, 10. * CV.KPH_TO_MS, 30. * CV.KPH_TO_MS, 130. * CV.KPH_TO_MS]
    ret.longitudinalTuning.kpV = [1.2, 1.0, 0.93, 0.88, 0.5]
    ret.longitudinalTuning.kiBP = [0., 130. * CV.KPH_TO_MS]
    ret.longitudinalTuning.kiV = [0.08, 0.03]
    ret.longitudinalActuatorDelayLowerBound = 0.3
    ret.longitudinalActuatorDelayUpperBound = 0.3

    ret.stopAccel = -2.0
    ret.stoppingDecelRate = 0.4  # brake_travel/s while trying to stop
    ret.vEgoStarting = 0.5
    ret.vEgoStopping = 1.0

    # TODO: get actual value, for now starting with reasonable value for
    # civic and scaling by mass and wheelbase
    ret.rotationalInertia = scale_rot_inertia(ret.mass, ret.wheelbase)

    # TODO: start from empirically derived lateral slip stiffness for the civic and scale by
    # mass and CG position, so all cars will have approximately similar dyn behaviors
    ret.tireStiffnessFront, ret.tireStiffnessRear = \
      scale_tire_stiffness(ret.mass, ret.wheelbase, ret.centerToFront, tire_stiffness_factor=tire_stiffness_factor)

    if candidate in HDA2_CAR:
      ret.enableBsm = 0x58b in fingerprint[0]
      ret.radarOffCan = False
    else:
      # ignore CAN2 address if L-CAN on the same BUS
      ret.mdpsBus = 1 if 593 in fingerprint[1] and 1296 not in fingerprint[1] else 0
      ret.sasBus = 1 if 688 in fingerprint[1] and 1296 not in fingerprint[1] else 0
      ret.sccBus = 0 if 1056 in fingerprint[0] \
              else 1 if 1056 in fingerprint[1] and 1296 not in fingerprint[1] \
              else 2 if 1056 in fingerprint[2] else -1

      if ret.sccBus >= 0:
        ret.hasScc13 = 1290 in fingerprint[ret.sccBus]
        ret.hasScc14 = 905 in fingerprint[ret.sccBus]

      ret.enableBsm = 0x58b in fingerprint[0]
      ret.enableAutoHold = 1151 in fingerprint[0]
      ret.hasEms = 608 in fingerprint[0] and 809 in fingerprint[0]
      ret.hasLfaHda = 1157 in fingerprint[0]
      ret.aebFcw = Params().get("AebSelect", encoding='utf8') == "1"
      ret.radarOffCan = ret.sccBus == -1

    ret.pcmCruise = not ret.radarOffCan

    return ret

  def _update(self, c):
    ret = self.CS.update(self.cp, self.cp2, self.cp_cam)
    #ret.steeringRateLimited = self.CC.steer_rate_limited if self.CC is not None else False

    if not self.cp.can_valid or not self.cp2.can_valid or not self.cp_cam.can_valid:
      print('cp = {}  cp2 = {}  cp_cam = {}'.format(bool(self.cp.can_valid), bool(self.cp2.can_valid), bool(self.cp_cam.can_valid)))

    if self.CP.pcmCruise and not self.CC.scc_live:
      self.CP.pcmCruise = False
    elif self.CC.scc_live and not self.CP.pcmCruise:
      self.CP.pcmCruise = True

    # most HKG cars has no long control, it is safer and easier to engage by main on
    if any([Params().get("LongControlSelect", encoding='utf8') == "0", Params().get("LongControlSelect", encoding='utf8') == "1"]):
      ret.cruiseState.enabled = ret.cruiseState.available

    allow_enable = any([self.CS.cruise_buttons or self.CS.main_buttons])
    events = self.create_common_events(ret, pcm_enable=self.CS.CP.pcmCruise, allow_enable=allow_enable or True)

    if self.CS.cruise_buttons != self.CS.prev_cruise_buttons:
      buttonEvents = [create_button_event(self.CS.cruise_buttons, self.CS.prev_cruise_buttons, BUTTONS_DICT)]
      # Handle CF_Clu_CruiseSwState changing buttons mid-press
      if self.CS.cruise_buttons != 0 and self.CS.prev_cruise_buttons != 0:
        buttonEvents.append(create_button_event(0, self.CS.prev_cruise_buttons, BUTTONS_DICT))

      ret.buttonEvents = buttonEvents
      events.events.extend(create_button_enable_events(ret.buttonEvents))

    # turning indicator alert logic
    if any([ret.leftBlinker, ret.rightBlinker, self.CC.turning_signal_timer]) and ret.vEgo < LANE_CHANGE_SPEED_MIN - 1.2:
      self.CC.turning_indicator_alert = True
    else:
      self.CC.turning_indicator_alert = False

    if self.CC.longcontrol and self.CS.cruise_unavail:
      events.add(EventName.brakeUnavailable)
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
    if self.low_speed_alert and not self.CS.mdps_bus:
      events.add(car.CarEvent.EventName.belowSteerSpeed)

    ret.events = events.to_msg()

    return ret

  # scc smoother - hyundai only
  def apply(self, c, controls):
    ret = self.CC.update(c, self.CS, controls)
    return ret
