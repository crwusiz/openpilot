import random
import numpy as np

from cereal import car
from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from common.params import Params
from selfdrive.car.hyundai import hyundaican, hyundaicanfd
from selfdrive.car.hyundai.values import Buttons, CANFD_CAR
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, V_CRUISE_MIN
from selfdrive.controls.lib.lateral_planner import TRAJECTORY_SIZE
from selfdrive.road_speed_limiter import get_road_speed_limiter

SYNC_MARGIN = 3.
MIN_CURVE_SPEED = 30. * CV.KPH_TO_MS

AliveIndex = 0
ALIVE_COUNT = [8, 10]
WaitIndex = 0
WAIT_COUNT = [12, 14, 16, 18]

EventName = car.CarEvent.EventName

class SccSmoother:

  @staticmethod
  def get_alive_count():
    global AliveIndex
    count = ALIVE_COUNT[AliveIndex]
    AliveIndex += 1
    if AliveIndex >= len(ALIVE_COUNT):
      AliveIndex = 0
    return count


  @staticmethod
  def get_wait_count():
    global WaitIndex
    count = WAIT_COUNT[WaitIndex]
    WaitIndex += 1
    if WaitIndex >= len(WAIT_COUNT):
      WaitIndex = 0
    return count


  def kph_to_clu(self, kph):
    return int(kph * CV.KPH_TO_MS * self.speed_conv_to_clu)


  def __init__(self, CP):
    self.CP = CP
    self.btn = Buttons.NONE

    self.started_frame = 0
    self.wait_timer = 0
    self.alive_timer = 0
    self.target_speed = 0.
    self.max_speed_clu = 0.
    self.curve_speed_ms = 0.

    self.alive_count = ALIVE_COUNT
    random.shuffle(WAIT_COUNT)

    self.slowing_down = False
    self.slowing_down_alert = False
    self.slowing_down_sound_alert = False
    self.over_speed_limit = False
    self.limited_lead = False

    self.is_metric = Params().get_bool("IsMetric")
    self.long_control = CP.openpilotLongitudinalControl
    self.can_fd = CP.carFingerprint in CANFD_CAR
    self.scc_bus = CP.sccBus

    self.speed_conv_to_ms = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS
    self.speed_conv_to_clu = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH

    self.min_set_speed_clu = self.kph_to_clu(V_CRUISE_MIN)
    self.max_set_speed_clu = self.kph_to_clu(V_CRUISE_MAX)


  def reset(self):
    self.btn = Buttons.NONE

    self.wait_timer = 0
    self.alive_timer = 0
    self.target_speed = 0.
    self.max_speed_clu = 0.
    self.curve_speed_ms = 0.

    self.slowing_down = False
    self.slowing_down_alert = False
    self.slowing_down_sound_alert = False


  def is_active(self, frame):
    return frame - self.started_frame <= max(ALIVE_COUNT) + max(WAIT_COUNT)


  def inject_events(self, events):
    if self.slowing_down_sound_alert:
      self.slowing_down_sound_alert = False
      events.add(EventName.slowingDownSpeedSound)
    elif self.slowing_down_alert:
      events.add(EventName.slowingDownSpeed)


  def cal_max_speed(self, frame, CS, sm, clu_speed, controls):
    # kph
    road_speed_limiter = get_road_speed_limiter()
    apply_limit_speed, road_limit_speed, left_dist, first_started = road_speed_limiter.get_max_speed(clu_speed, self.is_metric)

    self.cal_curve_speed(sm, CS.out.vEgo, frame)
    if self.curve_speed_ms >= MIN_CURVE_SPEED:
      max_speed_clu = min(controls.v_cruise_helper.v_cruise_kph * CV.KPH_TO_MS, self.curve_speed_ms) * self.speed_conv_to_clu
    else:
      max_speed_clu = self.kph_to_clu(controls.v_cruise_helper.v_cruise_kph)

    if road_speed_limiter.roadLimitSpeed is not None:
      camSpeedFactor = clip(road_speed_limiter.roadLimitSpeed.camSpeedFactor, 1.0, 1.1)
      self.over_speed_limit = \
        road_speed_limiter.roadLimitSpeed.camLimitSpeedLeftDist > 0 and 0 < road_limit_speed * camSpeedFactor < clu_speed + 2
    else:
      self.over_speed_limit = False

    if apply_limit_speed >= self.kph_to_clu(10):
      if first_started:
        self.max_speed_clu = clu_speed

      max_speed_clu = min(max_speed_clu, apply_limit_speed)

      if clu_speed > apply_limit_speed:
        if not self.slowing_down_alert and not self.slowing_down:
          self.slowing_down_sound_alert = True
          self.slowing_down = True
        self.slowing_down_alert = True
      else:
        self.slowing_down_alert = False
    else:
      self.slowing_down_alert = False
      self.slowing_down = False

    lead_speed = self.get_long_lead_speed(clu_speed, sm)
    if lead_speed >= self.min_set_speed_clu:
      if lead_speed < max_speed_clu:
        max_speed_clu = min(max_speed_clu, lead_speed)
        if not self.limited_lead:
          self.max_speed_clu = clu_speed + 3.
          self.limited_lead = True
    else:
      self.limited_lead = False

    max_speed = int(round(max_speed_clu))
    if not self.long_control or self.max_speed_clu <= 0:
      self.max_speed_clu = max_speed
    else:
      kp = 0.01
      error = max_speed - self.max_speed_clu
      self.max_speed_clu = self.max_speed_clu + error * kp

    return road_limit_speed, left_dist


  def update(self, enabled, can_sends, packer, CC, CS, frame, controls):
    clu_speed = CS.out.vEgoCluster * self.speed_conv_to_clu
    cruise_speed = CS.out.cruiseState.speed

    road_limit_speed, left_dist = self.cal_max_speed(frame, CS, controls.sm, clu_speed, controls)

    controls.applyMaxSpeed = float(clip(cruise_speed * CV.MS_TO_KPH, V_CRUISE_MIN,
                                        self.max_speed_clu * self.speed_conv_to_ms * CV.MS_TO_KPH))

    ascc_enabled = enabled and CS.out.cruiseState.enabled and 1 < cruise_speed < 255 and not CS.out.brakePressed
    if not self.long_control:
      if not ascc_enabled or CS.out.cruiseState.standstill or CS.cruise_buttons[-1] != Buttons.NONE:
        self.reset()
        self.wait_timer = max(ALIVE_COUNT) + max(WAIT_COUNT)
        return

    if not ascc_enabled:
      self.reset()

    self.cal_target_speed(CS, clu_speed, controls)

    if self.wait_timer > 0:
      self.wait_timer -= 1
    elif ascc_enabled and not CS.out.cruiseState.standstill:
      if self.alive_timer == 0:
        self.btn = self.get_button(cruise_speed * self.speed_conv_to_clu)
        self.alive_count = SccSmoother.get_alive_count()
      if self.btn != Buttons.NONE:

        if self.can_fd:
          can_sends.append(hyundaicanfd.create_buttons(packer, self.CP, CS.buttons_counter + 1, self.btn))
        else:
          can_sends.append(hyundaican.create_clu11(packer, frame, self.scc_bus, self.btn, clu_speed, CS.clu11))

        if self.alive_timer == 0:
          self.started_frame = frame
        self.alive_timer += 1
        if self.alive_timer >= self.alive_count:
          self.alive_timer = 0
          self.wait_timer = SccSmoother.get_wait_count()
          self.btn = Buttons.NONE
      else:
        if self.long_control and self.target_speed >= self.min_set_speed_clu:
          self.target_speed = 0.
    else:
      if self.long_control:
        self.target_speed = 0.


  def get_button(self, current_set_speed):
    if self.target_speed < self.min_set_speed_clu:
      return Buttons.NONE

    error = self.target_speed - current_set_speed
    if abs(error) < 0.9:
      return Buttons.NONE

    return Buttons.RES_ACCEL if error > 0 else Buttons.SET_DECEL


  def get_lead(self, sm):
    radar = sm['radarState']
    if radar.leadOne.status:
      return radar.leadOne
    return None


  def get_long_lead_speed(self, clu_speed, sm):
    if self.long_control:
      lead = self.get_lead(sm)
      if lead is not None:
        d = lead.dRel - 5.
        if 0. < d < -lead.vRel * (9. + 3.) * 2. and lead.vRel < -1.:
          t = d / lead.vRel
          accel = -(lead.vRel / t) * self.speed_conv_to_clu
          accel *= 1.2
          if accel < 0.:
            target_speed = clu_speed + accel
            target_speed = max(target_speed, self.min_set_speed_clu)
            return target_speed
    return 0


  def cal_curve_speed(self, sm, v_ego, frame):
    if frame % 20 == 0:
      md = sm['modelV2']
      if len(md.position.x) == TRAJECTORY_SIZE and len(md.position.y) == TRAJECTORY_SIZE:
        x = md.position.x
        y = md.position.y
        dy = np.gradient(y, x)
        d2y = np.gradient(dy, x)
        curv = d2y / (1 + dy ** 2) ** 1.5

        start = int(interp(v_ego, [10., 27.], [10, TRAJECTORY_SIZE-10]))
        curv = curv[start:min(start+10, TRAJECTORY_SIZE)]
        a_y_max = 2.975 - v_ego * 0.0375  # ~1.85 @ 75mph, ~2.6 @ 25mph
        v_curvature = np.sqrt(a_y_max / np.clip(np.abs(curv), 1e-4, None))
        model_speed = np.mean(v_curvature) * 0.85

        if model_speed < v_ego:
          self.curve_speed_ms = float(max(model_speed, MIN_CURVE_SPEED))
        else:
          self.curve_speed_ms = 255.

        if np.isnan(self.curve_speed_ms):
          self.curve_speed_ms = 255.
      else:
        self.curve_speed_ms = 255.


  def cal_target_speed(self, CS, clu_speed, controls):
    if not self.long_control:
      if CS.out.gasPressed and CS.cruise_buttons[-1] == Buttons.NONE:
        if clu_speed + SYNC_MARGIN > self.kph_to_clu(controls.v_cruise_helper.v_cruise_kph):
          set_speed = clip(clu_speed + SYNC_MARGIN, self.min_set_speed_clu, self.max_set_speed_clu)
          controls.v_cruise_helper.v_cruise_kph = set_speed * self.speed_conv_to_ms * CV.MS_TO_KPH
      self.target_speed = self.kph_to_clu(controls.v_cruise_helper.v_cruise_kph)

      if self.max_speed_clu > self.min_set_speed_clu:
        self.target_speed = clip(self.target_speed, self.min_set_speed_clu, self.max_speed_clu)

    elif CS.out.cruiseState.enabled:
      if CS.out.gasPressed and CS.cruise_buttons[-1] == Buttons.NONE:
        if clu_speed + SYNC_MARGIN > self.kph_to_clu(controls.v_cruise_helper.v_cruise_kph):
          set_speed = clip(clu_speed + SYNC_MARGIN, self.min_set_speed_clu, self.max_set_speed_clu)
          self.target_speed = set_speed