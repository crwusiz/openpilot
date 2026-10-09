#!/usr/bin/env python3
import math
import numpy as np
from types import SimpleNamespace
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

# stationary qualification parameters
V_EGO_STATIONARY = 4.  # no stationary object flag below this speed

RADAR_TO_CAMERA = 1.52  # RADAR is ~ 1.5m ahead from center of mesh frame
FRONT_RADAR_VISION_MATCH_MIN_PROB = 0.4

STICKY = SimpleNamespace(
  selected_count_max=int(2.0 / DT_MDL),
  max_dpath=0.8,
  far_drel=60.0,
  max_dpath_far=1.2,
  path_y_std_gain=0.5,
)

CUTIN = SimpleNamespace(
  sticky_frames=int(0.7 / DT_MDL),
  enter_prob_gain=0.12,
  keep_future_in_lane_prob=0.12,
  keep_max_dpath_future=1.6,
  keep_max_moving_away=0.3,
  promote_drel_margin=1.0,
  confirm_s=0.20,
  min_track_age_s=0.25,
  enter_min_x=1.0,
  enter_max_x=55.0,
  enter_min_abs_dpath=1.5,
  enter_future_in_lane_prob=0.20,
  enter_centering_gain=0.20,
)

CENTER_LEAD = SimpleNamespace(
  near_dpath_limit=1.2,
  far_dpath_limit=0.9,
  far_drel=60.0,
  near_in_lane_prob=0.3,
  far_in_lane_prob=0.45,
)

RADAR_ONLY_CENTER = SimpleNamespace(
  dpath_near_limit=1.1,
  dpath_mid_limit=0.9,
  dpath_far_limit=0.75,
  mid_drel=60.0,
  far_drel=80.0,
  max_drel=100.0,
  fallback_vision_prob=0.55,
)

RADAR_CENTER_PROMOTION = SimpleNamespace(
  max_lane_center_offset=1.5,
  receding_max_drel=45.0,
  receding_vrel=0.5,
)

CORNER_TRACK_IDS = SimpleNamespace(
  track_235_start=200,
  track_235_end=220,
  track_180_start=240,
  track_180_end=250,
)

CORNER_FRONT_MATCH = SimpleNamespace(
  drel=3.0,
  vrel=2.0,
  promote_drel_margin=8.0,
)

CORNER_ACCEL = SimpleNamespace(
  min_track_age=6,
  max_abs_dpath=1.5,
  max_abs_alead=3.0,
)

CORNER_STOPPED = SimpleNamespace(
  min_age=int(0.35 / DT_MDL),
  min_drel=5.0,
  max_drel=120.0,
  max_vlead=1.8,
  max_yvrel=0.8,
  near_dpath_limit=1.0,
  far_dpath_limit=0.75,
  near_in_lane_prob=0.35,
  far_in_lane_prob=0.5,
  far_drel=60.0,
)


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

  if abs(d_path - y_rel) >= RADAR_CENTER_PROMOTION.max_lane_center_offset:
    return False

  return d_rel <= RADAR_CENTER_PROMOTION.receding_max_drel or v_rel <= RADAR_CENTER_PROMOTION.receding_vrel


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


def pick_side_leads_with_gap(leads: list[dict[str, Any]], md_arrays: dict[str, np.ndarray],
                             min_gap: float = 5.0) -> list[dict[str, Any]]:
  def is_candidate(lead):
    if 'dRel' not in lead or 'yRel' not in lead:
      return False
    d_path = calculate_d_path(lead['dRel'], lead['yRel'], md_arrays)
    return lead.get('vLead', 0) > 2 and abs(d_path) < 4.2 and lead['dRel'] > 2

  candidates = sorted((lead for lead in leads if is_candidate(lead)), key=lambda lead: lead['dRel'])
  if not candidates:
    return []

  first = candidates[0]
  second = next((lead for lead in candidates[1:] if lead['dRel'] - first['dRel'] >= min_gap), None)
  return [first] if second is None else [first, second]


def is_corner_track_id(track_id: int) -> bool:
  return (
    CORNER_TRACK_IDS.track_235_start <= track_id < CORNER_TRACK_IDS.track_235_end or
    CORNER_TRACK_IDS.track_180_start <= track_id < CORNER_TRACK_IDS.track_180_end
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

    self.yRel_future = self.yRel + self.yvLead
    self.dRel_future = self.dRel + self.vLead
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
    if self.dRel < STICKY.far_drel:
      return STICKY.max_dpath
    return float(np.clip(STICKY.max_dpath + STICKY.path_y_std_gain * self.sticky_path_y_std,
                         STICKY.max_dpath, STICKY.max_dpath_far))

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


def match_vision_to_track(lead: capnp._DynamicStructReader, lead_prob: float,
                          tracks: dict[int, Track], update_counters: bool = True) -> Track | None:
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

    if t.in_lane_prob < 0.25:
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
      if t is best_track:
        t.selected_count = min(t.selected_count + 1, STICKY.selected_count_max)
      elif best_track is not None:
        t.selected_count = 0
        t.is_stopped_car_count = max(0, t.is_stopped_car_count - 1)

  return best_track


def get_RadarState_from_vision(lead_msg: capnp._DynamicStructReader, v_ego: float, model_v_ego: float,
                               lead_prob: float) -> dict[str, Any]:
  lead_v_rel_pred = lead_msg.v[0] - model_v_ego
  dRel = float(lead_msg.x[0] - RADAR_TO_CAMERA)
  yRel = float(-lead_msg.y[0])
  return {
    "dRel": dRel,
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
  def __init__(self):
    self.tracks: dict[int, Track] = {}

    self.lead_prob_filters = [FirstOrderFilter(0.0, 0.2, DT_MDL) for _ in range(2)]

    self.v_ego = 0.0
    self.last_v_ego_frame = -1

    self.radar_state: capnp._DynamicStructBuilder | None = None
    self.radar_state_valid = False

    self.ready = False

    self.params = Params()
    self.enable_radar_tracks = self.params.get_bool("RadarTrackEnable")
    self.enable_corner_radar = self.params.get_bool("IsHda2")

    self.md_arrays = {
      'pos_x': np.array([]),
      'pos_y': np.array([]),
      'pos_y_std': np.array([]),
      'lane_xs': np.array([]),
      'left_ys': np.array([]),
      'right_ys': np.array([]),
    }

    self.cutin_confirm_frames = max(1, int(round(CUTIN.confirm_s / DT_MDL)))
    self.cutin_min_track_age = max(1, int(round(CUTIN.min_track_age_s / DT_MDL)))
    self.cutin_enter_min_x = CUTIN.enter_min_x
    self.cutin_enter_max_x = CUTIN.enter_max_x
    self.cutin_enter_min_abs_dpath = CUTIN.enter_min_abs_dpath
    self.cutin_enter_future_in_lane_prob = CUTIN.enter_future_in_lane_prob
    self.cutin_enter_centering_gain = CUTIN.enter_centering_gain

    self.radar_detected = False
    self.lead_one_front_radar_vision_match = False
    self.leadCenter = None
    self.leadTwo = None
    self.leadCutIn = empty_lead()
    self.cornerLeadStopped = empty_lead()
    self.corner_tracks_available = False

  def update(self, sm: messaging.SubMaster, rr: car.RadarData):
    self.ready = sm.seen['modelV2']

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

      alive_tracks = {tid: trk for tid, trk in self.tracks.items() if trk.cnt > 2}
      front_tracks = {tid: trk for tid, trk in alive_tracks.items() if not self._is_corner_track(trk)}
      corner_tracks = {tid: trk for tid, trk in alive_tracks.items() if
                       self.enable_corner_radar and self._is_corner_track(trk)}
      self.corner_tracks_available = bool(corner_tracks)

      self.radar_state.leadOne, self.radar_detected = self.get_lead(front_tracks, 0, leads_v3[0], model_v_ego,
                                                                self.lead_prob_filters[0].x, low_speed_override=False)
      self.radar_state.leadTwo, _ = self.get_lead(front_tracks, 1, leads_v3[1], model_v_ego,
                                               self.lead_prob_filters[1].x, low_speed_override=False)

      self.lane_line_available = md.laneLineProbs[1] > 0.5 and md.laneLineProbs[2] > 0.5
      compute_tracks = dict(front_tracks)
      compute_tracks.update(corner_tracks)

      self.compute_leads(compute_tracks, md, self.lead_prob_filters[0].x, front_tracks)
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
    return is_corner_track_id(t.identifier)

  def _matching_front_track(self, corner: Track, front_tracks: dict[int, Track]) -> Track | None:
    matches = []
    for t in front_tracks.values():
      if t.cnt <= 2:
        continue
      if abs(t.dRel - corner.dRel) > CORNER_FRONT_MATCH.drel:
        continue
      if abs(t.vRel - corner.vRel) > CORNER_FRONT_MATCH.vrel:
        continue
      matches.append(t)

    return min(matches, key=lambda t: abs(t.dRel - corner.dRel) + abs(t.vRel - corner.vRel), default=None)

  def _corner_in_lane_ok(self, t: Track, matched_front: bool = False) -> bool:
    if not self.lane_line_available:
      return False

    dpath_limit = CORNER_STOPPED.near_dpath_limit
    in_lane_min = CORNER_STOPPED.near_in_lane_prob
    if t.dRel > CORNER_STOPPED.far_drel:
      dpath_limit = CORNER_STOPPED.far_dpath_limit
      in_lane_min = CORNER_STOPPED.far_in_lane_prob
    if matched_front:
      in_lane_min = max(0.2, in_lane_min - 0.15)
      dpath_limit += 0.15
    return abs(t.dPath) < dpath_limit and t.in_lane_prob > in_lane_min

  def _is_corner_stopped_candidate(self, t: Track, matched_front: bool = False) -> bool:
    return (
      self._is_corner_track(t) and
      t.cnt >= CORNER_STOPPED.min_age and
      CORNER_STOPPED.min_drel < t.dRel < CORNER_STOPPED.max_drel and
      abs(t.vLead) < CORNER_STOPPED.max_vlead and
      abs(t.yvLead) < CORNER_STOPPED.max_yvrel and
      self._corner_in_lane_ok(t, matched_front=matched_front)
    )

  def _corner_track_accel_allowed(self, t: Track) -> bool:
    return (
      t.cnt >= CORNER_ACCEL.min_track_age and
      self._track_is_closer_than_lead_one(t) and
      abs(t.dPath) < CORNER_ACCEL.max_abs_dpath and
      math.isfinite(t.aLeadK) and
      abs(t.aLeadK) < CORNER_ACCEL.max_abs_alead
    )

  def _corner_lead_from_track(self, t: Track, model_prob: float = 0.0, vision_y_rel: float = 0.0,
                              use_accel: bool = True) -> dict[str, Any]:
    ld = t.get_RadarState(model_prob, vision_y_rel)
    if not use_accel or not self._corner_track_accel_allowed(t):
      ld["aLeadK"] = 0.0
    ld["aLeadTau"] = _LEAD_ACCEL_TAU
    return ld

  def _corner_stopped_lead_from_track(self, t: Track) -> dict[str, Any]:
    ld = self._corner_lead_from_track(t, 0.04, use_accel=False)
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

    return max(sticky_tracks, key=lambda t: (t.selected_count, -t.dRel), default=None)

  def get_lead(self, tracks: dict[int, Track], index: int, lead_msg: capnp._DynamicStructReader,
               model_v_ego: float, lead_prob: float, low_speed_override: bool = True) -> tuple[dict[str, Any], bool]:

    v_ego = self.v_ego
    ready = self.ready
    if index == 0:
      self.lead_one_front_radar_vision_match = False

    if not self.enable_radar_tracks:
      track_scc = tracks.get(0)
    else:
      track_scc = tracks.pop(0, None)

    if tracks and ready and lead_prob > .4:
      track = match_vision_to_track(lead_msg, lead_prob, tracks, update_counters=(index == 0))
    else:
      track = None
    front_radar_vision_match = track is not None

    sticky_track = False
    if track is None and index == 0 and not self.corner_tracks_available:
      track = self.get_sticky_track(tracks)
      if track is not None:
        sticky_track = True
        track.selected_count = min(track.selected_count + 1, STICKY.selected_count_max)

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
    elif ready and lead_prob > .5:
      lead_dict = get_RadarState_from_vision(lead_msg, v_ego, model_v_ego, lead_prob)

    if low_speed_override:
      closest_track = min((c for c in tracks.values() if c.potential_low_speed_lead(v_ego)),
                          key=lambda c: c.dRel, default=None)
      if closest_track is not None and (not lead_dict['present'] or closest_track.dRel < lead_dict['dRel']):
        vision_y_rel = float(-lead_msg.y[0]) if ready else 0.0
        lead_dict = closest_track.get_RadarState(lead_prob, vision_y_rel)
        front_radar_vision_match = False

    if index == 0:
      self.lead_one_front_radar_vision_match = front_radar_vision_match
    return lead_dict, radar

  def _cutin_is_closer_or_matches_lead_one(self, t: Track, matched_front: bool = False) -> bool:
    if self._track_is_closer_than_lead_one(t):
      return True
    if not matched_front:
      return False

    lead_one = self.radar_state.leadOne
    if not lead_one.present or not lead_one.radar:
      return False
    if int(lead_one.radarTrackId) >= CORNER_TRACK_IDS.track_235_start:
      return False

    return (
      abs(t.dRel - float(lead_one.dRel)) < CORNER_FRONT_MATCH.drel and
      abs(t.vRel - float(lead_one.vRel)) < CORNER_FRONT_MATCH.vrel
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
    if (t.in_lane_prob_future - t.in_lane_prob) < CUTIN.enter_prob_gain:
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
    if moving_away > CUTIN.keep_max_moving_away:
      return False

    return (
      t.in_lane_prob_future > CUTIN.keep_future_in_lane_prob or
      abs(t.dPath_future) < CUTIN.keep_max_dpath_future
    )

  def _update_cutin_sticky(self, t: Track, matched_front: bool = False) -> bool:
    if self._is_cutin_enter_candidate(t, matched_front):
      t.cut_in_count = min(t.cut_in_count + 1, CUTIN.sticky_frames)
    elif t.cut_in_count > 0 and self._is_cutin_keep_candidate(t, matched_front):
      t.cut_in_count = max(t.cut_in_count - 1, 0)
    else:
      t.cut_in_count = 0

    return t.cut_in_count >= self.cutin_confirm_frames

  def _track_is_closer_than_lead_one(self, t: Track) -> bool:
    lead_one = self.radar_state.leadOne
    if not lead_one.present:
      return True
    return t.dRel + CUTIN.promote_drel_margin < lead_one.dRel

  def _corner_promote_drel_margin(self) -> float:
    return CORNER_FRONT_MATCH.promote_drel_margin if self._lead_one_has_front_radar_vision_match() else CUTIN.promote_drel_margin

  def _lead_one_has_front_radar_vision_match(self) -> bool:
    lead_one = self.radar_state.leadOne
    if not self.lead_one_front_radar_vision_match or not lead_one.present or not lead_one.radar:
      return False
    if int(lead_one.radarTrackId) >= CORNER_TRACK_IDS.track_235_start:
      return False
    return float(lead_one.modelProb) >= FRONT_RADAR_VISION_MATCH_MIN_PROB

  def _lead_is_corner_track(self, lead: dict[str, Any]) -> bool:
    return is_corner_track_id(int(lead.get("radarTrackId", -1)))

  def _is_center_lead_candidate(self, t: Track) -> bool:
    in_lane_min = CENTER_LEAD.near_in_lane_prob
    dpath_limit = CENTER_LEAD.near_dpath_limit
    if t.dRel > CENTER_LEAD.far_drel:
      in_lane_min = CENTER_LEAD.far_in_lane_prob
      dpath_limit = CENTER_LEAD.far_dpath_limit

    return t.in_lane_prob > in_lane_min and abs(t.dPath) < dpath_limit

  def _radar_only_center_ok(self, lead: dict[str, Any], md_arrays: dict[str, np.ndarray]) -> bool:
    d_rel = float(lead.get("dRel", 999.0))
    y_rel = float(lead.get("yRel", 999.0))
    d_path = abs(calculate_d_path(d_rel, y_rel, md_arrays))

    if d_rel > RADAR_ONLY_CENTER.max_drel:
      return False
    if d_rel > RADAR_ONLY_CENTER.far_drel:
      return d_path < RADAR_ONLY_CENTER.dpath_far_limit
    if d_rel > RADAR_ONLY_CENTER.mid_drel:
      return d_path < RADAR_ONLY_CENTER.dpath_mid_limit
    return d_path < RADAR_ONLY_CENTER.dpath_near_limit

  def compute_leads(self, tracks: dict[int, Track], md, lead_prob: float, front_tracks: dict[int, Track] | None = None):
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
    corner_stopped_list = []
    for c in tracks.values():
      is_corner = self._is_corner_track(c)
      matching_front = self._matching_front_track(c, front_tracks) if is_corner else None
      is_center = self._is_center_lead_candidate(c)
      if is_center:
        c.cut_in_count = max(c.cut_in_count - 1, 0)
        if c.cnt > 3:
          ld = self._corner_lead_from_track(c, lead_prob, float(-lead_msg.y[0])) if is_corner else c.get_RadarState(
            lead_prob, float(-lead_msg.y[0]))
          ld['modelProb'] = 0.01
          center_list.append(ld)

      if self._is_corner_stopped_candidate(c, matched_front=matching_front is not None):
        corner_stopped_list.append(self._corner_stopped_lead_from_track(c))

      if is_center:
        continue

      ld = self._corner_lead_from_track(c) if is_corner else c.get_RadarState()
      if self._update_cutin_sticky(c, matching_front is not None):
        ld['modelProb'] = 0.03
        cutin_list.append(ld)
      if -c.yRel < 0:
        side_list = corner_left_list if is_corner else front_left_list
      else:
        side_list = corner_right_list if is_corner else front_right_list
      side_list.append(ld)

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

    if self.leadCutIn["present"] and self.detect_cut_in:
      self.leadTwo = self.leadCutIn.copy()
      self.leadTwo["modelProb"] = 0.03

    self.radar_state.leadsLeft2 = pick_side_leads_with_gap(left_list, self.md_arrays)
    self.radar_state.leadsRight2 = pick_side_leads_with_gap(right_list, self.md_arrays)

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
        vision_weak_or_missing = (not lead_one.present) or vision_prob < RADAR_ONLY_CENTER.fallback_vision_prob

        if vision_weak_or_missing and (not lead_one.present or radar_clearly_closer) and self._radar_only_center_ok(
          self.leadCenter, self.md_arrays):
          chosen = self.leadCenter
          chosen["modelProb"] = 0.02
          detected = True

    if chosen is not None:
      self.radar_state.leadOne = chosen
      self.radar_detected = detected


# fuses camera and radar data for best lead detection
def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  # Wait for CarParams to be available before starting radar processing.
  cloudlog.info("radard is waiting for CarParams")
  Params().get("CarParams", block=True)
  cloudlog.info("radard got CarParams")

  # *** setup messaging
  sm = messaging.SubMaster(['modelV2', 'carState', 'radarTracks'], poll='modelV2')
  pm = messaging.PubMaster(['radarState'])

  RD = RadarD()

  while True:
    sm.update()

    if sm.updated['modelV2']:
      RD.update(sm, sm['radarTracks'])
      RD.publish(pm)


if __name__ == "__main__":
  main()
