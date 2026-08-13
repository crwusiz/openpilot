import threading
import time

import numpy as np

from openpilot.cereal import log, messaging
from opendbc.car import structs
from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params
from openpilot.common.transformations.camera import DEVICE_CAMERAS, view_frame_from_device_frame
from openpilot.common.transformations.orientation import rot_from_euler

GearShifter = structs.CarState.GearShifter


def _enum_value(value):
  return int(getattr(value, "raw", value))


class ClusterModels:
  def __init__(self):
    cloudlog.info("Initializing ClusterModels (Lightweight)...")

    self.sm = messaging.SubMaster([
      'modelV2', 'carState', 'selfdriveState', 'controlsState', 'carControl',
      'carParams', 'deviceState', 'gpsLocationExternal', 'naviData',
      'longitudinalPlan', 'vehicleParameters', 'extrinsicsCalibration',
      'narrowRoadCameraState', 'radarState',
    ])

    self.v_ego = 0.0  # m/s 단위 속도
    self.v_ego_cluster_seen = False
    self.accel = 0.0  # m/s², used for speed color feedback
    self.enabled = False
    self.pre_enabled_or_overriding = False
    self.lat_active = False
    self.cruise_available = False
    self.reverse = False
    self.left_blinker = False
    self.right_blinker = False
    self.left_blindspot = False
    self.right_blindspot = False
    self.brake_pressed = False
    self.gas_pressed = False
    self.steering_pressed = False
    self.steering_angle = 0.0
    self.cruise_speed = 0.0
    self.set_speed = 0.0
    self.is_cruise_set = False
    self.gps_bearing = 0.0
    self.gps_satellites = 0
    self.wifi_strength = 0
    self.tpms = [0.0, 0.0, 0.0, 0.0]
    self.distance_level = 1
    self.traffic_state = 0
    self.road_signs = 0
    self.nda_state = 0
    self.stock_limit_speed = 0.0
    self.nav_limit_speed = 0.0
    self.cam_limit_speed = 0.0
    self.cam_limit_speed_left_dist = 0.0
    self.section_limit_speed = 0.0
    self.section_left_dist = 0.0
    self.cam_type = 0
    self.speed_camera = False
    self.school_zone = False
    self._navi_school_zone = False
    self._navi_last_road_name = ""
    self.speed_bump = False
    self.ignore_limit_timer = 0.0

    try:
      personality = min(max(int(Params().get("LongitudinalPersonality") or 0), 0), 3)
      self.distance_level = personality + 1
    except (TypeError, ValueError):
      self.distance_level = 1

    self.model_valid = False
    self.path_x = []
    self.path_y = []
    self.left_lane_x = []
    self.left_lane_y = []
    self.right_lane_x = []
    self.right_lane_y = []
    self.path_z = []
    self.lane_lines = []
    self.lane_line_probs = []
    self.road_edges = []
    self.road_edge_stds = []
    self.leads = []
    self.camera_height = 1.22
    self.camera_intrinsics = None
    self.view_from_calib = view_frame_from_device_frame.copy()
    self.device_type = "unknown"
    self.camera_sensor = "unknown"

    self._running = True
    self._thread = threading.Thread(target=self._update_loop, daemon=True)
    self._thread.start()

  def _update_once(self):
    self.sm.update(0)

    if self.sm.updated['carState']:
      cs = self.sm['carState']
      v_ego_cluster = getattr(cs, 'vEgoCluster', 0.0)
      self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
      self.v_ego = v_ego_cluster if self.v_ego_cluster_seen else cs.vEgo
      self.accel = getattr(cs, 'aEgo', 0.0)
      self.cruise_available = bool(cs.cruiseState.available)
      self.reverse = cs.gearShifter == GearShifter.reverse
      self.left_blinker = cs.leftBlinker
      self.right_blinker = cs.rightBlinker
      self.left_blindspot = getattr(cs, 'leftBlindspot', False)
      self.right_blindspot = getattr(cs, 'rightBlindspot', False)
      self.brake_pressed = cs.brakePressed
      self.gas_pressed = cs.gasPressed
      self.steering_pressed = bool(getattr(cs, 'steeringPressed', False))
      self.steering_angle = cs.steeringAngleDeg
      cluster_speed = getattr(cs, 'vCruiseCluster', 0.0)
      fallback_speed = getattr(self.sm['controlsState'].deprecated, 'vCruise', 0.0)
      self.cruise_speed = cluster_speed if cluster_speed > 0 else fallback_speed
      self.set_speed = getattr(cs, 'vCruise', self.cruise_speed)
      self.is_cruise_set = 0 < self.cruise_speed < 255
      self.stock_limit_speed = getattr(cs, 'speedLimit', 0.0)
      if hasattr(cs, 'exState'):
        ex = cs.exState
        if hasattr(ex, 'tpms'):
          self.tpms = [ex.tpms.fl, ex.tpms.fr, ex.tpms.rl, ex.tpms.rr]
        self.road_signs = getattr(ex, 'roadSigns', self.road_signs)
        self.school_zone = self.road_signs == 1 or self._navi_school_zone
        self.ignore_limit_timer = getattr(ex, 'ignoreLimitTimer', self.ignore_limit_timer)

    if self.sm.updated['selfdriveState']:
      ss = self.sm['selfdriveState']
      self.enabled = ss.enabled
      self.pre_enabled_or_overriding = ss.state in (
        log.SelfdriveState.OpenpilotState.preEnabled,
        log.SelfdriveState.OpenpilotState.overriding,
      )
      self.distance_level = min(max(_enum_value(ss.personality) + 1, 1), 4)

    if self.sm.updated['carControl']:
      car_control = self.sm['carControl']
      self.lat_active = bool(car_control.latActive)
      distance_bars = int(car_control.hudControl.leadDistanceBars)
      if 1 <= distance_bars <= 4:
        self.distance_level = distance_bars

    if self.sm.updated['deviceState']:
      device_state = self.sm['deviceState']
      self.wifi_strength = _enum_value(device_state.networkStrength)
      self.device_type = str(device_state.deviceType)
      self._update_camera_intrinsics()

    if self.sm.updated['narrowRoadCameraState']:
      self.camera_sensor = str(self.sm['narrowRoadCameraState'].sensor)
      self._update_camera_intrinsics()

    if self.sm.updated['extrinsicsCalibration']:
      calib = self.sm['extrinsicsCalibration']
      if len(calib.rpyCalib) == 3:
        self.view_from_calib = view_frame_from_device_frame @ rot_from_euler(calib.rpyCalib)
      if len(calib.height) > 0:
        self.camera_height = float(calib.height[0])

    if self.sm.updated['gpsLocationExternal']:
      gps = self.sm['gpsLocationExternal']
      self.gps_bearing = gps.bearingDeg
      self.gps_satellites = gps.satelliteCount

    if self.sm.updated['naviData']:
      navi_data = self.sm['naviData']
      self.nda_state = getattr(navi_data, 'active', 0)
      self.nav_limit_speed = getattr(navi_data, 'roadLimitSpeed', 0.0)
      self.cam_type = int(getattr(navi_data, 'camType', 0) or 0)
      self.cam_limit_speed = getattr(navi_data, 'camLimitSpeed', 0.0)
      self.cam_limit_speed_left_dist = getattr(navi_data, 'camLimitSpeedLeftDist', 0.0)
      self.section_limit_speed = getattr(navi_data, 'sectionLimitSpeed', 0.0)
      self.section_left_dist = getattr(navi_data, 'sectionLeftDist', 0.0)
      in_camera_zone = self.cam_limit_speed > 0 and self.cam_limit_speed_left_dist > 0
      in_section_zone = self.section_limit_speed > 0 and self.section_left_dist > 0
      self.speed_bump = self.cam_type == 22 and in_camera_zone
      self.speed_camera = (in_camera_zone or in_section_zone) and not self.speed_bump
      self._update_navi_school_zone(navi_data)

    if self.sm.updated['longitudinalPlan']:
      self.traffic_state = getattr(self.sm['longitudinalPlan'], 'trafficState', 0)

    if self.sm.updated['modelV2']:
      model = self.sm['modelV2']

      if len(model.position.x) > 0:
        self.path_x = list(model.position.x)
        self.path_y = list(model.position.y)
        self.path_z = list(model.position.z)
        self.model_valid = True
      else:
        self.model_valid = False

      if len(model.laneLines) == 4:
        self.lane_lines = [
          (list(line.x), list(line.y), list(line.z)) for line in model.laneLines
        ]
        self.lane_line_probs = list(model.laneLineProbs)
        self.left_lane_x = list(model.laneLines[1].x)
        self.left_lane_y = list(model.laneLines[1].y)

        self.right_lane_x = list(model.laneLines[2].x)
        self.right_lane_y = list(model.laneLines[2].y)

      self.road_edges = [
        (list(edge.x), list(edge.y), list(edge.z)) for edge in model.roadEdges
      ]
      self.road_edge_stds = list(model.roadEdgeStds)

    if self.sm.updated['radarState']:
      radar_state = self.sm['radarState']
      self.leads = [
        {
          "present": bool(lead.present),
          "d_rel": float(lead.dRel),
          "y_rel": float(lead.yRel),
          "v_rel": float(lead.vRel),
        }
        for lead in (radar_state.leadOne, radar_state.leadTwo)
      ]

  def _update_camera_intrinsics(self):
    if self.device_type == "unknown":
      self.camera_intrinsics = None
      return
    camera = DEVICE_CAMERAS.get((self.device_type, self.camera_sensor))
    self.camera_intrinsics = camera.narrow_road.intrinsics.copy() if camera is not None else None

  def _update_navi_school_zone(self, navi_data):
    """Mirror SpeedLimiter's process-local school-zone state from naviData."""
    if self.nda_state <= 0:
      self._navi_school_zone = False
      self._navi_last_road_name = ""
      self.school_zone = self.road_signs == 1
      return

    if self.cam_type == 20:
      self._navi_school_zone = True
    elif self.cam_type == 21:
      self._navi_school_zone = False

    current_road_name = str(getattr(navi_data, 'currentRoadName', '') or '')
    if self._navi_school_zone:
      road_changed = bool(
        self._navi_last_road_name and current_road_name and
        current_road_name != self._navi_last_road_name
      )
      new_camera_event = self.cam_type not in (20, 21) and self.cam_limit_speed_left_dist > 0
      if road_changed or new_camera_event:
        self._navi_school_zone = False

    self._navi_last_road_name = current_road_name
    self.school_zone = self.road_signs == 1 or self._navi_school_zone

  def _update_loop(self):
    while self._running:
      try:
        self._update_once()
      except Exception as e:
        cloudlog.error(f"ClusterModels update error: {e}")
      time.sleep(0.01)

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
      "pre_enabled_or_overriding": self.pre_enabled_or_overriding,
      "lat_active": self.lat_active,
      "cruise_available": self.cruise_available,
      "reverse": self.reverse,
      "left_blinker": self.left_blinker,
      "right_blinker": self.right_blinker,
      "left_blindspot": self.left_blindspot,
      "right_blindspot": self.right_blindspot,
      "brake_pressed": self.brake_pressed,
      "gas_pressed": self.gas_pressed,
      "steering_pressed": self.steering_pressed,
      "steering_angle": self.steering_angle,
      "cruise_speed": self.cruise_speed,
      "set_speed": self.set_speed,
      "is_cruise_set": self.is_cruise_set,
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
      "cam_type": self.cam_type,
      "speed_camera": self.speed_camera,
      "school_zone": self.school_zone,
      "speed_bump": self.speed_bump,
      "ignore_limit_timer": self.ignore_limit_timer,
    }

  def get_path_data(self):
    calib_transform = None
    if self.camera_intrinsics is not None:
      calib_transform = np.asarray(self.camera_intrinsics @ self.view_from_calib, dtype=np.float32)

    return {
      "path_x": self.path_x,
      "path_y": self.path_y,
      "path_z": self.path_z,
      "left_lane_x": self.left_lane_x,
      "left_lane_y": self.left_lane_y,
      "right_lane_x": self.right_lane_x,
      "right_lane_y": self.right_lane_y,
      "lane_lines": self.lane_lines,
      "lane_line_probs": self.lane_line_probs,
      "road_edges": self.road_edges,
      "road_edge_stds": self.road_edge_stds,
      "leads": self.leads,
      "camera_height": self.camera_height,
      "calib_transform": calib_transform,
    }
