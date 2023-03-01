from random import randint

from cereal import car
from common.params import Params
from common.conversions import Conversions as CV
from common.numpy_fast import clip
from common.realtime import DT_CTRL
from opendbc.can.packer import CANPacker
from selfdrive.car import apply_driver_steer_torque_limits
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
    self.packer = CANPacker(dbc_name)
    self.scc_smoother = SccSmoother(CP)
    self.car_fingerprint = CP.carFingerprint

    self.frame = 0
    self.last_button_frame = 0
    self.accel_last = 0
    self.apply_steer_last = 0
    self.angle_limit_counter = 0
    self.resume_cnt = 0
    self.resume_wait_timer = 0
    self.last_lead_distance = 0
    self.turning_signal_timer = 0
    self.turning_indicator_alert = False

  def update(self, CC, CS, now_nanos, controls):
    actuators = CC.actuators
    hud_control = CC.hudControl

    # Steering Torque
    new_steer = int(round(actuators.steer * self.CCP.STEER_MAX))
    apply_steer = apply_driver_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, self.CCP)

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

    # accel + longitudinal
    accel = clip(actuators.accel, self.CCP.ACCEL_MIN, self.CCP.ACCEL_MAX)
    stopping = actuators.longControlState == LongCtrlState.stopping
    set_speed_in_units = hud_control.setSpeed * (CV.MS_TO_KPH if CS.is_metric else CV.MS_TO_MPH)

    # HUD messages
    sys_warning, sys_state, left_lane_warning, right_lane_warning = process_hud_alert(CC.enabled, self.car_fingerprint, hud_control)

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

    # CAN-FD platforms
    if self.CP.carFingerprint in CANFD_CAR:
      hda2 = self.CP.flags & HyundaiFlags.CANFD_HDA2
      hda2_long = hda2 and self.CP.openpilotLongitudinalControl

      # tester present - w/ no response (keeps relevant ECU disabled)
      if self.frame % 100 == 0 and not (self.CP.flags & HyundaiFlags.CANFD_CAMERA_SCC.value) and self.CP.openpilotLongitudinalControl:
        # for longitudinal control, either radar or ADAS driving ECU
        addr, bus = 0x7d0, 0
        if self.CP.flags & HyundaiFlags.CANFD_HDA2.value:
          addr, bus = 0x730, 5
        can_sends.append([addr, 0, b"\x02\x3E\x80\x00\x00\x00\x00\x00", bus])

        # for blinkers
        if self.CP.flags & HyundaiFlags.ENABLE_BLINKERS:
          can_sends.append([0x7b1, 0, b"\x02\x3E\x80\x00\x00\x00\x00\x00", 5])

      # steering control
      can_sends.extend(hyundaicanfd.create_steering_messages(self.packer, self.CP, CC.enabled, lat_active, apply_steer))

      # disable LFA on HDA2 ( 676 )
      if self.frame % 5 == 0 and hda2:
        can_sends.append(hyundaicanfd.create_cam_0x2a4(self.packer, CS.cam_0x2a4))

      # LFA and HDA icons
      if self.frame % 5 == 0 and (not hda2 or hda2_long):
        can_sends.append(hyundaicanfd.create_lfahda_cluster(self.packer, self.CP, CC.enabled, CC.latActive, CS.madsEnabled))

      # blinkers
      if hda2 and self.CP.flags & HyundaiFlags.ENABLE_BLINKERS:
        can_sends.extend(hyundaicanfd.create_spas_messages(self.packer, self.frame, CC.leftBlinker, CC.rightBlinker))

      if self.CP.openpilotLongitudinalControl:
        if hda2:
          can_sends.extend(hyundaicanfd.create_adrv_messages(self.packer, self.frame))
        if self.frame % 2 == 0:
          can_sends.append(hyundaicanfd.create_acc_control(self.packer, self.CP, CC.enabled, self.accel_last, accel, stopping, CC.cruiseControl.override,
                                                           set_speed_in_units))
          self.accel_last = accel
      else:
        # button presses
        if (self.frame - self.last_button_frame) * DT_CTRL > 0.25:
          # cruise cancel
          if CC.cruiseControl.cancel:
            if self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
              can_sends.append(hyundaicanfd.create_acc_cancel(self.packer, self.CP, CS.cruise_info))
              self.last_button_frame = self.frame
            else:
              for _ in range(20):
                can_sends.append(hyundaicanfd.create_buttons(self.packer, self.CP, CS.buttons_counter+1, Buttons.CANCEL))
              self.last_button_frame = self.frame

          # cruise standstill resume
          elif CC.cruiseControl.resume:
            if self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
              # TODO: resume for alt button cars
              pass
            else:
              for _ in range(20):
                can_sends.append(hyundaicanfd.create_buttons(self.packer, self.CP, CS.buttons_counter+1, Buttons.RES_ACCEL))
              self.last_button_frame = self.frame
    else:
      # send lkas11 bus 0
      can_sends.append(hyundaican.create_lkas11(self.packer, self.frame, self.CP.carFingerprint, apply_steer, lat_active, torque_fault, sys_warning, sys_state,
                                                CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible, left_lane_warning, right_lane_warning,
                                                0, CS.lkas11))

      clu11_speed = CS.clu11["CF_Clu_Vanz"]
      panda_safety_select = Params().get_bool("PandaSafetySelect")
      if panda_safety_select and self.CP.epsBus:
        # mdps enabled speed message
        enabled_speed = 60 if CS.is_metric else 38
        if clu11_speed > enabled_speed or not CC.latActive:
          enabled_speed = clu11_speed

        # send lkas11 bus 1
        can_sends.append(hyundaican.create_lkas11(self.packer, self.frame, self.CP.carFingerprint, apply_steer, lat_active, torque_fault, sys_warning, sys_state,
                                                  CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible, left_lane_warning, right_lane_warning,
                                                  1, CS.lkas11))

        # send clu11 to eps
        can_sends.append(hyundaican.create_clu11_mdps(self.packer, self.frame, self.CP.epsBus, Buttons.NONE, enabled_speed, CS.clu11))

        # send mdps12 to LKAS to prevent LKAS error
        can_sends.append(hyundaican.create_mdps12(self.packer, self.frame, CS.mdps12))

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
          if panda_safety_select == "1" and self.CP.epsBus:
            can_sends.append(hyundaican.create_clu11_mdps(self.packer, self.frame, self.CP.sccBus, Buttons.RES_ACCEL, clu11_speed, CS.clu11))
          else:
            can_sends.append(hyundaican.create_clu11(self.packer, self.frame, self.CP.sccBus, Buttons.RES_ACCEL, CS.clu11))
          self.resume_cnt += 1
          if self.resume_cnt >= int(randint(4, 5) * 2):
            self.resume_cnt = 0
            self.resume_wait_timer = int(randint(20, 25) * 2)
      elif self.last_lead_distance != 0:
        self.last_lead_distance = 0

      # scc smoother
      self.scc_smoother.update(CC.enabled, can_sends, self.packer, CC, CS, self.frame, controls)

      # send scc to car if longcontrol enabled and SCC not on bus 0 or not live
      if self.frame % 2 == 0 and self.CP.openpilotLongitudinalControl and CS.out.cruiseState.enabled:
        jerk = 3.0 if actuators.longControlState == LongCtrlState.pid else 1.0
        can_sends.extend(hyundaican.create_scc_commands(self.packer, int(self.frame / 2), CC.enabled and CC.longActive, accel, jerk,
                                                        hud_control.leadVisible, set_speed_in_units, stopping, CC.cruiseControl.override, CS))

      if self.frame % 500 == 0:
        print(f'scc11 = {bool(CS.scc11)}  scc12 = {bool(CS.scc12)}  scc13 = {bool(CS.scc13)}  scc14 = {bool(CS.scc14)}')

      # 20 Hz LFA MFA message
      if self.frame % 5 == 0:
        activated_hda = road_speed_limiter_get_active()  # 0 off, 1 main road, 2 highway
        if self.CP.flags & HyundaiFlags.SEND_LFA.value:
          can_sends.append(hyundaican.create_lfahda_mfc(self.packer, CC.enabled, activated_hda))

      # 5 Hz ACC options
      #if self.frame % 20 == 0 and self.CP.openpilotLongitudinalControl:
      #  can_sends.extend(hyundaican.create_acc_opt(self.packer))

      # 2 Hz front radar options
      #if self.frame % 50 == 0 and self.CP.openpilotLongitudinalControl:
      #  can_sends.append(hyundaican.create_frt_radar_opt(self.packer))

    new_actuators = actuators.copy()
    new_actuators.steer = apply_steer / self.CCP.STEER_MAX
    new_actuators.steerOutputCan = apply_steer
    new_actuators.accel = accel

    self.frame += 1
    return new_actuators, can_sends
