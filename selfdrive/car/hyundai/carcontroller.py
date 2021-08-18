
from cereal import car
from common.realtime import DT_CTRL
from common.numpy_fast import clip
from selfdrive.car import apply_std_steer_torque_limits
from selfdrive.car.hyundai.hyundaican import create_lkas11, create_clu11, \
  create_scc11, create_scc12, create_scc13, create_scc14, \
  create_mdps12, create_lfahda_mfc, create_hda_mfc
from selfdrive.car.hyundai.scc_smoother import SccSmoother
from selfdrive.car.hyundai.values import Buttons, CAR, FEATURES, CarControllerParams
from opendbc.can.packer import CANPacker
from selfdrive.config import Conversions as CV
from common.params import Params
from selfdrive.controls.lib.longcontrol import LongCtrlState
from selfdrive.road_speed_limiter import road_speed_limiter_get_active

VisualAlert = car.CarControl.HUDControl.VisualAlert
min_set_speed = 30 * CV.KPH_TO_MS


def accel_hysteresis(accel, accel_steady):
  # for small accel oscillations within ACCEL_HYST_GAP, don't change the accel command
  if accel > accel_steady + CarControllerParams.ACCEL_HYST_GAP:
    accel_steady = accel - CarControllerParams.ACCEL_HYST_GAP
  elif accel < accel_steady - CarControllerParams.ACCEL_HYST_GAP:
    accel_steady = accel + CarControllerParams.ACCEL_HYST_GAP
  accel = accel_steady

  return accel, accel_steady


SP_CARS = [CAR.GENESIS, CAR.GENESIS_G70, CAR.GENESIS_G80,
           CAR.GENESIS_EQ900, CAR.GENESIS_EQ900_L, CAR.K9, CAR.GENESIS_G90]

def process_hud_alert(enabled, fingerprint, visual_alert, left_lane, right_lane,
                      left_lane_depart, right_lane_depart):

  sys_warning = (visual_alert in [VisualAlert.steerRequired, VisualAlert.ldw])

  # initialize to no line visible
  sys_state = 1
  if left_lane and right_lane or sys_warning:  # HUD alert only display when LKAS status is active
    sys_state = 3 if enabled or sys_warning else 4
  elif left_lane:
    sys_state = 5
  elif right_lane:
    sys_state = 6

  # initialize to no warnings
  left_lane_warning = 0
  right_lane_warning = 0
  if left_lane_depart:
    left_lane_warning = 1 if fingerprint in SP_CARS else 2
  if right_lane_depart:
    right_lane_warning = 1 if fingerprint in SP_CARS else 2

  return sys_warning, sys_state, left_lane_warning, right_lane_warning


class CarController():
  def __init__(self, dbc_name, CP, VM):
    self.car_fingerprint = CP.carFingerprint
    self.packer = CANPacker(dbc_name)
    self.accel_steady = 0
    self.apply_steer_last = 0
    self.steer_rate_limited = False
    self.lkas11_cnt = 0
    self.scc12_cnt = 0

    self.resume_cnt = 0
    self.last_lead_distance = 0
    self.resume_wait_timer = 0

    self.turning_signal_timer = 0
    self.longcontrol = CP.openpilotLongitudinalControl
    self.scc_live = not CP.radarOffCan

    self.mad_mode_enabled = Params().get_bool('MadModeEnabled')
    self.ldws_opt = Params().get_bool('IsLdwsCar')
    self.stock_navi_decel_enabled = Params().get_bool('StockNaviDecelEnabled')

    # gas_factor, brake_factor
    # Adjust it in the range of 0.7 to 1.3
    self.scc_smoother = SccSmoother()

  def update(self, enabled, CS, frame, CC, actuators, pcm_cancel_cmd, visual_alert,
             left_lane, right_lane, left_lane_depart, right_lane_depart, set_speed, lead_visible, controls):

    # *** compute control surfaces ***

    # gas and brake
    apply_accel = actuators.gas - actuators.brake
    apply_accel, self.accel_steady = accel_hysteresis(apply_accel, self.accel_steady)
    apply_accel = self.scc_smoother.get_accel(CS, controls.sm, apply_accel)
    apply_accel = clip(apply_accel * CarControllerParams.ACCEL_SCALE,
                       CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX)

    # Steering Torque
    new_steer = int(round(actuators.steer * CarControllerParams.STEER_MAX))
    apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque,
                                                CarControllerParams)

    self.steer_rate_limited = new_steer != apply_steer

    # disable if steer angle reach 90 deg, otherwise mdps fault in some models
    lkas_active = enabled and abs(CS.out.steeringAngleDeg) < CS.CP.maxSteeringAngleDeg

    # fix for Genesis hard fault at low speed
    if CS.out.vEgo < 60 * CV.KPH_TO_MS and self.car_fingerprint == CAR.GENESIS and not CS.mdps_bus:
      lkas_active = False

    # Disable steering while turning blinker on and speed below 60 kph
    if CS.out.leftBlinker or CS.out.rightBlinker:
      self.turning_signal_timer = 0.5 / DT_CTRL  # Disable for 0.5 Seconds after blinker turned off
    if self.turning_indicator_alert: # set and clear by interface
      lkas_active = 0
    if self.turning_signal_timer > 0:
      self.turning_signal_timer -= 1

    if not lkas_active:
      apply_steer = 0

    self.apply_accel_last = apply_accel
    self.apply_steer_last = apply_steer

    sys_warning, sys_state, left_lane_warning, right_lane_warning = \
      process_hud_alert(enabled, self.car_fingerprint, visual_alert,
                        left_lane, right_lane, left_lane_depart, right_lane_depart)

    clu11_speed = CS.clu11["CF_Clu_Vanz"]
    enabled_speed = 38 if CS.is_set_speed_in_mph else 60
    if clu11_speed > enabled_speed or not lkas_active:
      enabled_speed = clu11_speed

    controls.clu_speed_ms = clu11_speed * CS.speed_conv_to_ms

    if not (min_set_speed < set_speed < 255 * CV.KPH_TO_MS):
      set_speed = min_set_speed
    set_speed *= CV.MS_TO_MPH if CS.is_set_speed_in_mph else CV.MS_TO_KPH

    if frame == 0:  # initialize counts from last received count signals
      self.lkas11_cnt = CS.lkas11["CF_Lkas_MsgCount"]
      self.scc12_cnt = CS.scc12["CR_VSM_Alive"] + 1 if not CS.no_radar else 0

      # TODO: fix this
      # self.prev_scc_cnt = CS.scc11["AliveCounterACC"]
      # self.scc_update_frame = frame

    # check if SCC is alive
    # if frame % 7 == 0:
    # if CS.scc11["AliveCounterACC"] == self.prev_scc_cnt:
    # if frame - self.scc_update_frame > 20 and self.scc_live:
    # self.scc_live = False
    # else:
    # self.scc_live = True
    # self.prev_scc_cnt = CS.scc11["AliveCounterACC"]
    # self.scc_update_frame = frame

    self.prev_scc_cnt = CS.scc11["AliveCounterACC"]

    self.lkas11_cnt = (self.lkas11_cnt + 1) % 0x10
    self.scc12_cnt %= 0xF

    can_sends = []
    can_sends.append(create_lkas11(self.packer, frame, self.car_fingerprint, apply_steer, lkas_active,
                                   CS.lkas11, sys_warning, sys_state, enabled, left_lane, right_lane,
                                   left_lane_warning, right_lane_warning, 0, self.ldws_opt))

    if CS.mdps_bus or CS.scc_bus == 1:  # send lkas11 bus 1 if mdps or scc is on bus 1
      can_sends.append(create_lkas11(self.packer, frame, self.car_fingerprint, apply_steer, lkas_active,
                                     CS.lkas11, sys_warning, sys_state, enabled, left_lane, right_lane,
                                     left_lane_warning, right_lane_warning, 1, self.ldws_opt))

    if frame % 2 and CS.mdps_bus: # send clu11 to mdps if it is not on bus 0
      can_sends.append(create_clu11(self.packer, frame // 2 % 0x10, CS.mdps_bus, CS.clu11, Buttons.NONE, enabled_speed))

    if pcm_cancel_cmd and (self.longcontrol and not self.mad_mode_enabled):
      can_sends.append(create_clu11(self.packer, frame % 0x10, CS.scc_bus, CS.clu11, Buttons.CANCEL, clu11_speed))
    else:
      can_sends.append(create_mdps12(self.packer, frame, CS.mdps12))

    # fix auto resume - by neokii
    if CS.out.cruiseState.standstill and not CS.out.gasPressed:

      if self.last_lead_distance == 0:
        self.last_lead_distance = CS.lead_distance
        self.resume_cnt = 0
        self.resume_wait_timer = 0

      # scc smoother
      elif self.scc_smoother.is_active(frame):
        pass

      elif self.resume_wait_timer > 0:
        self.resume_wait_timer -= 1

      elif abs(CS.lead_distance - self.last_lead_distance) > 0.01:
        can_sends.append(create_clu11(self.packer, self.resume_cnt, CS.scc_bus, CS.clu11, Buttons.RES_ACCEL, clu11_speed))
        self.resume_cnt += 1

        if self.resume_cnt >= 8:
          self.resume_cnt = 0
          self.resume_wait_timer = SccSmoother.get_wait_count() * 2

    # reset lead distnce after the car starts moving
    elif self.last_lead_distance != 0:
      self.last_lead_distance = 0

    # scc smoother
    self.scc_smoother.update(enabled, can_sends, self.packer, CC, CS, frame, apply_accel, controls)

    controls.apply_accel = apply_accel
    aReqValue = CS.scc12["aReqValue"]
    controls.aReqValue = aReqValue

    if aReqValue < controls.aReqValueMin:
      controls.aReqValueMin = controls.aReqValue

    if aReqValue > controls.aReqValueMax:
      controls.aReqValueMax = controls.aReqValue

    # send scc to car if longcontrol enabled and SCC not on bus 0 or ont live
    if self.longcontrol and CS.cruiseState_enabled and (CS.scc_bus or not self.scc_live) and frame % 2 == 0:

      if self.stock_navi_decel_enabled:
        controls.sccStockCamAct = CS.scc11["Navi_SCC_Camera_Act"]
        controls.sccStockCamStatus = CS.scc11["Navi_SCC_Camera_Status"]
        apply_accel, stock_cam = self.scc_smoother.get_stock_cam_accel(apply_accel, aReqValue, CS.scc11)
      else:
        controls.sccStockCamAct = 0
        controls.sccStockCamStatus = 0
        stock_cam = False

      can_sends.append(create_scc12(self.packer, apply_accel, enabled, self.scc12_cnt, self.scc_live, CS.scc12))
      can_sends.append(create_scc11(self.packer, frame, enabled, set_speed, lead_visible, self.scc_live, CS.scc11,
                                    self.scc_smoother.active_cam, stock_cam))

      if frame % 20 == 0 and CS.has_scc13:
        can_sends.append(create_scc13(self.packer, CS.scc13))
      if CS.has_scc14:
        if CS.out.vEgo < 2.:
          long_control_state = controls.LoC.long_control_state
          acc_standstill = True if long_control_state == LongCtrlState.stopping else False
        else:
          acc_standstill = False

        lead = self.scc_smoother.get_lead(controls.sm)

        if lead is not None:
          d = lead.dRel
          obj_gap = 1 if d < 25 else 2 if d < 40 else 3 if d < 60 else 4 if d < 80 else 5
        else:
          obj_gap = 0

        can_sends.append(create_scc14(self.packer, enabled, CS.out.vEgo, acc_standstill, apply_accel, CS.out.gasPressed,
                                      obj_gap, CS.scc14))
      self.scc12_cnt += 1

    # 20 Hz LFA MFA message
    if frame % 5 == 0:
      activated_hda = road_speed_limiter_get_active()
      # activated_hda: 0 - off, 1 - main road, 2 - highway
      if self.car_fingerprint in FEATURES["send_lfa_mfa"]:
        can_sends.append(create_lfahda_mfc(self.packer, enabled, activated_hda))
      elif CS.mdps_bus == 0:
        state = 2 if self.car_fingerprint in FEATURES["send_hda_state_2"] else 1
        can_sends.append(create_hda_mfc(self.packer, activated_hda, state))

    return can_sends
