#!/usr/bin/env python3
import math
import numpy as np
from collections import deque
from typing import Any

import capnp
from openpilot.cereal import messaging, log
from opendbc.car.structs import car
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL, Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog

# Default lead acceleration decay set to 50% at 1s
_LEAD_ACCEL_TAU = 1.5

# radar tracks
SPEED, ACCEL = 0, 1  # Kalman filter states enum

# stationary qualification parameters
V_EGO_STATIONARY = 4.  # no stationary object flag below this speed

RADAR_TO_CAMERA = 1.52  # RADAR is ~ 1.5m ahead from center of mesh frame

STICKY_SELECTED_COUNT_MAX = int(2.0 / DT_MDL)
STICKY_MAX_DPATH = 0.8
STICKY_FAR_DREL = 60.0
STICKY_MAX_DPATH_FAR = 1.2
STICKY_PATH_Y_STD_GAIN = 0.5

CUTIN_STICKY_FRAMES = int(0.7 / DT_MDL)
CUTIN_ENTER_PROB_GAIN = 0.12
CUTIN_KEEP_FUTURE_IN_LANE_PROB = 0.12
CUTIN_KEEP_MAX_DPATH_FUTURE = 1.6
CUTIN_KEEP_MAX_MOVING_AWAY = 0.3
CORNER_ACCEL_MIN_TRACK_AGE = 6
CORNER_ACCEL_MAX_ABS_DPATH = 1.5
CORNER_ACCEL_MAX_ABS_ALEAD = 3.0
CUTIN_PROMOTE_DREL_MARGIN = 1.0
CORNER_FRONT_MATCH_PROMOTE_DREL_MARGIN = 8.0
CUTIN_DEFAULT_CONFIRM_S = 0.20
CUTIN_DEFAULT_MIN_TRACK_AGE_S = 0.25
CUTIN_DEFAULT_ENTER_MIN_X = 1.0
CUTIN_DEFAULT_ENTER_MAX_X = 55.0
CUTIN_DEFAULT_ENTER_MIN_ABS_DPATH = 1.5
CUTIN_DEFAULT_ENTER_FUTURE_IN_LANE_PROB = 0.20
CUTIN_DEFAULT_ENTER_CENTERING_GAIN = 0.20
RADAR_ONLY_FALLBACK_VISION_PROB = 0.55

CENTER_LEAD_NEAR_DPATH_LIMIT = 1.2
CENTER_LEAD_FAR_DPATH_LIMIT = 0.9
CENTER_LEAD_FAR_DREL = 60.0
CENTER_LEAD_NEAR_IN_LANE_PROB = 0.3
CENTER_LEAD_FAR_IN_LANE_PROB = 0.45
RADAR_ONLY_CENTER_DPATH_NEAR_LIMIT = 1.1
RADAR_ONLY_CENTER_DPATH_MID_LIMIT = 0.9
RADAR_ONLY_CENTER_DPATH_FAR_LIMIT = 0.75
RADAR_ONLY_CENTER_MID_DREL = 60.0
RADAR_ONLY_CENTER_FAR_DREL = 80.0
RADAR_ONLY_CENTER_MAX_DREL = 100.0

RADAR_CENTER_PROMOTION_MAX_LANE_CENTER_OFFSET = 1.5
RADAR_CENTER_PROMOTION_RECEDING_MAX_DREL = 45.0
RADAR_CENTER_PROMOTION_RECEDING_VREL = 0.5
CORNER_235_TRACK_ID_START = 200
CORNER_235_TRACK_ID_END = 220
CORNER_180_TRACK_ID_START = 240
CORNER_180_TRACK_ID_END = 250

CORNER_FRONT_MATCH_DREL = 3.0
CORNER_FRONT_MATCH_VREL = 2.0
CORNER_CENTER_MIN_AGE = int(0.25 / DT_MDL)
CORNER_STOPPED_MIN_AGE = int(0.35 / DT_MDL)
CORNER_STOPPED_MIN_DREL = 5.0
CORNER_STOPPED_MAX_DREL = 120.0
CORNER_STOPPED_MAX_VLEAD = 1.8
CORNER_STOPPED_MAX_YVREL = 0.8
CORNER_STOPPED_NEAR_DPATH_LIMIT = 1.0
CORNER_STOPPED_FAR_DPATH_LIMIT = 0.75
CORNER_STOPPED_NEAR_IN_LANE_PROB = 0.35
CORNER_STOPPED_FAR_IN_LANE_PROB = 0.5
CORNER_STOPPED_FAR_DREL = 60.0
CORNER_VISION_KEEP_PROB = 0.75
FRONT_RADAR_VISION_MATCH_MIN_PROB = 0.4


def laplacian_pdf(x: float, mu: float, b: float):
  diff = abs(x - mu) / max(b, 1e-4)
  return 0.0 if diff > 50.0 else math.exp(-diff)


def clamp(x: float, lo: float, hi: float) -> float:
  return float(np.clip(x, lo, hi))


def calculate_d_path(d_rel: float, y_rel: float, md_arrays: dict[str, np.ndarray]) -> float:
  if len(md_arrays.get('lane_xs', [])) == 0:
    return 0.0
  left_lane_y = np.interp(d_rel, md_arrays['lane_xs'], md_arrays['left_ys'])
  right_lane_y = np.interp(d_rel, md_arrays['lane_xs'], md_arrays['right_ys'])
  center_y = (left_lane_y + right_lane_y) / 2.0
  return float(y_rel + center_y)


def is_radar_center_promotion_safe(lead: dict[str, Any], md_arrays: dict[str, np.ndarray]) -> bool:
  d_rel = float(lead.get("dRel", 999.0))
  y_rel = float(lead.get("yRel", 999.0))
  d_path = calculate_d_path(d_rel, y_rel, md_arrays)
  v_rel = float(lead.get("vRel", 999.0))

  if abs(d_path - y_rel) >= RADAR_CENTER_PROMOTION_MAX_LANE_CENTER_OFFSET:
    return False

  return d_rel <= RADAR_CENTER_PROMOTION_RECEDING_MAX_DREL or v_rel <= RADAR_CENTER_PROMOTION_RECEDING_VREL


EMPTY_LEAD = {
  "dRel": 0.0,
  "yRel": 0.0,
  "vRel": 0.0,
  "vLead": 0.0,
  "vLeadK": 0.0,
  "aLeadK": 0.0,
  "present": False,
  "aLeadTau": 0.0,
  "modelProb": 0.0,
  "radar": False,
  "radarTrackId": -1,
}


def empty_lead():
  return EMPTY_LEAD.copy()


def select_side_leads(front_leads: list[dict[str, Any]], corner_leads: list[dict[str, Any]],
                      corner_tracks_available: bool) -> list[dict[str, Any]]:
  return corner_leads if corner_tracks_available else front_leads


def pick_side_lead(leads: list[dict[str, Any]], md_arrays: dict[str, np.ndarray]) -> dict[str, Any]:
  return min(
    (ld for ld in leads if ld['dRel'] > 5 and abs(calculate_d_path(ld['dRel'], ld['yRel'], md_arrays)) < 3.5),
    key=lambda d: d['dRel'],
    default=empty_lead()
  )


class Track:
  def __init__(self, identifier: int):
    self.identifier = identifier
    self.cnt = 0
    self.aLeadTau = FirstOrderFilter(_LEAD_ACCEL_TAU, 0.45, DT_MDL)

    self.is_stopped_car_count = 0
    self.selected_count = 0
    self.cut_in_count = 0
    self.in_lane_prob = 0.0
    self.in_lane_prob_future = 0.0

    self.dRel = 0.0
    self.yRel = 0.0
    self.vRel = 0.0
    self.vLead = 0.0
    self.vLeadK = 0.0
    self.aLeadK = 0.0
    self.yvLead = 0.0
    self.dRel_future = 0.0
    self.yRel_future = 0.0
    self.dPath_future = 0.0
    self.dPath = 0.0
    self.sticky_dPath = 0.0
    self.sticky_path_y_std = 0.0

    self._vLead_last = 0.0
    self._vLead_filt = 0.0
    self._vLead_filt_init = False

  def update(self, md_arrays, pt, ready, v_ego):
    prev_dRel = self.dRel
    prev_yRel = self.yRel
    prev_vLead = self.vLead

    self.dRel = pt.dRel
    self.yRel = pt.yRel
    self.vRel = pt.vRel

    self.vLead = self.vLeadK = pt.vRel + v_ego

    if self.cnt > 0:
      a_lead_raw = (self.vLead - prev_vLead) / DT_MDL
      self.aLeadK = 0.1 * a_lead_raw + 0.9 * self.aLeadK
      self.yvLead = (self.yRel - prev_yRel) / DT_MDL
    else:
      self.aLeadK = 0.0
      self.yvLead = 0.0

    if self.selected_count > 0:
      if (abs(self.dRel - prev_dRel) > 5.0 or
        abs(self.yRel - prev_yRel) > 2.0 or
        abs(self.vLead - prev_vLead) > 7.0):
        self.selected_count = 0
        self.is_stopped_car_count = 0

    self.yRel_future = self.yRel + self.yvLead * 1.0
    self.dRel_future = self.dRel + self.vLead * 1.0
    if ready:
      self.d_path(md_arrays)
      if self.selected_count > 0:
        self.sticky_dPath, self.sticky_path_y_std = self.path_d_path(md_arrays)

      if self.selected_count > 0 and abs(self.sticky_dPath) > self.sticky_dpath_limit():
        self.selected_count = 0
        self.is_stopped_car_count = 0

    a_lead_threshold = 0.5
    if abs(self.aLeadK) < a_lead_threshold:
      self.aLeadTau.x = _LEAD_ACCEL_TAU
    else:
      self.aLeadTau.update(0.0)

    self.cnt += 1

  def d_path(self, md_arrays):
    lane_xs = md_arrays['lane_xs']
    left_ys = md_arrays['left_ys']
    right_ys = md_arrays['right_ys']

    def d_path_interp(dRel, yRel):
      left_lane_y = np.interp(dRel, lane_xs, left_ys)
      right_lane_y = np.interp(dRel, lane_xs, right_ys)
      center_y = (left_lane_y + right_lane_y) / 2.0
      lane_half_width = max(0.1, abs(right_lane_y - left_lane_y) / 2.0)
      dist_from_center = yRel + center_y
      in_lane_prob = max(0.0, 1.0 - (abs(dist_from_center) / lane_half_width))
      return float(dist_from_center), float(in_lane_prob)

    self.dPath, self.in_lane_prob = d_path_interp(self.dRel, self.yRel)
    self.dPath_future, self.in_lane_prob_future = d_path_interp(self.dRel_future, self.yRel_future)

  def path_d_path(self, md_arrays) -> tuple[float, float]:
    path_y = float(np.interp(self.dRel, md_arrays['pos_x'], md_arrays['pos_y']))
    path_y_std = float(np.interp(self.dRel, md_arrays['pos_x'], md_arrays['pos_y_std'])) if len(
      md_arrays['pos_y_std']) > 0 else 0.0
    return float(self.yRel + path_y), path_y_std

  def sticky_dpath_limit(self) -> float:
    if self.dRel < STICKY_FAR_DREL:
      return STICKY_MAX_DPATH
    return float(np.clip(STICKY_MAX_DPATH + STICKY_PATH_Y_STD_GAIN * self.sticky_path_y_std,
                         STICKY_MAX_DPATH, STICKY_MAX_DPATH_FAR))

  def vlead_for_matching(self, dv_max: float = 4.0, alpha: float = 0.35) -> float:
    v = float(self.vLead)

    if self.cnt < 2:
      return v

    if not self._vLead_filt_init:
      self._vLead_last = v
      self._vLead_filt = v
      self._vLead_filt_init = True
      return v

    v_last = self._vLead_last
    self._vLead_last = v

    v_clamped = clamp(v, v_last - dv_max, v_last + dv_max)
    self._vLead_filt = alpha * v_clamped + (1.0 - alpha) * self._vLead_filt
    return float(self._vLead_filt)

  def get_RadarState(self, model_prob: float = 0.0, vision_y_rel=0.0):
    return {
      "dRel": float(self.dRel),
      "yRel": float(self.yRel) if self.yRel != 0.0 else vision_y_rel,
      "vRel": float(self.vRel),
      "vLead": float(self.vLead),
      "vLeadK": float(self.vLeadK),
      "aLeadK": float(self.aLeadK),
      "aLeadTau": float(self.aLeadTau.x),
      "present": True,
      "modelProb": model_prob,
      "radar": True,
      "radarTrackId": self.identifier,
    }

  def potential_low_speed_lead(self, v_ego: float):
    return abs(self.yRel) < 1.0 and (v_ego < V_EGO_STATIONARY) and (0.75 < self.dRel < 25)

  def __str__(self):
    return f"x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}"


def match_vision_to_track(v_ego: float, lead: capnp._DynamicStructReader, lead_prob: float,
                          tracks: dict[int, Track], update_counters: bool = True):
  if not tracks:
    return None

  offset_vision_dist = float(lead.x[0] - RADAR_TO_CAMERA)

  max_vision_dist = max(offset_vision_dist * 1.25, 5.0)
  min_vision_dist = max(offset_vision_dist * 0.80, 1.0)
  max_vision_dist2 = max(offset_vision_dist * 1.45, 5.0)
  min_vision_dist2 = 1.5

  vel_tol = float(max(lead.v[0] * np.interp(lead_prob, [0.8, 0.98], [0.3, 0.5]), 5.0))
  vel_guard = max(vel_tol * 3.0, 20.0)

  def dist_sane(t: Track, wide: bool = False) -> bool:
    if wide:
      return (min_vision_dist2 < t.dRel < max_vision_dist2)
    return (min_vision_dist < t.dRel < max_vision_dist)

  def y_sane(t: Track, wide: bool = False) -> bool:
    lim = 4.0 if wide else 2.0
    return abs(t.yRel + float(lead.y[0])) < lim

  def vel_sane(t: Track) -> bool:
    v_vis = float(lead.v[0])
    v_trk = float(t.vLead)
    dv = abs(v_trk - v_vis)

    if dv < vel_tol:
      return True

    moving = (v_trk > 3.0)
    if not moving:
      return False

    if dv > vel_guard:
      return False

    if hasattr(t, "dPath") and (t.in_lane_prob < 0.25):
      return False

    return True

  def score_pair(t: Track):
    pd = laplacian_pdf(float(t.dRel), offset_vision_dist, float(lead.xStd[0]))
    py = laplacian_pdf(float(t.yRel), -float(lead.y[0]), float(lead.yStd[0]))
    py2 = laplacian_pdf(float(t.yRel), -float(lead.y[0]), float(lead.yStd[0]) * 2.0)

    v_use = float(t.vlead_for_matching())
    pv = laplacian_pdf(v_use, float(lead.v[0]), float(lead.vStd[0]))

    s1 = pd * py * pv
    s2 = pd * py2 * pv
    return s1, s2

  first_track, second_track, extra_track = None, None, None
  first_score, second_score, extra_score = -1e18, -1e18, -1e18

  for t in tracks.values():
    s1, s2 = score_pair(t)

    if s1 > first_score:
      second_track, second_score = first_track, first_score
      first_track, first_score = t, s1
    elif s1 > second_score:
      second_track, second_score = t, s1

    if s2 > extra_score:
      extra_track, extra_score = t, s2

  if first_track is None or first_score < 1e-4:
    return None

  best_track = None

  if dist_sane(first_track) and vel_sane(first_track):
    select_second_track = False
    if second_track is not None and vel_sane(second_track) and second_track.in_lane_prob > 0.3:
      if second_track.cnt > 5 and offset_vision_dist * 0.5 < second_track.dRel < first_track.dRel:
        select_second_track = True

    if select_second_track:
      best_track = second_track
    elif y_sane(first_track):
      if lead_prob > 0.5:
        best_track = first_track
      elif lead_prob > 0.4 and first_track.selected_count > 0:
        best_track = first_track
    elif lead_prob > 0.6:
      best_track = first_track

  if best_track is None and dist_sane(first_track) and y_sane(first_track, wide=True):
    if (second_track is not None and second_score > 1e-5 and
      dist_sane(second_track) and y_sane(second_track) and vel_sane(second_track)):
      best_track = second_track
    elif first_track.selected_count > 0:
      best_track = first_track
    else:
      first_track.is_stopped_car_count += 2
      if first_track.is_stopped_car_count > int(1.0 / DT_MDL):
        best_track = first_track

  if best_track is None and offset_vision_dist < 90.0 and lead_prob > 0.65:
    if (extra_track is not None and extra_score > first_score and
      dist_sane(extra_track, wide=True) and vel_sane(extra_track) and y_sane(extra_track, wide=True)):
      best_track = extra_track

    elif dist_sane(first_track, wide=True) and vel_sane(first_track) and y_sane(first_track, wide=True):
      best_track = first_track

    elif (second_track is not None and second_score > 1e-4 and
          dist_sane(second_track, wide=True) and vel_sane(second_track) and y_sane(second_track, wide=True)):
      best_track = second_track

  if update_counters:
    for t in tracks.values():
      if t is best_track and best_track is not None:
        t.selected_count = min(t.selected_count + 1, STICKY_SELECTED_COUNT_MAX)
      elif best_track is not None:
        t.selected_count = 0
        t.is_stopped_car_count = max(0, t.is_stopped_car_count - 1)

  return best_track


def get_RadarState_from_vision(md_arrays, lead_msg: capnp._DynamicStructReader, v_ego: float, model_v_ego: float,
                               lead_prob: float):
  lead_v_rel_pred = lead_msg.v[0] - model_v_ego
  dRel = float(lead_msg.x[0] - RADAR_TO_CAMERA)
  yRel = float(-lead_msg.y[0])
  return {
    "dRel": float(dRel),
    "yRel": yRel,
    "vRel": float(lead_v_rel_pred),
    "vLead": float(v_ego + lead_v_rel_pred),
    "vLeadK": float(v_ego + lead_v_rel_pred),
    "aLeadK": float(lead_msg.a[0]),
    "aLeadTau": 0.3,
    "modelProb": float(lead_prob),
    "present": True,
    "radar": False,
    "radarTrackId": -1,
  }


class RadarD:
  def __init__(self, delay: float = 0.0):
    self.current_time = 0.0

    self.tracks: dict[int, Track] = {}

    self.lead_prob_filters = [FirstOrderFilter(0.0, 0.2, DT_MDL) for _ in range(2)]

    self.v_ego = 0.0
    self.v_ego_hist = deque([0.0], maxlen=int(round(delay / DT_MDL)) + 1)
    self.last_v_ego_frame = -1

    self.radar_state: capnp._DynamicStructBuilder | None = None
    self.radar_state_valid = False

    self.ready = False

    self.params = Params()
    self.enable_radar_tracks = self.params.get_bool("RadarTrackEnable")
    self.enable_corner_radar = self.params.get_bool("IsHda2")

    self.update_counter = 0

    self.md_arrays = {
      'pos_x': np.array([]),
      'pos_y': np.array([]),
      'pos_y_std': np.array([]),
      'lane_xs': np.array([]),
      'left_ys': np.array([]),
      'right_ys': np.array([]),
    }

    self.cutin_confirm_frames = max(1, int(round(CUTIN_DEFAULT_CONFIRM_S / DT_MDL)))
    self.cutin_min_track_age = max(1, int(round(CUTIN_DEFAULT_MIN_TRACK_AGE_S / DT_MDL)))
    self.cutin_enter_min_x = CUTIN_DEFAULT_ENTER_MIN_X
    self.cutin_enter_max_x = CUTIN_DEFAULT_ENTER_MAX_X
    self.cutin_enter_min_abs_dpath = CUTIN_DEFAULT_ENTER_MIN_ABS_DPATH
    self.cutin_enter_future_in_lane_prob = CUTIN_DEFAULT_ENTER_FUTURE_IN_LANE_PROB
    self.cutin_enter_centering_gain = CUTIN_DEFAULT_ENTER_CENTERING_GAIN

    self.radar_detected = False
    self.lead_one_front_radar_vision_match = False
    self.leadCenter = None
    self.leadTwo = None
    self.leadCutIn = empty_lead()
    self.cornerLeadStopped = empty_lead()
    self.corner_tracks_available = False

  def update(self, sm: messaging.SubMaster, rr: car.RadarData):
    self.ready = sm.seen['modelV2']
    self.current_time = 1e-9 * max(sm.logMonoTime.values())

    self.enable_radar_tracks = self.params.get_bool("RadarTrackEnable")
    self.enable_corner_radar = self.params.get_bool("IsHda2")

    self.detect_cut_in = self.enable_corner_radar
    vision_only_mode = not self.enable_radar_tracks

    md = sm['modelV2']
    leads_v3 = md.leadsV3

    if self.ready and sm.updated['modelV2']:
      self.md_arrays['pos_x'] = np.array(md.position.x)
      self.md_arrays['pos_y'] = np.array(md.position.y)
      self.md_arrays['pos_y_std'] = np.array(md.position.yStd) if len(md.position.yStd) > 0 else np.array([])
      self.md_arrays['lane_xs'] = np.array(md.laneLines[1].x)
      self.md_arrays['left_ys'] = np.array(md.laneLines[1].y)
      self.md_arrays['right_ys'] = np.array(md.laneLines[2].y)

    if sm.recv_frame['carState'] != self.last_v_ego_frame:
      self.v_ego = sm['carState'].vEgo
      self.v_ego_hist.append(self.v_ego)
      self.last_v_ego_frame = sm.recv_frame['carState']

    if vision_only_mode:
      self.tracks.clear()
    else:
      valid_ids = set()
      for pt in rr.points:
        track_id = pt.trackId
        valid_ids.add(track_id)

        if track_id not in self.tracks:
          self.tracks[track_id] = Track(track_id)

        self.tracks[track_id].update(self.md_arrays, pt, self.ready, self.v_ego)

      for tid in list(self.tracks.keys()):
        if tid not in valid_ids:
          self.tracks.pop(tid)

    radar_state_valid = sm.all_checks()
    if not radar_state_valid and self.radar_state_valid:
      print("radarState invalid: sm.all_checks() failed")

    self.radar_state_valid = radar_state_valid
    if not self.radar_state_valid:
      self.radar_state = log.RadarState.new_message()

    self.radar_state.mdMonoTime = sm.logMonoTime['modelV2']
    self.radar_state.radarErrors = rr.errors
    self.radar_state.carStateMonoTime = sm.logMonoTime['carState']

    if len(md.velocity.x) > 0:
      model_v_ego = md.velocity.x[0]
    else:
      model_v_ego = self.v_ego

    if len(leads_v3) > 1:
      for i in range(2):
        lead_prob = leads_v3[i].prob
        if lead_prob > self.lead_prob_filters[i].x:
          self.lead_prob_filters[i].x = lead_prob
        else:
          self.lead_prob_filters[i].update(lead_prob)

      corner_radar_enabled = self.enable_corner_radar

      alive_tracks = {tid: trk for tid, trk in self.tracks.items() if trk.cnt > 2}
      front_tracks = {tid: trk for tid, trk in alive_tracks.items() if not self._is_corner_track(trk)}
      corner_tracks = {tid: trk for tid, trk in alive_tracks.items() if
                       corner_radar_enabled and self._is_corner_track(trk)}
      self.corner_tracks_available = len(corner_tracks) > 0

      self.radar_state.leadOne, self.radar_detected = self.get_lead(sm['carState'], self.md_arrays, front_tracks, 0,
                                                                    leads_v3[0], model_v_ego,
                                                                    self.lead_prob_filters[0].x,
                                                                    low_speed_override=False)
      self.radar_state.leadTwo, _ = self.get_lead(sm['carState'], self.md_arrays, front_tracks, 1, leads_v3[1],
                                                  model_v_ego, self.lead_prob_filters[1].x, low_speed_override=False)

      self.lane_line_available = md.laneLineProbs[1] > 0.5 and md.laneLineProbs[2] > 0.5
      compute_tracks = dict(front_tracks)
      compute_tracks.update(corner_tracks)

      self.compute_leads(self.v_ego, compute_tracks, md, self.lead_prob_filters[0].x, front_tracks)
      if self.leadTwo is not None:
        self.radar_state.leadTwo = self.leadTwo
      if self.enable_radar_tracks or (self.cornerLeadStopped and self.cornerLeadStopped.get("present")):
        self._pick_lead_one_from_state()

  def publish(self, pm: messaging.PubMaster):
    assert self.radar_state is not None

    radar_msg = messaging.new_message("radarState")
    radar_msg.valid = self.radar_state_valid
    radar_msg.radarState = self.radar_state
    pm.send("radarState", radar_msg)

  def _is_corner_track(self, t: Track) -> bool:
    return (
      CORNER_235_TRACK_ID_START <= t.identifier < CORNER_235_TRACK_ID_END or
      CORNER_180_TRACK_ID_START <= t.identifier < CORNER_180_TRACK_ID_END
    )

  def _matching_front_track(self, corner: Track, front_tracks: dict[int, Track]) -> Track | None:
    matches = []
    for t in front_tracks.values():
      if t.cnt <= 2:
        continue
      if abs(t.dRel - corner.dRel) > CORNER_FRONT_MATCH_DREL:
        continue
      if abs(t.vRel - corner.vRel) > CORNER_FRONT_MATCH_VREL:
        continue
      matches.append(t)

    if not matches:
      return None

    return min(matches, key=lambda t: abs(t.dRel - corner.dRel) + abs(t.vRel - corner.vRel))

  def _corner_in_lane_ok(self, t: Track, stopped: bool = False, matched_front: bool = False) -> bool:
    if not self.lane_line_available:
      return False

    if stopped:
      dpath_limit = CORNER_STOPPED_NEAR_DPATH_LIMIT
      in_lane_min = CORNER_STOPPED_NEAR_IN_LANE_PROB
      if t.dRel > CORNER_STOPPED_FAR_DREL:
        dpath_limit = CORNER_STOPPED_FAR_DPATH_LIMIT
        in_lane_min = CORNER_STOPPED_FAR_IN_LANE_PROB
      if matched_front:
        in_lane_min = max(0.2, in_lane_min - 0.15)
        dpath_limit += 0.15
      return abs(t.dPath) < dpath_limit and t.in_lane_prob > in_lane_min

    return self._is_center_lead_candidate(t)

  def _is_corner_center_candidate(self, t: Track) -> bool:
    return (
      self._is_corner_track(t) and
      t.cnt >= CORNER_CENTER_MIN_AGE and
      3.0 < t.dRel < RADAR_ONLY_CENTER_MAX_DREL and
      t.vLead > 2.0 and
      self._corner_in_lane_ok(t)
    )

  def _is_corner_stopped_candidate(self, t: Track, matched_front: bool = False) -> bool:
    return (
      self._is_corner_track(t) and
      t.cnt >= CORNER_STOPPED_MIN_AGE and
      CORNER_STOPPED_MIN_DREL < t.dRel < CORNER_STOPPED_MAX_DREL and
      abs(t.vLead) < CORNER_STOPPED_MAX_VLEAD and
      abs(t.yvLead) < CORNER_STOPPED_MAX_YVREL and
      self._corner_in_lane_ok(t, stopped=True, matched_front=matched_front)
    )

  def _corner_track_accel_allowed(self, t: Track) -> bool:
    return (
      t.cnt >= CORNER_ACCEL_MIN_TRACK_AGE and
      self._track_is_closer_than_lead_one(t) and
      abs(t.dPath) < CORNER_ACCEL_MAX_ABS_DPATH and
      math.isfinite(t.aLeadK) and
      abs(t.aLeadK) < CORNER_ACCEL_MAX_ABS_ALEAD
    )

  def _corner_lead_from_track(self, t: Track, model_prob: float = 0.0, vision_y_rel: float = 0.0,
                              use_accel: bool = True) -> dict[str, Any]:
    ld = t.get_RadarState(model_prob, vision_y_rel)
    if use_accel and self._corner_track_accel_allowed(t):
      a_lead = float(np.clip(t.aLeadK, -CORNER_ACCEL_MAX_ABS_ALEAD, CORNER_ACCEL_MAX_ABS_ALEAD))
      ld["aLeadK"] = a_lead
    else:
      ld["aLeadK"] = 0.0
    ld["aLeadTau"] = _LEAD_ACCEL_TAU
    return ld

  def _corner_stopped_lead_from_track(self, t: Track, lead_prob: float) -> dict[str, Any]:
    ld = self._corner_lead_from_track(t, min(0.04, lead_prob), 0.0, use_accel=False)
    ld["modelProb"] = 0.04
    ld["vLead"] = 0.0
    ld["vLeadK"] = 0.0
    ld["vRel"] = -float(self.v_ego)
    return ld

  def get_sticky_track(self, tracks: dict[int, Track]) -> Track | None:
    sticky_tracks = []
    for t in tracks.values():
      if t.selected_count > 0 and abs(t.sticky_dPath) > t.sticky_dpath_limit():
        t.selected_count = 0
        t.is_stopped_car_count = 0
        continue

      if t.cnt > 2 and t.selected_count > 0 and 1.0 < t.dRel < 150.0:
        sticky_tracks.append(t)

    if not sticky_tracks:
      return None

    return max(sticky_tracks, key=lambda t: (t.selected_count, -t.dRel))

  def get_lead(self, CS, md_arrays, tracks: dict[int, Track], index: int, lead_msg: capnp._DynamicStructReader,
               model_v_ego: float, lead_prob: float, low_speed_override: bool = True) -> dict[str, Any]:

    v_ego = self.v_ego
    ready = self.ready
    if index == 0:
      self.lead_one_front_radar_vision_match = False
    front_radar_vision_match = False

    if not self.enable_radar_tracks:
      track_scc = tracks.get(0)
    else:
      track_scc = tracks.pop(0, None)

    if len(tracks) > 0 and ready and lead_prob > .4:
      track = match_vision_to_track(v_ego, lead_msg, lead_prob, tracks, update_counters=(index == 0))
    else:
      track = None
    front_radar_vision_match = track is not None

    sticky_track = False
    if track is None and index == 0 and not self.corner_tracks_available:
      track = self.get_sticky_track(tracks)
      if track is not None:
        sticky_track = True
        front_radar_vision_match = False
        track.selected_count = min(track.selected_count + 1, STICKY_SELECTED_COUNT_MAX)

    if (track is None or (lead_prob < .6 and not sticky_track)) and track_scc is not None and track_scc.cnt > 2:
      if not self.enable_radar_tracks or track_scc.vLead < 5.0:
        track = track_scc
        front_radar_vision_match = False

    lead_dict = empty_lead()
    radar = False
    if track is not None:
      vision_y_rel = float(-lead_msg.y[0]) if ready else 0.0
      lead_dict = track.get_RadarState(lead_prob, vision_y_rel)
      radar = True
    elif (track is None) and ready and (lead_prob > .5):
      lead_dict = get_RadarState_from_vision(md_arrays, lead_msg, v_ego, model_v_ego, lead_prob)

    if low_speed_override:
      low_speed_tracks = [c for c in tracks.values() if c.potential_low_speed_lead(v_ego)]
      if len(low_speed_tracks) > 0:
        closest_track = min(low_speed_tracks, key=lambda c: c.dRel)

        if (not lead_dict['present']) or (closest_track.dRel < lead_dict['dRel']):
          vision_y_rel = float(-lead_msg.y[0]) if ready else 0.0
          lead_dict = closest_track.get_RadarState(lead_prob, vision_y_rel)
          front_radar_vision_match = False

    if index == 0 and front_radar_vision_match:
      self.lead_one_front_radar_vision_match = True
    return lead_dict, radar

  def _cutin_is_closer_or_matches_lead_one(self, t: Track, matched_front: bool = False) -> bool:
    if self._track_is_closer_than_lead_one(t):
      return True
    if not matched_front:
      return False

    lead_one = self.radar_state.leadOne
    if not lead_one.present or not lead_one.radar:
      return False
    if int(lead_one.radarTrackId) >= CORNER_235_TRACK_ID_START:
      return False

    return (
      abs(t.dRel - float(lead_one.dRel)) < CORNER_FRONT_MATCH_DREL and
      abs(t.vRel - float(lead_one.vRel)) < CORNER_FRONT_MATCH_VREL
    )

  def _is_cutin_enter_candidate(self, t: Track, matched_front: bool = False) -> bool:
    if not self.detect_cut_in or not self.lane_line_available or not self._is_corner_track(t):
      return False
    if not self._cutin_is_closer_or_matches_lead_one(t, matched_front):
      return False
    if t.cnt < self.cutin_min_track_age:
      return False
    if not (self.cutin_enter_min_x < t.dRel < self.cutin_enter_max_x and t.vLead > 4.0):
      return False
    if abs(t.dPath) < self.cutin_enter_min_abs_dpath:
      return False
    if t.in_lane_prob_future < self.cutin_enter_future_in_lane_prob:
      return False
    if (t.in_lane_prob_future - t.in_lane_prob) < CUTIN_ENTER_PROB_GAIN:
      return False
    if (abs(t.dPath) - abs(t.dPath_future)) < self.cutin_enter_centering_gain:
      return False
    return True

  def _is_cutin_keep_candidate(self, t: Track, matched_front: bool = False) -> bool:
    if not self.detect_cut_in or not self.lane_line_available or not self._is_corner_track(t):
      return False
    if not self._cutin_is_closer_or_matches_lead_one(t, matched_front):
      return False
    if not (2.5 < t.dRel < 55.0 and t.vLead > 2.0):
      return False

    moving_away = abs(t.dPath_future) - abs(t.dPath)
    if moving_away > CUTIN_KEEP_MAX_MOVING_AWAY:
      return False

    return (
      t.in_lane_prob_future > CUTIN_KEEP_FUTURE_IN_LANE_PROB or
      abs(t.dPath_future) < CUTIN_KEEP_MAX_DPATH_FUTURE
    )

  def _update_cutin_sticky(self, t: Track, matched_front: bool = False) -> bool:
    if self._is_cutin_enter_candidate(t, matched_front):
      t.cut_in_count = min(t.cut_in_count + 1, CUTIN_STICKY_FRAMES)
    elif t.cut_in_count > 0 and self._is_cutin_keep_candidate(t, matched_front):
      t.cut_in_count = max(t.cut_in_count - 1, 0)
    else:
      t.cut_in_count = 0

    return t.cut_in_count >= self.cutin_confirm_frames

  def _cutin_can_replace_lead_one(self, cutin: dict[str, Any]) -> bool:
    lead_one = self.radar_state.leadOne
    if not lead_one.present:
      return True
    if self._lead_one_has_front_radar_vision_match():
      return False

    return cutin["dRel"] + CUTIN_PROMOTE_DREL_MARGIN < lead_one.dRel

  def _track_is_closer_than_lead_one(self, t: Track) -> bool:
    lead_one = self.radar_state.leadOne
    if not lead_one.present:
      return True
    return t.dRel + CUTIN_PROMOTE_DREL_MARGIN < lead_one.dRel

  def _lead_is_closer_than_lead_one(self, lead: dict[str, Any]) -> bool:
    lead_one = self.radar_state.leadOne
    if not lead_one.present:
      return True
    return lead["dRel"] + CUTIN_PROMOTE_DREL_MARGIN < lead_one.dRel

  def _corner_stopped_can_replace_lead_one(self, stopped: dict[str, Any]) -> bool:
    lead_one = self.radar_state.leadOne
    if not lead_one.present:
      return True

    if stopped["dRel"] + self._corner_promote_drel_margin() < lead_one.dRel:
      return True

    if lead_one.radar:
      return False

    vision_prob = lead_one.modelProb if lead_one.present else 0.0
    same_object = abs(stopped["dRel"] - lead_one.dRel) < CORNER_FRONT_MATCH_DREL
    return same_object and vision_prob < CORNER_VISION_KEEP_PROB

  def _corner_promote_drel_margin(self) -> float:
    return CORNER_FRONT_MATCH_PROMOTE_DREL_MARGIN if self._lead_one_has_front_radar_vision_match() else CUTIN_PROMOTE_DREL_MARGIN

  def _corner_lead_clearly_closer_than_lead_one(self, lead: dict[str, Any]) -> bool:
    lead_one = self.radar_state.leadOne
    if not lead_one.present:
      return True
    return lead["dRel"] + CORNER_FRONT_MATCH_PROMOTE_DREL_MARGIN < lead_one.dRel

  def _lead_one_has_front_radar_vision_match(self) -> bool:
    lead_one = self.radar_state.leadOne
    if not self.lead_one_front_radar_vision_match or not lead_one.present or not lead_one.radar:
      return False
    if int(lead_one.radarTrackId) >= CORNER_235_TRACK_ID_START:
      return False
    return float(lead_one.modelProb) >= FRONT_RADAR_VISION_MATCH_MIN_PROB

  def _lead_is_corner_track(self, lead: dict[str, Any]) -> bool:
    track_id = int(lead.get("radarTrackId", -1))
    return (
      CORNER_235_TRACK_ID_START <= track_id < CORNER_235_TRACK_ID_END or
      CORNER_180_TRACK_ID_START <= track_id < CORNER_180_TRACK_ID_END
    )

  def _is_center_lead_candidate(self, t: Track) -> bool:
    in_lane_min = CENTER_LEAD_NEAR_IN_LANE_PROB
    dpath_limit = CENTER_LEAD_NEAR_DPATH_LIMIT
    if t.dRel > CENTER_LEAD_FAR_DREL:
      in_lane_min = CENTER_LEAD_FAR_IN_LANE_PROB
      dpath_limit = CENTER_LEAD_FAR_DPATH_LIMIT

    return t.in_lane_prob > in_lane_min and abs(t.dPath) < dpath_limit

  def _radar_only_center_ok(self, lead: dict[str, Any], md_arrays: dict[str, np.ndarray]) -> bool:
    d_rel = float(lead.get("dRel", 999.0))
    y_rel = float(lead.get("yRel", 999.0))
    d_path = abs(calculate_d_path(d_rel, y_rel, md_arrays))

    if d_rel > RADAR_ONLY_CENTER_MAX_DREL:
      return False
    if d_rel > RADAR_ONLY_CENTER_FAR_DREL:
      return d_path < RADAR_ONLY_CENTER_DPATH_FAR_LIMIT
    if d_rel > RADAR_ONLY_CENTER_MID_DREL:
      return d_path < RADAR_ONLY_CENTER_DPATH_MID_LIMIT
    return d_path < RADAR_ONLY_CENTER_DPATH_NEAR_LIMIT

  def compute_leads(self, v_ego, tracks, md, lead_prob, front_tracks: dict[int, Track] | None = None):
    self.leadCenter = None
    self.leadTwo = None
    self.leadCutIn = empty_lead()
    self.cornerLeadStopped = empty_lead()
    front_tracks = front_tracks or {}

    lead_msg = md.leadsV3[0] if (md is not None and len(self.md_arrays['pos_x']) == 33) else None
    if lead_msg is None:
      self.radar_state.leadsLeft = []
      self.radar_state.leadsCenter = []
      self.radar_state.leadsRight = []
      self.radar_state.leadsCutIn = []
      self.radar_state.leadsLeft2 = []
      self.radar_state.leadsRight2 = []
      self.radar_state.leadLeft = empty_lead()
      self.radar_state.leadRight = empty_lead()
      return

    front_left_list, front_right_list = [], []
    corner_left_list, corner_right_list = [], []
    center_list, cutin_list = [], []
    corner_center_list, corner_stopped_list = [], []
    for c in tracks.values():
      y_rel_neg = - c.yRel
      is_corner = self._is_corner_track(c)
      matching_front = self._matching_front_track(c, front_tracks) if is_corner else None
      if self._is_center_lead_candidate(c):
        c.cut_in_count = max(c.cut_in_count - 1, 0)
        if c.cnt > 3:
          ld = self._corner_lead_from_track(c, lead_prob, float(-lead_msg.y[0])) if is_corner else c.get_RadarState(
            lead_prob, float(-lead_msg.y[0]))
          ld['modelProb'] = 0.01
          center_list.append(ld)
          if self._is_corner_center_candidate(c):
            corner_center_list.append(ld)

      if self._is_corner_stopped_candidate(c, matched_front=matching_front is not None):
        corner_stopped_list.append(self._corner_stopped_lead_from_track(c, lead_prob))

      if self._is_center_lead_candidate(c):
        continue
      elif y_rel_neg < 0:
        ld = self._corner_lead_from_track(c, 0, 0) if is_corner else c.get_RadarState(0, 0)
        if self._update_cutin_sticky(c, matching_front is not None):
          ld['modelProb'] = 0.03
          cutin_list.append(ld)
        if is_corner:
          corner_left_list.append(ld)
        else:
          front_left_list.append(ld)
      else:
        ld = self._corner_lead_from_track(c, 0, 0) if is_corner else c.get_RadarState(0, 0)
        if self._update_cutin_sticky(c, matching_front is not None):
          ld['modelProb'] = 0.03
          cutin_list.append(ld)
        if is_corner:
          corner_right_list.append(ld)
        else:
          front_right_list.append(ld)

    left_list = select_side_leads(front_left_list, corner_left_list, self.corner_tracks_available)
    right_list = select_side_leads(front_right_list, corner_right_list, self.corner_tracks_available)

    self.radar_state.leadsLeft = left_list
    self.radar_state.leadsRight = right_list
    self.radar_state.leadsCenter = center_list
    self.radar_state.leadsCutIn = cutin_list
    self.leadCutIn = min(
      (ld for ld in cutin_list if self.cutin_enter_min_x < ld['dRel'] < self.cutin_enter_max_x and ld['vLead'] > 4),
      key=lambda d: d['dRel'],
      default=empty_lead()
    )
    self.cornerLeadStopped = min(
      corner_stopped_list,
      key=lambda d: d['dRel'],
      default=empty_lead()
    )

    self.radar_state.leadLeft = pick_side_lead(left_list, self.md_arrays)
    self.radar_state.leadRight = pick_side_lead(right_list, self.md_arrays)

    self.leadTwo = None
    if self.lane_line_available:
      self.leadCenter = min(
        (ld for ld in center_list if ld['vLead'] > 5 and ld['radar'] and ld['dRel'] > 3.5),
        key=lambda d: d['dRel'],
        default=None
      )
      if self.radar_state.leadOne.present and self.radar_state.leadOne.radar:
        self.leadTwo = min(
          (ld for ld in center_list if
           ld['vLead'] > 5 and ld['radar'] and not self._lead_is_corner_track(ld) and self.radar_state.leadOne.dRel <
           ld['dRel'] < 80),
          key=lambda d: d['dRel'],
          default=None
        )
        if self.leadTwo is not None:
          self.leadTwo = self.leadTwo.copy()
          self.leadTwo['dRel'] = max(self.radar_state.leadOne.dRel + 3.0, self.leadTwo['dRel'] - 8.0)
    else:
      self.leadCenter = None

    if self.leadCutIn and self.leadCutIn.get("present") and self.detect_cut_in:
      self.leadTwo = self.leadCutIn.copy()
      self.leadTwo["modelProb"] = 0.03

    def _ok(ld):
      if 'dRel' not in ld or 'yRel' not in ld:
        return False
      d_path = calculate_d_path(ld['dRel'], ld['yRel'], self.md_arrays)
      return (ld.get('vLead', 0) > 2 and
              abs(d_path) < 4.2 and
              ld['dRel'] > 2)

    def _pick_two_with_gap(cands, min_gap=5.0):
      xs = sorted((ld for ld in cands if _ok(ld)), key=lambda d: d['dRel'])
      if not xs:
        return []
      first = xs[0]
      second = None
      for ld in xs[1:]:
        if (ld['dRel'] - first['dRel']) >= min_gap:
          second = ld
          break
      return [first] if second is None else [first, second]

    self.radar_state.leadsLeft2 = _pick_two_with_gap(left_list, min_gap=5.0)
    self.radar_state.leadsRight2 = _pick_two_with_gap(right_list, min_gap=5.0)

  def _pick_lead_one_from_state(self):
    chosen = None
    detected = self.radar_detected

    if (self.leadCenter and self.leadCenter["present"] and
      not self._lead_is_corner_track(self.leadCenter) and
      is_radar_center_promotion_safe(self.leadCenter, self.md_arrays)):
      lead_one = self.radar_state.leadOne
      vision_prob = lead_one.modelProb if lead_one.present else 0.0

      if self.radar_detected:
        if lead_one.present and self.leadCenter["dRel"] + self._corner_promote_drel_margin() < lead_one.dRel:
          chosen = self.leadCenter
          chosen["modelProb"] = 0.01
      else:
        radar_clearly_closer = lead_one.present and self.leadCenter[
          "dRel"] + self._corner_promote_drel_margin() < lead_one.dRel
        vision_weak_or_missing = (not lead_one.present) or vision_prob < RADAR_ONLY_FALLBACK_VISION_PROB

        if vision_weak_or_missing and (not lead_one.present or radar_clearly_closer) and self._radar_only_center_ok(
          self.leadCenter, self.md_arrays):
          chosen = self.leadCenter
          chosen["modelProb"] = 0.02
          detected = True

    if chosen is not None:
      self.radar_state.leadOne = chosen
      self.radar_detected = detected


def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("radard is waiting for CarParams")
  CP = messaging.log_from_bytes(Params().get("CarParams", block=True), car.CarParams)
  cloudlog.info("radard got CarParams")

  sm = messaging.SubMaster(['modelV2', 'carState', 'liveTracks'], poll='modelV2')
  pm = messaging.PubMaster(['radarState'])

  RD = RadarD(CP.radarDelay)

  while 1:
    sm.update()

    if sm.updated['modelV2']:
      RD.update(sm, sm['liveTracks'])
      RD.publish(pm)


if __name__ == "__main__":
  main()
