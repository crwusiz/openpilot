
from cereal import car
from common.numpy_fast import clip
from selfdrive.car import create_button_event
from common.conversions import Conversions as CV
from common.params import Params
from selfdrive.car.hyundai.values import CANFD_CAR
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MIN, V_CRUISE_MAX, V_CRUISE_ENABLE_MIN

V_CRUISE_DELTA_MI = 5 * CV.MPH_TO_KPH
V_CRUISE_DELTA_KM = 10

ButtonType = car.CarState.ButtonEvent.Type


def is_radar_disabler(CP):
  return CP.carFingerprint in CANFD_CAR or (CP.openpilotLongitudinalControl and CP.sccBus == 0)


class CruiseStateManager:
  __instance = None

  @classmethod
  def __getInstance(cls):
    return cls.__instance


  @classmethod
  def instance(cls):
    cls.__instance = cls()
    cls.instance = cls.__getInstance
    return cls.__instance


  def __init__(self):
    self.available = False
    self.enabled = False
    self.speed = V_CRUISE_ENABLE_MIN * CV.KPH_TO_MS
    self.gapAdjust = 4

    self.prev_speed = 0
    self.prev_main_buttons = 0
    self.prev_cruise_button = 0
    self.button_events = None

    self.prev_btn = ButtonType.unknown
    self.btn_count = 0
    self.btn_long_pressed = False
    self.is_cruise_enabled = False

    self.prev_brake_pressed = False

    self.is_metric = Params().get_bool('IsMetric')
    self.cruise_state_control = Params().get_bool('CruiseStateControl')


  def is_resume_spam_allowed(self, CP):
    if is_radar_disabler(CP):
      return False
    return not self.cruise_state_control


  def is_set_speed_spam_allowed(self, CP):
    return self.is_resume_spam_allowed(CP)


  def update(self, CS, main_buttons, cruise_buttons, buttons_dict, available=-1, cruise_state_control=True):
    # CS - CarState cereal message
    if available >= 0:
      self.available = available
    elif main_buttons[-1] != self.prev_main_buttons and main_buttons[-1]:
      self.available = not self.available

    self.prev_main_buttons = main_buttons[-1]

    cruise_button = cruise_buttons[-1]
    if cruise_button != self.prev_cruise_button:
      self.button_events = [create_button_event(cruise_button, self.prev_cruise_button, buttons_dict)]
      if cruise_button != 0 and self.prev_cruise_button != 0:
        self.button_events.append(create_button_event(0, self.prev_cruise_button, buttons_dict))
        self.prev_cruise_button = 0
      else:
        self.prev_cruise_button = cruise_button

    button = self.update_buttons()
    if button != ButtonType.unknown:
      self.update_cruise_state(CS, int(round(self.speed * CV.MS_TO_KPH)), button)

    if not self.available:
      self.enabled = False

    if self.prev_brake_pressed != CS.brakePressed and CS.brakePressed:
      self.enabled = False
    self.prev_brake_pressed = CS.brakePressed

    CS.cruiseState.available = self.available

    if cruise_state_control:
      CS.cruiseState.enabled = self.enabled
      CS.cruiseState.standstill = False
      CS.cruiseState.speed = self.speed
      CS.cruiseState.gapAdjust = self.gapAdjust


  def update_buttons(self):
    if self.button_events is None:
      return ButtonType.unknown

    btn = ButtonType.unknown

    if self.btn_count > 0:
      self.btn_count += 1

    for b in self.button_events:
      if b.pressed and self.btn_count == 0 \
          and (
          b.type == ButtonType.accelCruise
          or b.type == ButtonType.decelCruise
          or b.type == ButtonType.gapAdjustCruise
          or b.type == ButtonType.cancel
      ):
        self.btn_count = 1
        self.prev_btn = b.type

      elif not b.pressed and self.btn_count > 0:
        if not self.btn_long_pressed:
          btn = b.type
        self.btn_long_pressed = False
        self.btn_count = 0

    if self.btn_count > 70:
      self.btn_long_pressed = True
      btn = self.prev_btn
      self.btn_count %= 70

    return btn


  def update_cruise_state(self, CS, v_cruise_kph, btn):
    if self.enabled:
      if not self.btn_long_pressed:
        if btn == ButtonType.accelCruise:
          v_cruise_kph += 1 if self.is_metric else 1 * CV.MPH_TO_KPH
        elif btn == ButtonType.decelCruise:
          v_cruise_kph -= 1 if self.is_metric else 1 * CV.MPH_TO_KPH
      else:
        v_cruise_delta = V_CRUISE_DELTA_KM if self.is_metric else V_CRUISE_DELTA_MI
        if btn == ButtonType.accelCruise:
          v_cruise_kph += v_cruise_delta - v_cruise_kph % v_cruise_delta
        elif btn == ButtonType.decelCruise:
          v_cruise_kph -= v_cruise_delta - -v_cruise_kph % v_cruise_delta
    else:
      if not self.btn_long_pressed:
        if btn == ButtonType.decelCruise and not self.enabled:
          self.enabled = True
          v_cruise_kph = CS.vEgoCluster * CV.MS_TO_KPH
          if CS.vEgoCluster < 0.1:
            v_cruise_kph = clip(round(v_cruise_kph, 1), V_CRUISE_ENABLE_MIN, V_CRUISE_MAX)
          else:
            v_cruise_kph = clip(round(v_cruise_kph, 1), V_CRUISE_MIN, V_CRUISE_MAX)
        elif btn == ButtonType.accelCruise and not self.enabled:
          self.enabled = True
          v_cruise_kph = clip(round(self.speed * CV.MS_TO_KPH, 1), V_CRUISE_ENABLE_MIN, V_CRUISE_MAX)

    if btn == ButtonType.gapAdjustCruise and not self.btn_long_pressed:
      self.gapAdjust += 1
      if self.gapAdjust > 4:
        self.gapAdjust = 1

    if btn == ButtonType.cancel:
      self.enabled = False

    v_cruise_kph = clip(round(v_cruise_kph, 1), V_CRUISE_MIN, V_CRUISE_MAX)
    self.speed = v_cruise_kph * CV.KPH_TO_MS
