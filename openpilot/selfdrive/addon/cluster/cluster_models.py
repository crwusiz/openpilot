import threading
import time

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params


class ClusterModels:
  def __init__(self):
    cloudlog.info("Initializing ClusterModels (Lightweight)...")

    self.sm = messaging.SubMaster([
      'modelV2', 'carState', 'selfdriveState', 'controlsState', 'carControl',
      'carParams', 'deviceState', 'gpsLocationExternal', 'naviData',
      'longitudinalPlan', 'liveParameters',
    ])

    self.v_ego = 0.0  # m/s 단위 속도
    self.accel = 0.0  # m/s², used for speed color feedback
    self.enabled = False
    self.left_blinker = False
    self.right_blinker = False
    self.left_blindspot = False
    self.right_blindspot = False
    self.brake_pressed = False
    self.gas_pressed = False
    self.steering_angle = 0.0
    self.cruise_speed = 0.0
    self.set_speed = 0.0
    self.gps_bearing = 0.0
    self.gps_satellites = 0
    self.wifi_strength = 0
    self.tpms = [0.0, 0.0, 0.0, 0.0]
    self.distance_level = 0
    self.traffic_state = 0
    self.road_signs = 0
    self.nda_state = 0
    self.stock_limit_speed = 0.0
    self.nav_limit_speed = 0.0
    self.cam_limit_speed = 0.0
    self.cam_limit_speed_left_dist = 0.0
    self.section_limit_speed = 0.0
    self.section_left_dist = 0.0
    self.speed_camera = False
    self.school_zone = False
    self.speed_bump = False
    self.ignore_limit_timer = 0.0

    try:
      self.distance_level = min(max(int(Params().get("LongitudinalPersonality") or 0), 0), 3)
    except (TypeError, ValueError):
      self.distance_level = 0

    self.model_valid = False
    self.path_x = []
    self.path_y = []
    self.left_lane_x = []
    self.left_lane_y = []
    self.right_lane_x = []
    self.right_lane_y = []

    self._running = True
    self._thread = threading.Thread(target=self._update_loop, daemon=True)
    self._thread.start()

  def _update_once(self):
    self.sm.update(0)

    if self.sm.updated['carState']:
      cs = self.sm['carState']
      self.v_ego = cs.vEgo
      self.accel = getattr(cs, 'aEgo', 0.0)
      self.left_blinker = cs.leftBlinker
      self.right_blinker = cs.rightBlinker
      self.left_blindspot = getattr(cs, 'leftBlindspot', False)
      self.right_blindspot = getattr(cs, 'rightBlindspot', False)
      self.brake_pressed = cs.brakePressed
      self.gas_pressed = cs.gasPressed
      self.steering_angle = cs.steeringAngleDeg
      cluster_speed = getattr(cs, 'vCruiseCluster', 0.0)
      fallback_speed = getattr(self.sm['controlsState'].deprecated, 'vCruise', 0.0)
      self.cruise_speed = cluster_speed if cluster_speed > 0 else fallback_speed
      self.set_speed = getattr(cs, 'vCruise', self.cruise_speed)
      self.stock_limit_speed = getattr(cs, 'speedLimit', 0.0)
      if hasattr(cs, 'exState'):
        ex = cs.exState
        if hasattr(ex, 'tpms'):
          self.tpms = [ex.tpms.fl, ex.tpms.fr, ex.tpms.rl, ex.tpms.rr]
        self.road_signs = getattr(ex, 'roadSigns', self.road_signs)
        self.ignore_limit_timer = getattr(ex, 'ignoreLimitTimer', self.ignore_limit_timer)

    if self.sm.updated['selfdriveState']:
      ss = self.sm['selfdriveState']
      self.enabled = ss.enabled

    if self.sm.updated['deviceState']:
      self.wifi_strength = self.sm['deviceState'].networkStrength

    if self.sm.updated['gpsLocationExternal']:
      gps = self.sm['gpsLocationExternal']
      self.gps_bearing = gps.bearingDeg
      self.gps_satellites = gps.satelliteCount

    if self.sm.updated['naviData']:
      nav = self.sm['naviData']
      self.nda_state = getattr(nav, 'active', 0)
      self.nav_limit_speed = getattr(nav, 'roadLimitSpeed', 0.0)
      self.cam_limit_speed = getattr(nav, 'camLimitSpeed', 0.0)
      self.cam_limit_speed_left_dist = getattr(nav, 'camLimitSpeedLeftDist', 0.0)
      self.section_limit_speed = getattr(nav, 'sectionLimitSpeed', 0.0)
      self.section_left_dist = getattr(nav, 'sectionLeftDist', 0.0)
      self.speed_camera = self.cam_limit_speed > 0
      self.school_zone = self.section_limit_speed > 0

    if self.sm.updated['longitudinalPlan']:
      self.traffic_state = getattr(self.sm['longitudinalPlan'], 'trafficState', 0)

    if self.sm.updated['modelV2']:
      md = self.sm['modelV2']

      if len(md.position.x) > 0:
        self.path_x = list(md.position.x)
        self.path_y = list(md.position.y)
        self.model_valid = True
      else:
        self.model_valid = False

      if len(md.laneLines) == 4:
        self.left_lane_x = list(md.laneLines[1].x)
        self.left_lane_y = list(md.laneLines[1].y)

        self.right_lane_x = list(md.laneLines[2].x)
        self.right_lane_y = list(md.laneLines[2].y)

  def _update_loop(self):
    while self._running:
      try:
        self._update_once()
      except Exception as e:
        cloudlog.error(f"ClusterModels update error: {e}")
      time.sleep(0.01)

  def update(self):
    return

  def close(self):
    self._running = False
    if self._thread is not None:
      self._thread.join(timeout=1.0)

  def is_valid(self):
    return self.model_valid

  def get_hud_data(self):
    return {
      "v_ego": self.v_ego,
      "accel": self.accel,
      "enabled": self.enabled,
      "left_blinker": self.left_blinker,
      "right_blinker": self.right_blinker,
      "left_blindspot": self.left_blindspot,
      "right_blindspot": self.right_blindspot,
      "brake_pressed": self.brake_pressed,
      "gas_pressed": self.gas_pressed,
      "steering_angle": self.steering_angle,
      "cruise_speed": self.cruise_speed,
      "set_speed": self.set_speed,
      "gps_bearing": self.gps_bearing,
      "gps_satellites": self.gps_satellites,
      "wifi_strength": self.wifi_strength,
      "tpms": self.tpms,
      "distance_level": self.distance_level,
      "traffic_state": self.traffic_state,
      "road_signs": self.road_signs,
      "nda_state": self.nda_state,
      "stock_limit_speed": self.stock_limit_speed,
      "nav_limit_speed": self.nav_limit_speed,
      "cam_limit_speed": self.cam_limit_speed,
      "cam_limit_speed_left_dist": self.cam_limit_speed_left_dist,
      "section_limit_speed": self.section_limit_speed,
      "section_left_dist": self.section_left_dist,
      "speed_camera": self.speed_camera,
      "school_zone": self.school_zone,
      "speed_bump": self.speed_bump,
      "ignore_limit_timer": self.ignore_limit_timer,
    }

  def get_path_data(self):
    return {
      "path_x": self.path_x,
      "path_y": self.path_y,
      "left_lane_x": self.left_lane_x,
      "left_lane_y": self.left_lane_y,
      "right_lane_x": self.right_lane_x,
      "right_lane_y": self.right_lane_y
    }
