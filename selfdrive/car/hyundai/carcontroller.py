from random import randint

from cereal import car
from common.params import Params
from common.conversions import Conversions as CV
from common.numpy_fast import clip
from common.realtime import DT_CTRL
from opendbc.can.packer import CANPacker
from selfdrive.car import apply_std_steer_torque_limits
from selfdrive.car.hyundai import hyundaicanfd, hyundaican
from selfdrive.car.hyundai.values import HyundaiFlags, Buttons, CarControllerParams, CANFD_CAR, CAR
from selfdrive.road_speed_limiter import road_speed_limiter_get_active
from selfdrive.car.hyundai.scc_smoother import SccSmoother

VisualAlert = car.CarControl.HUDControl.VisualAlert
LongCtrlState = car.CarControl.Actuators.LongControlState

# EPS faults if you apply torque while the steering angle is above 90 degrees for more than 1 second
# All slightly below EPS thresholds to avoid fault
MAX_ANGLE = 85
MAX_ANGLE_FRAMES = 89
MAX_ANGLE_CONSECUTIVE_FRAMES = 2

def process_hud_alert(enabled, fingerprint, hud_control):
  sys_warning = (hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw))

  # initialize to no line visible
  # TODO: this is not accurate for all cars
  sys_state = 1
  if hud_control.leftLaneVisible and hud_control.rightLaneVisible or sys_warning:  # HUD alert only display when LKAS status is active
    sys_state = 3 if enabled or sys_warning else 4
  elif hud_control.leftLaneVisible:
    sys_state = 5
  elif hud_control.rightLaneVisible:
    sys_state = 6

  # initialize to no warnings
  left_lane_warning = 0
  right_lane_warning = 0
  if hud_control.leftLaneDepart:
    left_lane_warning = 1 if fingerprint in (CAR.GENESIS_G90, CAR.GENESIS_G80) else 2
  if hud_control.rightLaneDepart:
    right_lane_warning = 1 if fingerprint in (CAR.GENESIS_G90, CAR.GENESIS_G80) else 2

  return sys_warning, sys_state, left_lane_warning, right_lane_warning


class CarController:
  def __init__(self, dbc_name, CP, VM):
    self.CP = CP
    self.CCP = CarControllerParams(CP)
    self.longcontrol = CP.openpilotLongitudinalControl
    self.scc_live = not CP.radarOffCan
    self.car_fingerprint = CP.carFingerprint

    self.packer = CANPacker(dbc_name)
    self.angle_limit_counter = 0
    self.frame = 0
    self.last_button_frame = 0
    self.apply_steer_last = 0
    self.lkas11_cnt = 0
    self.scc12_cnt = -1
    self.resume_cnt = 0
    self.last_lead_distance = 0
    self.resume_wait_timer = 0
    self.turning_signal_timer = 0
    self.turning_indicator_alert = False

    self.scc_smoother = SccSmoother(CP)
    self.lfahdamfc = Params().get("MfcSelect", encoding='utf8') == "2"


  def update(self, CC, CS, controls):
    if self.CP.carFingerprint in CANFD_CAR:
      return self.update_canfd(CC, CS, controls)

    actuators = CC.actuators
    hud_control = CC.hudControl

    # Steering Torque
    steer = actuators.steer
    new_steer = int(round(steer * self.CCP.STEER_MAX))
    apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, self.CCP)

    # Disable steering while turning blinker on and speed below 60 kph
    if CS.out.leftBlinker or CS.out.rightBlinker:
      self.turning_signal_timer = 0.5 / DT_CTRL  # Disable for 0.5 Seconds after blinker turned off
    if self.turning_indicator_alert: # set and clear by interface
      CC.latActive = False
    if self.turning_signal_timer > 0:
      self.turning_signal_timer -= 1
    if not CC.latActive:
      apply_steer = 0

    self.apply_steer_last = apply_steer
    apply_steer = int(round(float(apply_steer)))

    accel = clip(actuators.accel, self.CCP.ACCEL_MIN, self.CCP.ACCEL_MAX)

    # HUD messages
    sys_warning, sys_state, left_lane_warning, right_lane_warning = process_hud_alert(CC.enabled, self.car_fingerprint, hud_control)

    clu11_speed = CS.clu11["CF_Clu_Vanz"]
    enabled_speed = 60 if CS.is_metric else 38
    if clu11_speed > enabled_speed or not CC.latActive:
      enabled_speed = clu11_speed

    if self.frame == 0:  # initialize counts from last received count signals
      self.lkas11_cnt = CS.lkas11["CF_Lkas_MsgCount"]
    self.lkas11_cnt = (self.lkas11_cnt + 1) % 0x10

    can_sends = []

    # >90 degree steering fault prevention
    # Count up to MAX_ANGLE_FRAMES, at which point we need to cut torque to avoid a steering fault
    if CC.latActive and abs(CS.out.steeringAngleDeg) >= MAX_ANGLE:
      self.angle_limit_counter += 1
    else:
      self.angle_limit_counter = 0

    # Cut steer actuation bit for two frames and hold torque with induced temporary fault
    torque_fault = CC.latActive and self.angle_limit_counter > MAX_ANGLE_FRAMES
    lat_active = CC.latActive and not torque_fault

    if self.angle_limit_counter >= MAX_ANGLE_FRAMES + MAX_ANGLE_CONSECUTIVE_FRAMES:
      self.angle_limit_counter = 0

    can_sends.append(hyundaican.create_lkas11(self.packer, self.frame, self.CP.carFingerprint, apply_steer, lat_active, torque_fault, CS.lkas11, sys_warning,
                                              sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible, left_lane_warning, right_lane_warning, 0))

    if CS.eps_bus or CS.scc_bus == 1:  # send lkas11 bus 1 if eps or scc is on bus 1
      can_sends.append(hyundaican.create_lkas11(self.packer, self.frame, self.CP.carFingerprint, apply_steer, lat_active, torque_fault, CS.lkas11, sys_warning,
                                                sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible, left_lane_warning, right_lane_warning, 1))

    if self.frame % 2 and CS.eps_bus: # send clu11 to eps if it is not on bus 0
      can_sends.append(hyundaican.create_clu11(self.packer, CS.eps_bus, CS.clu11, Buttons.NONE, enabled_speed))

    if CS.eps_bus:  # send mdps12 to LKAS to prevent LKAS error
      can_sends.append(hyundaican.create_mdps12(self.packer, self.frame, CS.mdps12))

    self.update_auto_resume(CC, CS, clu11_speed, can_sends)
    self.update_scc(CC, CS, actuators, controls, hud_control, can_sends)

    # 20 Hz LFA MFA message
    if self.frame % 5 == 0:
      activated_hda = road_speed_limiter_get_active()  # 0 off, 1 main road, 2 highway
      if self.lfahdamfc:
        can_sends.append(hyundaican.create_lfahda_mfc(self.packer, CC.enabled, activated_hda))
      elif CS.has_lfa_hda:
        can_sends.append(hyundaican.create_hda_mfc(self.packer, activated_hda, CS, hud_control.leftLaneVisible, hud_control.rightLaneVisible))

    # 5 Hz ACC options
    #if self.frame % 20 == 0 and self.CP.openpilotLongitudinalControl:
    #  can_sends.extend(hyundaican.create_acc_opt(self.packer))

    # 2 Hz front radar options
    #if self.frame % 50 == 0 and self.CP.openpilotLongitudinalControl:
    #  can_sends.append(hyundaican.create_frt_radar_opt(self.packer))

    new_actuators = actuators.copy()
    new_actuators.steer = apply_steer / self.CCP.STEER_MAX
    new_actuators.accel = accel

    self.frame += 1
    return new_actuators, can_sends

  def update_auto_resume(self, CC, CS, clu11_speed, can_sends):
    # fix auto resume - by neokii
    if CC.cruiseControl.resume and not CS.out.gasPressed:
      if self.last_lead_distance == 0:
        self.last_lead_distance = CS.lead_distance
        self.resume_cnt = 0
        self.resume_wait_timer = 0

      elif self.scc_smoother.is_active(self.frame):
        pass

      elif self.resume_wait_timer > 0:
        self.resume_wait_timer -= 1

      elif abs(CS.lead_distance - self.last_lead_distance) > 0.1:
        can_sends.append(hyundaican.create_clu11(self.packer, CS.scc_bus, CS.clu11, Buttons.RES_ACCEL, clu11_speed))
        self.resume_cnt += 1

        if self.resume_cnt >= int(randint(4, 5) * 2):
          self.resume_cnt = 0
          self.resume_wait_timer = int(randint(20, 25) * 2)

    elif self.last_lead_distance != 0:
      self.last_lead_distance = 0

  def update_scc(self, CC, CS, actuators, controls, hud_control, can_sends):
    # scc smoother
    self.scc_smoother.update(CC.enabled, can_sends, self.packer, CC, CS, self.frame, controls)

    # send scc to car if longcontrol enabled and SCC not on bus 0 or ont live
    if self.longcontrol and CS.out.cruiseState.enabled and (CS.scc_bus or not self.scc_live):
      if self.frame % 2 == 0:
        set_speed_in_units = hud_control.setSpeed * (CV.MS_TO_KPH if CS.is_metric else CV.MS_TO_MPH)

        stopping = (actuators.longControlState == LongCtrlState.stopping)
        apply_accel = self.scc_smoother.get_apply_accel(CS, actuators.accel)
        apply_accel = clip(apply_accel if CC.longActive else 0,
                           self.CCP.ACCEL_MIN, self.CCP.ACCEL_MAX)

        if self.scc12_cnt < 0:
          self.scc12_cnt = CS.scc12["CR_VSM_Alive"] if not CS.no_radar else 0
        self.scc12_cnt += 1
        self.scc12_cnt %= 0xF

        can_sends.append(hyundaican.create_scc11(self.packer, self.frame, CC.enabled, set_speed_in_units, self.scc_live, CS.scc11))

        can_sends.append(hyundaican.create_scc12(self.packer, apply_accel, CC.enabled, self.scc12_cnt, self.scc_live, CS.scc12,
                                                 CS.out.gasPressed, CS.out.brakePressed, CS.out.cruiseState.standstill, self.CP.carFingerprint))

        if self.frame % 20 == 0 and CS.has_scc13:
          can_sends.append(hyundaican.create_scc13(self.packer, CS.scc13))

        if CS.has_scc14:
          acc_standstill = stopping if CS.out.vEgo < 2. else False
          lead = self.scc_smoother.get_lead(controls.sm)

          if lead is not None:
            d = lead.dRel
            obj_gap = 1 if d < 25 else 2 if d < 40 else 3 if d < 60 else 4 if d < 80 else 0

          can_sends.append(hyundaican.create_scc14(self.packer, CC.enabled, CS.out.vEgo, acc_standstill, apply_accel,
                                                   CS.out.gasPressed, obj_gap, CS.scc14))
    else:
      self.scc12_cnt = -1


  def update_canfd(self, CC, CS, controls):
    actuators = CC.actuators

    # Steering Torque
    steer = actuators.steer
    new_steer = int(round(steer * self.CCP.STEER_MAX))
    apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, self.CCP)

    if not CC.latActive:
      apply_steer = 0

    self.apply_steer_last = apply_steer
    apply_steer = int(round(float(apply_steer)))

    # accel + longitudinal
    accel = clip(actuators.accel, self.CCP.ACCEL_MIN, self.CCP.ACCEL_MAX)
    stopping = actuators.longControlState == LongCtrlState.stopping
    set_speed_in_units = hud_control.setSpeed * (CV.MS_TO_KPH if CS.is_metric else CV.MS_TO_MPH)

    can_sends = []

    # tester present - w/ no response (keeps relevant ECU disabled)
    if self.frame % 100 == 0 and self.CP.openpilotLongitudinalControl:
      addr, bus = 0x7d0, 0
      if self.CP.flags & HyundaiFlags.CANFD_HDA2.value:
        addr, bus = 0x730, 5
      can_sends.append([addr, 0, b"\x02\x3E\x80\x00\x00\x00\x00\x00", bus])

    # >90 degree steering fault prevention
    # Count up to MAX_ANGLE_FRAMES, at which point we need to cut torque to avoid a steering fault
    if CC.latActive and abs(CS.out.steeringAngleDeg) >= MAX_ANGLE:
      self.angle_limit_counter += 1
    else:
      self.angle_limit_counter = 0

    # Cut steer actuation bit for two frames and hold torque with induced temporary fault
    torque_fault = CC.latActive and self.angle_limit_counter > MAX_ANGLE_FRAMES
    lat_active = CC.latActive and not torque_fault

    if self.angle_limit_counter >= MAX_ANGLE_FRAMES + MAX_ANGLE_CONSECUTIVE_FRAMES:
      self.angle_limit_counter = 0

    # CAN-FD platforms
    hda2 = self.CP.flags & HyundaiFlags.CANFD_HDA2
    hda2_long = hda2 and self.CP.openpilotLongitudinalControl

    # steering control
    can_sends.extend(hyundaicanfd.create_steering_messages(self.packer, self.CP, CC.enabled, CC.latActive, apply_steer))

    # disable LFA on HDA2 ( 676 )
    if self.frame % 5 == 0 and hda2:
      can_sends.append(hyundaicanfd.create_cam_0x2a4(self.packer, CS.cam_0x2a4))

    # LFA and HDA icons
    if self.frame % 5 == 0 and (not hda2 or hda2_long):
      can_sends.append(hyundaicanfd.create_lfahda_cluster(self.packer, self.CP, CC.enabled))

    if self.CP.openpilotLongitudinalControl:
      can_sends.extend(hyundaicanfd.create_adrv_messages(self.packer, self.frame))
      if self.frame % 2 == 0:
        can_sends.append(hyundaicanfd.create_acc_control(self.packer, self.CP, CC.enabled, accel, stopping, CC.cruiseControl.override,
                                                         set_speed_in_units))

    # button presses
    if (self.frame - self.last_button_frame) * DT_CTRL > 0.25:
      # cruise cancel
      if CC.cruiseControl.cancel:
        if self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
          can_sends.append(hyundaicanfd.create_acc_cancel(self.packer, self.CP, CS.cruise_info))
          self.last_button_frame = self.frame
        else:
          for _ in range(20):
            can_sends.append(hyundaicanfd.create_buttons(self.packer, CS.buttons_counter + 1, Buttons.CANCEL))
          self.last_button_frame = self.frame

      # cruise standstill resume
      elif CC.cruiseControl.resume:
        if not (self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS):
          for _ in range(20):
            can_sends.append(hyundaicanfd.create_buttons(self.packer, CS.buttons_counter + 1, Buttons.RES_ACCEL))
          self.last_button_frame = self.frame

    new_actuators = actuators.copy()
    new_actuators.steer = apply_steer / self.CCP.STEER_MAX
    new_actuators.accel = accel

    self.frame += 1
    return new_actuators, can_sends
