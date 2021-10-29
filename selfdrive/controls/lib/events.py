from enum import IntEnum
from typing import Dict, Union, Callable, Any

from cereal import log, car
import cereal.messaging as messaging
from common.realtime import DT_CTRL
from selfdrive.config import Conversions as CV
from selfdrive.locationd.calibrationd import MIN_SPEED_FILTER

AlertSize = log.ControlsState.AlertSize
AlertStatus = log.ControlsState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert
EventName = car.CarEvent.EventName


# Alert priorities
class Priority(IntEnum):
  LOWEST = 0
  LOWER = 1
  LOW = 2
  MID = 3
  HIGH = 4
  HIGHEST = 5


# Event types
class ET:
  ENABLE = 'enable'
  PRE_ENABLE = 'preEnable'
  NO_ENTRY = 'noEntry'
  WARNING = 'warning'
  USER_DISABLE = 'userDisable'
  SOFT_DISABLE = 'softDisable'
  IMMEDIATE_DISABLE = 'immediateDisable'
  PERMANENT = 'permanent'


# get event name from enum
EVENT_NAME = {v: k for k, v in EventName.schema.enumerants.items()}


class Events:
  def __init__(self):
    self.events = []
    self.static_events = []
    self.events_prev = dict.fromkeys(EVENTS.keys(), 0)

  @property
  def names(self):
    return self.events

  def __len__(self):
    return len(self.events)

  def add(self, event_name, static=False):
    if static:
      self.static_events.append(event_name)
    self.events.append(event_name)

  def clear(self):
    self.events_prev = {k: (v + 1 if k in self.events else 0) for k, v in self.events_prev.items()}
    self.events = self.static_events.copy()

  def any(self, event_type):
    for e in self.events:
      if event_type in EVENTS.get(e, {}).keys():
        return True
    return False

  def create_alerts(self, event_types, callback_args=None):
    if callback_args is None:
      callback_args = []

    ret = []
    for e in self.events:
      types = EVENTS[e].keys()
      for et in event_types:
        if et in types:
          alert = EVENTS[e][et]
          if not isinstance(alert, Alert):
            alert = alert(*callback_args)

          if DT_CTRL * (self.events_prev[e] + 1) >= alert.creation_delay:
            alert.alert_type = f"{EVENT_NAME[e]}/{et}"
            alert.event_type = et
            ret.append(alert)
    return ret

  def add_from_msg(self, events):
    for e in events:
      self.events.append(e.name.raw)

  def to_msg(self):
    ret = []
    for event_name in self.events:
      event = car.CarEvent.new_message()
      event.name = event_name
      for event_type in EVENTS.get(event_name, {}).keys():
        setattr(event, event_type, True)
      ret.append(event)
    return ret

# 메세지 한글화 : 로웰 ( https://github.com/crwusiz/openpilot )

class Alert:
  def __init__(self,
               alert_text_1: str,
               alert_text_2: str,
               alert_status: log.ControlsState.AlertStatus,
               alert_size: log.ControlsState.AlertSize,
               alert_priority: Priority,
               visual_alert: car.CarControl.HUDControl.VisualAlert,
               audible_alert: car.CarControl.HUDControl.AudibleAlert,
               duration_sound: float,
               duration_hud_alert: float,
               duration_text: float,
               alert_rate: float = 0.,
               creation_delay: float = 0.):

    self.alert_text_1 = alert_text_1
    self.alert_text_2 = alert_text_2
    self.alert_status = alert_status
    self.alert_size = alert_size
    self.alert_priority = alert_priority
    self.visual_alert = visual_alert
    self.audible_alert = audible_alert

    self.duration_sound = duration_sound
    self.duration_hud_alert = duration_hud_alert
    self.duration_text = duration_text

    self.alert_rate = alert_rate
    self.creation_delay = creation_delay

    self.start_time = 0.
    self.alert_type = ""
    self.event_type = None

  def __str__(self) -> str:
    return f"{self.alert_text_1}/{self.alert_text_2} {self.alert_priority} {self.visual_alert} {self.audible_alert}"

  def __gt__(self, alert2) -> bool:
    return self.alert_priority > alert2.alert_priority


class NoEntryAlert(Alert):
  def __init__(self, alert_text_2, audible_alert=AudibleAlert.chimeError, duration_sound=.4,
               visual_alert=VisualAlert.none, duration_hud_alert=2.):
    #super().__init__("openpilot Unavailable", alert_text_2, AlertStatus.normal,
    super().__init__("오픈파일럿 사용불가", alert_text_2, AlertStatus.normal,
                     AlertSize.mid, Priority.LOW, visual_alert,
                     audible_alert, duration_sound, duration_hud_alert, 3.)


class SoftDisableAlert(Alert):
  def __init__(self, alert_text_2):
    #super().__init__("TAKE CONTROL IMMEDIATELY", alert_text_2,
    super().__init__("핸들을 즉시 잡아주세요", alert_text_2,
                     AlertStatus.critical, AlertSize.full,
                     Priority.MID, VisualAlert.steerRequired,
                     AudibleAlert.chimeWarningRepeat, .1, 2., 2.),


class ImmediateDisableAlert(Alert):
  #def __init__(self, alert_text_2, alert_text_1="TAKE CONTROL IMMEDIATELY"):
  def __init__(self, alert_text_2, alert_text_1="핸들을 즉시 잡아주세요"):
    super().__init__(alert_text_1, alert_text_2,
                     AlertStatus.critical, AlertSize.full,
                     Priority.HIGHEST, VisualAlert.steerRequired,
                     AudibleAlert.chimeWarningRepeat, 2.2, 3., 4.),


class EngagementAlert(Alert):
  def __init__(self, audible_alert=True):
    super().__init__("", "",
                     AlertStatus.normal, AlertSize.none,
                     Priority.MID, VisualAlert.none,
                     audible_alert, .2, 0., 0.),


class NormalPermanentAlert(Alert):
  def __init__(self, alert_text_1: str, alert_text_2: str, duration_text: float = 0.2):
    super().__init__(alert_text_1, alert_text_2,
                     AlertStatus.normal, AlertSize.mid if len(alert_text_2) else AlertSize.small,
                     Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., duration_text),


# ********** alert callback functions **********
def below_steer_speed_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  speed = int(round(CP.minSteerSpeed * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH)))
  unit = "㎞/h" if metric else "mph"
  return Alert(
    #"TAKE CONTROL",
    #"Steer Unavailable Below %d %s" % (speed, unit),
    "핸들을 잡아주세요",
    "%d %s 이상의 속도에서 조향제어가능합니다" % (speed, unit),
    AlertStatus.userPrompt, AlertSize.mid,
    Priority.MID, VisualAlert.steerRequired, AudibleAlert.chimePrompt, 0., 0.4, .3)


def calibration_incomplete_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  speed = int(MIN_SPEED_FILTER * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH))
  unit = "㎞/h" if metric else "mph"
  return Alert(
    #"Calibration in Progress: %d%%" % sm['liveCalibration'].calPerc,
    #"Drive Above %d %s" % (speed, unit),
    "캘리브레이션 진행중입니다 : %d%%" % sm['liveCalibration'].calPerc,
    "속도를 %d %s 이상으로 주행하세요" % (speed, unit),
    AlertStatus.normal, AlertSize.mid,
    Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2)


def no_gps_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  gps_integrated = sm['peripheralState'].pandaType in [log.PandaState.PandaType.uno, log.PandaState.PandaType.dos]
  return Alert(
    #"Poor GPS reception",
    #"If sky is visible, contact support" if gps_integrated else "Check GPS antenna placement",
    "GPS 수신불량",
    "GPS 연결상태 및 안테나를 점검하세요" if gps_integrated else "GPS 안테나를 점검하세요",
    AlertStatus.normal, AlertSize.mid,
    Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=300.)


def wrong_car_mode_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  #text = "Cruise Mode Disabled"
  text = "크루즈 비활성상태"
  if CP.carName == "honda":
    #text = "Main Switch Off"
    text = "메인 스위치 OFF"
  return NoEntryAlert(text, duration_hud_alert=0.)


def joystick_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  axes = sm['testJoystick'].axes
  gb, steer = list(axes)[:2] if len(axes) else (0., 0.)
  return Alert(
    #"Joystick Mode",
    "조이스틱 모드",
    f"Gas: {round(gb * 100.)}%, Steer: {round(steer * 100.)}%",
    AlertStatus.normal, AlertSize.mid,
    Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .1)

def auto_lane_change_alert(CP: car.CarParams, sm: messaging.SubMaster, metric: bool) -> Alert:
  alc_timer = sm['lateralPlan'].autoLaneChangeTimer
  return Alert(
    "자동차선변경이 %d초 뒤에 시작됩니다" % alc_timer,
    "차선의 차량을 확인하세요",
    AlertStatus.normal, AlertSize.mid,
    Priority.LOW, VisualAlert.none, AudibleAlert.chimeDingRepeat, .1, .1, .1, alert_rate=0.75)

EVENTS: Dict[int, Dict[str, Union[Alert, Callable[[Any, messaging.SubMaster, bool], Alert]]]] = {
  # ********** events with no alerts **********

  EventName.stockFcw: {},

  # ********** events only containing alerts displayed in all states **********

  EventName.joystickDebug: {
    ET.WARNING: joystick_alert,
    ET.PERMANENT: Alert(
      #"Joystick Mode",
      "조이스틱 모드",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 0.1),
  },

  EventName.controlsInitializing: {
    #ET.NO_ENTRY: NoEntryAlert("Controls Initializing"),
    ET.NO_ENTRY: NoEntryAlert("프로세스 초기화중입니다"),
  },

  EventName.startup: {
    ET.PERMANENT: Alert(
      #"Be ready to take over at any time",
      #"Always keep hands on wheel and eyes on road",
      "오픈파일럿 사용준비 완료",
      "항상 핸들을 잡고 도로를 주시하세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.chimeReady, 1., 0., 10.),
  },

  EventName.startupMaster: {
    ET.PERMANENT: Alert(
      #"WARNING: This branch is not tested",
      #"Always keep hands on wheel and eyes on road",
      "오픈파일럿 사용준비 완료",
      "항상 핸들을 잡고 도로를 주시하세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.chimeReady, 1., 0., 10.),
  },

  # Car is recognized, but marked as dashcam only
  EventName.startupNoControl: {
    ET.PERMANENT: Alert(
      #"Dashcam mode",
      #"Always keep hands on wheel and eyes on road",
      "대시캠 모드",
      "항상 핸들을 잡고 도로를 주시하세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 1., 0., 10.),
  },

  # Car is not recognized
  EventName.startupNoCar: {
    ET.PERMANENT: Alert(
      #"Dashcam mode for unsupported car",
      #"Always keep hands on wheel and eyes on road",
      "대시캠 모드 : 호환되지않는 차량",
      "항상 핸들을 잡고 도로를 주시하세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 10.),
  },

  EventName.startupNoFw: {
    ET.PERMANENT: Alert(
      #"Car Unrecognized",
      #"Check All Connections",
      "차량이 인식되지않습니다",
      "연결상태를 확인하세요",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., 10.),
  },

  EventName.dashcamMode: {
    ET.PERMANENT: Alert(
      #"Dashcam Mode",
      "대시캠 모드",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.invalidLkasSetting: {
    ET.PERMANENT: Alert(
      #"Stock LKAS is turned on",
      #"Turn off stock LKAS to engage",
      "차량 LKAS 버튼 상태확인",
      "차량 LKAS 버튼 OFF후 활성화됩니다",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  # Some features or cars are marked as community features. If openpilot
  # detects the use of a community feature it switches to dashcam mode
  # until these features are allowed using a toggle in settings.
  EventName.communityFeatureDisallowed: {
    # LOW priority to overcome Cruise Error
    ET.PERMANENT: Alert(
      #"openpilot Not Available",
      #"Enable Community Features in Settings to Engage",
      "커뮤니티 기능 감지됨",
      "개발자설정에서 커뮤니티 기능을 활성화해주세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  # openpilot doesn't recognize the car. This switches openpilot into a
  # read-only mode. This can be solved by adding your fingerprint.
  # See https://github.com/commaai/openpilot/wiki/Fingerprinting for more information
  EventName.carUnrecognized: {
    ET.PERMANENT: Alert(
      #"Dashcam Mode",
      #"Car Unrecognized",
      "대시캠 모드",
      "차량인식 불가",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.stockAeb: {
    ET.PERMANENT: Alert(
      #"BRAKE!",
      #"Stock AEB: Risk of Collision",
      "브레이크!",
      "추돌 위험",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGHEST, VisualAlert.fcw, AudibleAlert.none, 1., 2., 2.),
    #ET.NO_ENTRY: NoEntryAlert("Stock AEB: Risk of Collision"),
    ET.NO_ENTRY: NoEntryAlert("AEB: 추돌위험"),
  },

  EventName.fcw: {
    ET.PERMANENT: Alert(
      #"BRAKE!",
      #"Risk of Collision",
      "브레이크!",
      "추돌 위험",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGHEST, VisualAlert.fcw, AudibleAlert.chimeWarningRepeat, 1., 2., 2.),
  },

  EventName.ldw: {
    ET.PERMANENT: Alert(
      #"TAKE CONTROL",
      #"Lane Departure Detected",
      "핸들을 잡아주세요",
      "차선이탈 감지됨",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.ldw, AudibleAlert.chimeDing, .1, 2., 3.),
  },

  # ********** events only containing alerts that display while engaged **********

  EventName.gasPressed: {
    ET.PRE_ENABLE: Alert(
      #"openpilot will not brake while gas pressed",
      "가속패달감지시 오픈파일럿은 브레이크를 사용하지않습니다",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .0, .0, .1, creation_delay=1.),
  },

  # openpilot tries to learn certain parameters about your car by observing
  # how the car behaves to steering inputs from both human and openpilot driving.
  # This includes:
  # - steer ratio: gear ratio of the steering rack. Steering angle divided by tire angle
  # - tire stiffness: how much grip your tires have
  # - angle offset: most steering angle sensors are offset and measure a non zero angle when driving straight
  # This alert is thrown when any of these values exceed a sanity check. This can be caused by
  # bad alignment or bad sensor data. If this happens consistently consider creating an issue on GitHub
  EventName.vehicleModelInvalid: {
    #ET.NO_ENTRY: NoEntryAlert("Vehicle Parameter Identification Failed"),
    #ET.SOFT_DISABLE: SoftDisableAlert("Vehicle Parameter Identification Failed"),
    ET.NO_ENTRY: NoEntryAlert("차량 매개변수 식별 오류"),
    ET.SOFT_DISABLE: SoftDisableAlert("차량 매개변수 식별 오류"),
    ET.WARNING: Alert(
      #"Vehicle Parameter Identification Failed",
      "차량 매개변수 식별 오류",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.steerRequired, AudibleAlert.none, .0, .0, .1),
  },

  EventName.steerTempUnavailableSilent: {
    ET.WARNING: Alert(
      #"Steering Temporarily Unavailable",
      "조향제어 일시적으로 사용불가",
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.chimePrompt, 1., 1., 1.),
  },

  EventName.preDriverDistracted: {
    ET.WARNING: Alert(
      #"KEEP EYES ON ROAD: Driver Distracted",
      "도로를 주시하세요 : 운전자 도로주시 불안",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeDing, .1, .1, .1),
  },

  EventName.promptDriverDistracted: {
    ET.WARNING: Alert(
      #"KEEP EYES ON ROAD",
      #"Driver Distracted",
      "도로를 주시하세요",
      "운전자 도로주시 불안",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.steerRequired, AudibleAlert.chimeDing, .1, .1, .1),
  },

  EventName.driverDistracted: {
    ET.WARNING: Alert(
      #"DISENGAGE IMMEDIATELY",
      #"Driver Distracted",
      "조향제어가 해제됩니다",
      "운전자 도로주시 불안",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGH, VisualAlert.steerRequired, AudibleAlert.chimeWarningRepeat, .1, .1, .1),
  },

  EventName.preDriverUnresponsive: {
    ET.WARNING: Alert(
      #"TOUCH STEERING WHEEL: No Face Detected",
      "핸들을 잡아주세요 : 운전자 인식 불가",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.chimeDing, .1, .1, .1, alert_rate=0.75),
  },

  EventName.promptDriverUnresponsive: {
    ET.WARNING: Alert(
      #"TOUCH STEERING WHEEL",
      #"Driver Unresponsive",
      "핸들을 잡아주세요",
      "운전자 응답하지않음",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.steerRequired, AudibleAlert.chimeWarning2Repeat, .1, .1, .1),
  },

  EventName.driverUnresponsive: {
    ET.WARNING: Alert(
      #"DISENGAGE IMMEDIATELY",
      #"Driver Unresponsive",
      "조향제어가 해제됩니다",
      "운전자 응답하지않음",
      AlertStatus.critical, AlertSize.full,
      Priority.HIGH, VisualAlert.steerRequired, AudibleAlert.chimeWarningRepeat, .1, .1, .1),
  },

  EventName.manualRestart: {
    ET.WARNING: Alert(
      #"TAKE CONTROL",
      #"Resume Driving Manually",
      "핸들을 잡아주세요",
      "수동으로 재활성하세요",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.resumeRequired: {
    ET.WARNING: Alert(
      #"STOPPED",
      #"Press Resume to Move",
      "앞차량 멈춤",
      "이동하려면 RES버튼을 누르세요",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
  },

  EventName.belowSteerSpeed: {
    ET.WARNING: below_steer_speed_alert,
  },

  EventName.preLaneChangeLeft: {
    ET.WARNING: Alert(
      #"Steer Left to Start Lane Change Once Safe",
      #"",
      "좌측차선으로 차선을 변경합니다",
      "",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.preLaneChangeRight: {
    ET.WARNING: Alert(
      #"Steer Right to Start Lane Change Once Safe",
      #"",
      "우측차선으로 차선을 변경합니다",
      "",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1, alert_rate=0.75),
  },

  EventName.laneChangeBlocked: {
    ET.WARNING: Alert(
      #"Car Detected in Blindspot",
      #"",
      "차선에 차량이 감지되니 대기하세요",
      "",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeDingRepeat, .1, .1, .1),
  },

  EventName.laneChange: {
    ET.WARNING: Alert(
      #"Changing Lanes",
      #"",
      "차선을 변경합니다",
      "",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, .0, .1, .1),
  },

  EventName.steerSaturated: {
    ET.WARNING: Alert(
      #"TAKE CONTROL",
      #"Turn Exceeds Steering Limit",
      "핸들을 잡아주세요",
      "조향제어 제한을 초과함",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.chimePrompt, 1., 1., 1.),
  },

  # Thrown when the fan is driven at >50% but is not rotating
  EventName.fanMalfunction: {
    #ET.PERMANENT: NormalPermanentAlert("Fan Malfunction", "Contact Support"),
    ET.PERMANENT: NormalPermanentAlert("FAN 오작동", "장치를 점검하세요"),
  },

  # Camera is not outputting frames at a constant framerate
  EventName.cameraMalfunction: {
    #ET.PERMANENT: NormalPermanentAlert("Camera Malfunction", "Contact Support"),
    ET.PERMANENT: NormalPermanentAlert("카메라 오작동", "장치를 점검하세요"),
  },

  # Unused
  EventName.gpsMalfunction: {
    #ET.PERMANENT: NormalPermanentAlert("GPS Malfunction", "Contact Support"),
    ET.PERMANENT: NormalPermanentAlert("GPS 오작동", "장치를 점검하세요"),
  },

  # When the GPS position and localizer diverge the localizer is reset to the
  # current GPS position. This alert is thrown when the localizer is reset
  # more often than expected.
  EventName.localizerMalfunction: {
    # ET.PERMANENT: NormalPermanentAlert("Sensor Malfunction", "Contact Support"),
    ET.PERMANENT: NormalPermanentAlert("센서 오작동", "장치를 점검하세요"),
  },

  # ********** events that affect controls state transitions **********

  EventName.pcmEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.chimeEngage),
  },

  EventName.buttonEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.chimeEngage),
  },

  EventName.pcmDisable: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
  },

  EventName.buttonCancel: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
  },

  EventName.brakeHold: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    #ET.NO_ENTRY: NoEntryAlert("Brake Hold Active"),
    ET.NO_ENTRY: NoEntryAlert("브레이크 감지됨"),
  },

  EventName.parkBrake: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    #ET.NO_ENTRY: NoEntryAlert("Park Brake Engaged"),
    ET.NO_ENTRY: NoEntryAlert("주차 브레이크를 해제하세요"),
  },

  EventName.pedalPressed: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    #ET.NO_ENTRY: NoEntryAlert("Pedal Pressed During Attempt",
    ET.NO_ENTRY: NoEntryAlert("브레이크 감지됨",
                              visual_alert=VisualAlert.brakePressed),
  },

  EventName.wrongCarMode: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    ET.NO_ENTRY: wrong_car_mode_alert,
  },

  EventName.wrongCruiseMode: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    #ET.NO_ENTRY: NoEntryAlert("Enable Adaptive Cruise"),
    ET.NO_ENTRY: NoEntryAlert("어뎁티브크루즈를 활성화하세요"),
  },

  EventName.steerTempUnavailable: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Steering Temporarily Unavailable"),
    #ET.NO_ENTRY: NoEntryAlert("Steering Temporarily Unavailable",
    ET.WARNING: Alert(
      "핸들을 잡아주세요",
      "조향제어 일시적으로 사용불가",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.steerRequired, AudibleAlert.none, 0., 0., .2),
    ET.NO_ENTRY: NoEntryAlert("조향제어 일시적으로 사용불가",
                              duration_hud_alert=0.),
  },

  EventName.outOfSpace: {
    ET.PERMANENT: Alert(
      #"Out of Storage",
      "저장공간 부족",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    #ET.NO_ENTRY: NoEntryAlert("Out of Storage Space",
    ET.NO_ENTRY: NoEntryAlert("저장공간 부족",
                              duration_hud_alert=0.),
  },

  EventName.belowEngageSpeed: {
    #ET.NO_ENTRY: NoEntryAlert("Speed Too Low"),
    ET.NO_ENTRY: NoEntryAlert("속도를 높여주세요"),
  },

  EventName.sensorDataInvalid: {
    ET.PERMANENT: Alert(
      #"No Data from Device Sensors",
      #"Reboot your Device",
      "장치 센서 오류",
      "장치를 점검하세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=1.),
    #ET.NO_ENTRY: NoEntryAlert("No Data from Device Sensors"),
    ET.NO_ENTRY: NoEntryAlert("장치 센서 오류"),
  },

  EventName.noGps: {
    ET.PERMANENT: no_gps_alert,
  },

  EventName.soundsUnavailable: {
    #ET.PERMANENT: NormalPermanentAlert("Speaker not found", "Reboot your Device"),
    #ET.NO_ENTRY: NoEntryAlert("Speaker not found"),
    ET.PERMANENT: NormalPermanentAlert("스피커가 감지되지않습니다", "장치를 점검하세요"),
    ET.NO_ENTRY: NoEntryAlert("스피커가 감지되지않습니다"),
  },

  EventName.tooDistracted: {
    #ET.NO_ENTRY: NoEntryAlert("Distraction Level Too High"),
    ET.NO_ENTRY: NoEntryAlert("방해 수준이 너무높습니다"),
  },

  EventName.overheat: {
    ET.PERMANENT: Alert(
      #"System Overheated",
      "장치 과열됨",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    #ET.SOFT_DISABLE: SoftDisableAlert("System Overheated"),
    #ET.NO_ENTRY: NoEntryAlert("System Overheated"),
    ET.SOFT_DISABLE: SoftDisableAlert("장치 과열됨"),
    ET.NO_ENTRY: NoEntryAlert("장치 과열됨"),
  },

  EventName.wrongGear: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Gear not D"),
    #ET.NO_ENTRY: NoEntryAlert("Gear not D"),
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    ET.NO_ENTRY: NoEntryAlert("기어를 [D]로 변경하세요",
                              audible_alert=AudibleAlert.chimeGeard, duration_sound=3.),
  },

  # This alert is thrown when the calibration angles are outside of the acceptable range.
  # For example if the device is pointed too much to the left or the right.
  # Usually this can only be solved by removing the mount from the windshield completely,
  # and attaching while making sure the device is pointed straight forward and is level.
  # See https://comma.ai/setup for more information
  EventName.calibrationInvalid: {
    #ET.PERMANENT: NormalPermanentAlert("Calibration Invalid", "Remount Device and Recalibrate"),
    #ET.SOFT_DISABLE: SoftDisableAlert("Calibration Invalid: Remount Device & Recalibrate"),
    #ET.NO_ENTRY: NoEntryAlert("Calibration Invalid: Remount Device & Recalibrate"),
    ET.PERMANENT: NormalPermanentAlert("캘리브레이션 오류", "장치 위치변경후 캘리브레이션을 다시하세요"),
    ET.SOFT_DISABLE: SoftDisableAlert("캘리브레이션 오류 : 장치 위치변경후 캘리브레이션을 다시하세요"),
    ET.NO_ENTRY: NoEntryAlert("캘리브레이션 오류 : 장치 위치변경후 캘리브레이션을 다시하세요"),
  },

  EventName.calibrationIncomplete: {
    ET.PERMANENT: calibration_incomplete_alert,
    #ET.SOFT_DISABLE: SoftDisableAlert("Calibration in Progress"),
    #ET.NO_ENTRY: NoEntryAlert("Calibration in Progress"),
    ET.SOFT_DISABLE: SoftDisableAlert("캘리브레이션 진행중입니다"),
    ET.NO_ENTRY: NoEntryAlert("캘리브레이션 진행중입니다"),
  },

  EventName.doorOpen: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Door Open"),
    #ET.NO_ENTRY: NoEntryAlert("Door Open"),
    ET.PERMANENT: Alert(
      "도어 열림",
      "",
      AlertStatus.normal, AlertSize.full,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=0.5),
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    ET.NO_ENTRY: NoEntryAlert("도어 열림"),
  },

  EventName.seatbeltNotLatched: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Seatbelt Unlatched"),
    #ET.NO_ENTRY: NoEntryAlert("Seatbelt Unlatched"),
    ET.PERMANENT: Alert(
      "안전벨트 미착용",
      "",
      AlertStatus.normal, AlertSize.full,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=0.5),
    ET.SOFT_DISABLE: SoftDisableAlert("안전벨트를 착용해주세요"),
    ET.NO_ENTRY: NoEntryAlert("안전벨트를 착용해주세요",
                              audible_alert=AudibleAlert.chimeSeatbelt, duration_sound=3.),
  },

  EventName.espDisabled: {
    #ET.SOFT_DISABLE: SoftDisableAlert("ESP Off"),
    #ET.NO_ENTRY: NoEntryAlert("ESP Off"),
    ET.SOFT_DISABLE: SoftDisableAlert("ESP 꺼짐"),
    ET.NO_ENTRY: NoEntryAlert("ESP 꺼짐"),
  },

  EventName.lowBattery: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Low Battery"),
    #ET.NO_ENTRY: NoEntryAlert("Low Battery"),
    ET.SOFT_DISABLE: SoftDisableAlert("배터리 부족"),
    ET.NO_ENTRY: NoEntryAlert("배터리 부족"),
  },

  # Different openpilot services communicate between each other at a certain
  # interval. If communication does not follow the regular schedule this alert
  # is thrown. This can mean a service crashed, did not broadcast a message for
  # ten times the regular interval, or the average interval is more than 10% too high.
  EventName.commIssue: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Communication Issue between Processes"),
    #ET.NO_ENTRY: NoEntryAlert("Communication Issue between Processes",
    ET.SOFT_DISABLE: SoftDisableAlert("장치 프로세스 동작오류"),
    ET.NO_ENTRY: NoEntryAlert("장치 프로세스 동작오류",
                              audible_alert=AudibleAlert.chimeDisengage),
  },

  # Thrown when manager detects a service exited unexpectedly while driving
  EventName.processNotRunning: {
    #ET.NO_ENTRY: NoEntryAlert("System Malfunction: Reboot Your Device",
    ET.NO_ENTRY: NoEntryAlert("시스템 오작동: 장치를 재부팅 하세요",
                              audible_alert=AudibleAlert.chimeDisengage),
  },

  EventName.radarFault: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Radar Error: Restart the Car"),
    #ET.NO_ENTRY: NoEntryAlert("Radar Error: Restart the Car"),
    ET.SOFT_DISABLE: SoftDisableAlert("레이더 오류 : 차량을 재가동하세요"),
    ET.NO_ENTRY: NoEntryAlert("레이더 오류 : 차량을 재가동하세요"),
  },

  # Every frame from the camera should be processed by the model. If modeld
  # is not processing frames fast enough they have to be dropped. This alert is
  # thrown when over 20% of frames are dropped.
  EventName.modeldLagging: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Driving model lagging"),
    #ET.NO_ENTRY: NoEntryAlert("Driving model lagging"),
    ET.SOFT_DISABLE: SoftDisableAlert("주행모델 지연됨"),
    ET.NO_ENTRY: NoEntryAlert("주행모델 지연됨"),
  },

  # Besides predicting the path, lane lines and lead car data the model also
  # predicts the current velocity and rotation speed of the car. If the model is
  # very uncertain about the current velocity while the car is moving, this
  # usually means the model has trouble understanding the scene. This is used
  # as a heuristic to warn the driver.
  EventName.posenetInvalid: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Model Output Uncertain"),
    #ET.NO_ENTRY: NoEntryAlert("Model Output Uncertain"),
    ET.SOFT_DISABLE: SoftDisableAlert("차선인식상태가 좋지않으니 주의운전하세요"),
    ET.NO_ENTRY: NoEntryAlert("차선인식상태가 좋지않으니 주의운전하세요"),
  },

  # When the localizer detects an acceleration of more than 40 m/s^2 (~4G) we
  # alert the driver the device might have fallen from the windshield.
  EventName.deviceFalling: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Device Fell Off Mount"),
    #ET.NO_ENTRY: NoEntryAlert("Device Fell Off Mount"),
    ET.SOFT_DISABLE: SoftDisableAlert("장치가 마운트에서 떨어짐"),
    ET.NO_ENTRY: NoEntryAlert("장치가 마운트에서 떨어짐"),
  },

  EventName.lowMemory: {
    #ET.SOFT_DISABLE: SoftDisableAlert("Low Memory: Reboot Your Device"),
    #ET.PERMANENT: NormalPermanentAlert("Low Memory", "Reboot your Device"),
    #ET.NO_ENTRY: NoEntryAlert("Low Memory: Reboot Your Device",
    ET.SOFT_DISABLE: SoftDisableAlert("메모리 부족 : 장치를 재가동하세요"),
    ET.PERMANENT: NormalPermanentAlert("메모리 부족", "장치를 재가동하세요"),
    ET.NO_ENTRY: NoEntryAlert("메모리 부족 : 장치를 재가동하세요",
                              audible_alert=AudibleAlert.chimeDisengage),
  },

  EventName.highCpuUsage: {
    #ET.SOFT_DISABLE: SoftDisableAlert("System Malfunction: Reboot Your Device"),
    #ET.PERMANENT: NormalPermanentAlert("System Malfunction", "Reboot your Device"),
    ET.NO_ENTRY: NoEntryAlert("System Malfunction: Reboot Your Device",
                              audible_alert=AudibleAlert.chimeDisengage),
  },

  EventName.accFaulted: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Cruise Faulted"),
    #ET.PERMANENT: NormalPermanentAlert("Cruise Faulted", ""),
    #ET.NO_ENTRY: NoEntryAlert("Cruise Faulted"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("크루즈 오류"),
    ET.PERMANENT: NormalPermanentAlert("크루즈 오류", ""),
    ET.NO_ENTRY: NoEntryAlert("크루즈 오류"),
  },

  EventName.controlsMismatch: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Controls Mismatch"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("컨트롤 불일치"),
  },

  EventName.roadCameraError: {
    #ET.PERMANENT: NormalPermanentAlert("Road Camera Error", "",
    ET.PERMANENT: NormalPermanentAlert("주행 카메라 오류", "",
                                       duration_text=10.),
  },

  EventName.driverCameraError: {
    #ET.PERMANENT: NormalPermanentAlert("Driver Camera Error", "",
    ET.PERMANENT: NormalPermanentAlert("운전자 카메라 오류", "",
                                       duration_text=10.),
  },

  EventName.wideRoadCameraError: {
    #ET.PERMANENT: NormalPermanentAlert("Wide Road Camera Error", "",
    ET.PERMANENT: NormalPermanentAlert("와이드 주행카메라 오류", "",
                                       duration_text=10.),
  },

  # Sometimes the USB stack on the device can get into a bad state
  # causing the connection to the panda to be lost
  EventName.usbError: {
    #ET.SOFT_DISABLE: SoftDisableAlert("USB Error: Reboot Your Device"),
    #ET.PERMANENT: NormalPermanentAlert("USB Error: Reboot Your Device", ""),
    #ET.NO_ENTRY: NoEntryAlert("USB Error: Reboot Your Device"),
    ET.SOFT_DISABLE: SoftDisableAlert("USB 오류: 장치를 재부팅하세요"),
    ET.PERMANENT: NormalPermanentAlert("USB 오류: 장치를 재부팅하세요", ""),
    ET.NO_ENTRY: NoEntryAlert("USB 오류: 장치를 재부팅하세요"),
  },

  # This alert can be thrown for the following reasons:
  # - No CAN data received at all
  # - CAN data is received, but some message are not received at the right frequency
  # If you're not writing a new car port, this is usually cause by faulty wiring
  EventName.canError: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("CAN Error: Check Connections"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("CAN 오류 : 장치를 점검하세요"),
    ET.PERMANENT: Alert(
      #"CAN Error: Check Connections",
      "CAN 오류 : 장치를 점검하세요",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=1.),
    #ET.NO_ENTRY: NoEntryAlert("CAN Error: Check Connections"),
    ET.NO_ENTRY: NoEntryAlert("CAN 오류 : 장치를 점검하세요"),
  },

  EventName.steerUnavailable: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("LKAS Fault: Restart the Car"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("LKAS 오류 : 차량을 재가동하세요"),
    ET.PERMANENT: Alert(
      #"LKAS Fault: Restart the car to engage",
      "LKAS 오류 : 차량을 재가동하세요",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    #ET.NO_ENTRY: NoEntryAlert("LKAS Fault: Restart the Car"),
    ET.NO_ENTRY: NoEntryAlert("LKAS 오류 : 차량을 재가동하세요"),
  },

  EventName.brakeUnavailable: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Cruise Fault: Restart the Car"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("크루즈 오류 : 차량을 재가동하세요"),
    ET.PERMANENT: Alert(
      #"Cruise Fault: Restart the car to engage",
      "크루즈 오류 : 차량을 재가동하세요",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    #ET.NO_ENTRY: NoEntryAlert("Cruise Fault: Restart the Car"),
    ET.NO_ENTRY: NoEntryAlert("크루즈 오류 : 차량을 재가동하세요"),
  },

  EventName.reverseGear: {
    ET.PERMANENT: Alert(
      #"Reverse\nGear",
      "기어 [R] 상태",
      "",
      AlertStatus.normal, AlertSize.full,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0., 0., .2, creation_delay=0.5),
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Reverse Gear"),
    #ET.NO_ENTRY: NoEntryAlert("Reverse Gear"),
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.chimeDisengage),
    ET.NO_ENTRY: NoEntryAlert("기어 [R] 상태"),
  },

  # On cars that use stock ACC the car can decide to cancel ACC for various reasons.
  # When this happens we can no long control the car so the user needs to be warned immediately.
  EventName.cruiseDisabled: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Cruise Is Off"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("크루즈 꺼짐"),
  },

  # For planning the trajectory Model Predictive Control (MPC) is used. This is
  # an optimization algorithm that is not guaranteed to find a feasible solution.
  # If no solution is found or the solution has a very high cost this alert is thrown.
  EventName.plannerError: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Planner Solution Error"),
    #ET.NO_ENTRY: NoEntryAlert("Planner Solution Error"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("플래너 솔루션 오류"),
    ET.NO_ENTRY: NoEntryAlert("플래너 솔루션 오류"),
  },

  # When the relay in the harness box opens the CAN bus between the LKAS camera
  # and the rest of the car is separated. When messages from the LKAS camera
  # are received on the car side this usually means the relay hasn't opened correctly
  # and this alert is thrown.
  EventName.relayMalfunction: {
    #ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Harness Malfunction"),
    #ET.PERMANENT: NormalPermanentAlert("Harness Malfunction", "Check Hardware"),
    #ET.NO_ENTRY: NoEntryAlert("Harness Malfunction"),
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("하네스 오작동"),
    ET.PERMANENT: NormalPermanentAlert("하네스 오작동", "장치를 점검하세요"),
    ET.NO_ENTRY: NoEntryAlert("하네스 오작동"),
  },

  EventName.noTarget: {
    ET.IMMEDIATE_DISABLE: Alert(
      #"openpilot Canceled",
      #"No close lead car",
      "오픈파일럿 사용불가",
      "근접 앞차량이 없습니다",
      AlertStatus.normal, AlertSize.mid,
      Priority.HIGH, VisualAlert.none, AudibleAlert.chimeDisengage, .4, 2., 3.),
    #ET.NO_ENTRY: NoEntryAlert("No Close Lead Car"),
    ET.NO_ENTRY: NoEntryAlert("근접 앞차량이 없습니다"),
  },

  EventName.speedTooLow: {
    ET.IMMEDIATE_DISABLE: Alert(
      #"openpilot Canceled",
      #"Speed too low",
      "오픈파일럿 사용불가",
      "속도를 높이고 재가동하세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.HIGH, VisualAlert.none, AudibleAlert.chimeDisengage, .4, 2., 3.),
  },

  # When the car is driving faster than most cars in the training data the model outputs can be unpredictable
  EventName.speedTooHigh: {
    ET.WARNING: Alert(
      #"Speed Too High",
      #"Model uncertain at this speed",
      "속도가 너무 높습니다",
      "속도를 줄여주세요",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.HIGH, VisualAlert.steerRequired, AudibleAlert.chimeWarning2Repeat, 2.2, 3., 4.),
    ET.NO_ENTRY: Alert(
      #"Speed Too High",
      #"Slow down to engage",
      "속도가 너무 높습니다",
      "속도를 줄이고 재가동하세요",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.chimeError, .4, 2., 3.),
  },

  EventName.lowSpeedLockout: {
    ET.PERMANENT: Alert(
      #"Cruise Fault: Restart the car to engage",
      "크루즈 오류 : 차량을 재가동하세요",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, 0., 0., .2),
    #ET.NO_ENTRY: NoEntryAlert("Cruise Fault: Restart the Car"),
    ET.NO_ENTRY: NoEntryAlert("크루즈 오류 : 차량을 재가동하세요"),
  },

  EventName.turningIndicatorOn: {
    ET.WARNING: Alert(
      "방향지시등 동작중에는 핸들을 잡아주세요",
      "",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.MID, VisualAlert.none, AudibleAlert.none, .0, .1, .2),
  },

  EventName.autoLaneChange: {
    ET.WARNING: auto_lane_change_alert,
  },

  EventName.slowingDownSpeed: {
    #ET.PERMANENT: Alert("Slowing down", "", AlertStatus.normal, AlertSize.small,
    ET.PERMANENT: Alert("속도를 줄이고 있습니다", "", AlertStatus.normal, AlertSize.small,
      Priority.MID, VisualAlert.none, AudibleAlert.none, 0., .1, .1),
  },

  EventName.slowingDownSpeedSound: {
    #ET.PERMANENT: Alert("Slowing down","", AlertStatus.normal, AlertSize.small,
    ET.PERMANENT: Alert("속도를 줄이고 있습니다", "", AlertStatus.normal, AlertSize.small,
      Priority.HIGH, VisualAlert.none, AudibleAlert.chimeSlowingDownSpeed, 2., 2., 2.),
  },

}
