#!/usr/bin/env python3
import math
import numpy as np
from collections import deque
from typing import Any

import capnp
from cereal import messaging, log, car
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

# Constants for lead tracking
LEAD_PROB_THRESHOLD = 0.4

EMPTY_LEAD_DICT = {'status': False}


def laplacian_pdf(x: float, mu: float, b: float):
  diff = abs(x - mu) / max(b, 1e-4)
  return 0.0 if diff > 50.0 else math.exp(-diff)


def _pick_two_with_gap(cands, min_gap=5.0):
  valid_cands = [ld for ld in cands if
                 ld.get('vLead', 0) > 2 and abs(ld.get('dPath', 0)) < 4.2 and ld.get('dRel', 0) > 2]
  if not valid_cands:
    return []
  valid_cands.sort(key=lambda d: d['dRel'])
  first = valid_cands[0]
  for ld in valid_cands[1:]:
    if (ld['dRel'] - first['dRel']) >= min_gap:
      return [first, ld]
  return [first]


class Track:
  __slots__ = ['identifier', 'cnt', 'aLeadTau', 'is_stopped_car_count', 'selected_count',
               'cut_in_count', 'measured', 'score', 'in_lane_prob', 'dPath', 'dRel',
               'yRel', 'vRel', 'vLead', 'vLeadK', 'aLead', 'aLeadK', 'jLead', 'yvLead']

  def __init__(self, identifier: int):
    self.identifier = identifier
    self.cnt = 0
    self.aLeadTau = FirstOrderFilter(_LEAD_ACCEL_TAU, 0.45, DT_MDL)

    self.is_stopped_car_count = 0
    self.selected_count = 0
    self.cut_in_count = 0
    self.measured = False
    self.score = 0.0
    self.in_lane_prob = 0.0
    self.dPath = 0.0
    self.dRel = 0.0
    self.yRel = 0.0
    self.vRel = 0.0
    self.vLead = 0.0
    self.vLeadK = 0.0
    self.aLead = 0.0
    self.aLeadK = 0.0
    self.jLead = 0.0
    self.yvLead = 0.0

  def update(self, model, radar_point, ready: bool):
    self.dRel = radar_point.dRel
    self.yRel = radar_point.yRel
    self.vRel = radar_point.vRel

    self.vLead = self.vLeadK = radar_point.vLead
    self.aLead = self.aLeadK = radar_point.aLead
    self.jLead = radar_point.jLead
    self.yvLead = radar_point.yvRel

    self.measured = radar_point.measured
    if not self.measured:
      self.cnt = 0

    if ready and model is not None and len(model.position.x) > 0:
      self.d_path(model)

    if abs(self.aLead) < 0.5 and abs(self.jLead) < 0.5:
      self.aLeadTau.x = _LEAD_ACCEL_TAU
    else:
      self.aLeadTau.update(0.0)
    self.cnt += 1

  def d_path(self, md):
    lane_xs = md.laneLines[1].x
    left_ys = md.laneLines[1].y
    right_ys = md.laneLines[2].y

    left_lane_y = np.interp(self.dRel, lane_xs, left_ys)
    right_lane_y = np.interp(self.dRel, lane_xs, right_ys)
    center_y = (left_lane_y + right_lane_y) * 0.5
    lane_half_width = abs(right_lane_y - left_lane_y) * 0.5

    if lane_half_width < 0.1:
      lane_half_width = 1.8

    dist_from_center = self.yRel + center_y
    self.in_lane_prob = max(0.0, 1.0 - (abs(dist_from_center) / lane_half_width))
    self.dPath = dist_from_center

  def get_RadarState(self, model_prob: float = 0.0, vision_y_rel: float = 0.0):
    return {
      "dRel": float(self.dRel),
      "yRel": float(self.yRel) if self.yRel != 0.0 else vision_y_rel,
      "dPath": float(self.dPath),
      "vRel": float(self.vRel),
      "vLead": float(self.vLead),
      "vLeadK": float(self.vLeadK),
      "aLead": float(self.aLead),
      "aLeadK": float(self.aLeadK),
      "aLeadTau": float(self.aLeadTau.x),
      "jLead": float(self.jLead),
      "vLat": float(self.yvLead),
      "status": True,
      "fcw": model_prob > 0.9,
      "modelProb": model_prob,
      "radar": True,
      "radarTrackId": self.identifier,
      "score": self.score,
    }

  def potential_low_speed_lead(self, v_ego: float):
    return abs(self.yRel) < 1.0 and (v_ego < V_EGO_STATIONARY) and (0.75 < self.dRel < 25)

  def __str__(self):
    return f"x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}"


def match_vision_to_track(v_ego: float, lead: capnp._DynamicStructReader, lead_prob: float, tracks: dict[int, Track]):
  offset_vision_dist = lead.x[0] - RADAR_TO_CAMERA
  max_vision_dist = max(offset_vision_dist * 1.25, 5.0)
  min_vision_dist = max(offset_vision_dist * 0.8, 1.0)
  max_vision_dist2 = max(offset_vision_dist * 1.45, 5.0)
  min_vision_dist2 = 1.5
  max_offset_vision_vel = max(lead.v[0] * np.interp(lead_prob, [0.8, 0.98], [0.3, 0.5]), 5.0)

  lead_v = lead.v[0]
  lead_y = lead.y[0]
  lead_xStd = lead.xStd[0]
  lead_yStd = lead.yStd[0]
  lead_yStd_2 = lead_yStd * 2
  lead_vStd = lead.vStd[0]
  neg_lead_y = -lead_y

  first_track, second_track, extra_track = None, None, None
  first_score, second_score, extra_score = -1e6, -1e6, -1e6

  for c in tracks.values():
    prob_d = laplacian_pdf(c.dRel, offset_vision_dist, lead_xStd)
    prob_y = laplacian_pdf(c.yRel, neg_lead_y, lead_yStd)
    prob_y2 = laplacian_pdf(c.yRel, neg_lead_y, lead_yStd_2)
    prob_v = laplacian_pdf(c.vLead, lead_v, lead_vStd)

    c.score = prob_d * prob_y * prob_v
    score2 = prob_d * prob_y2 * prob_v

    if c.score > first_score:
      second_score = first_score
      second_track = first_track
      first_score = c.score
      first_track = c
    if score2 > extra_score:
      extra_score = score2
      extra_track = c

  best_track = None

  if first_score >= 0.0001:
    track = first_track
    d_sane = min_vision_dist < track.dRel < max_vision_dist
    v_sane = (abs(track.vLead - lead_v) < max_offset_vision_vel) or (track.vLead > 3)
    y_sane = abs(track.yRel + lead_y) < 2.0

    if d_sane and v_sane:
      if y_sane:
        if lead_prob > 0.5 or (lead_prob > 0.4 and track.selected_count > 0):
          best_track = track
      elif lead_prob > 0.6:
        best_track = track
    elif d_sane and abs(track.yRel + lead_y) < 4.0:  # stopped-car
      if second_track and second_score > 0.00001:
        d_sane2 = min_vision_dist < second_track.dRel < max_vision_dist
        v_sane2 = (abs(second_track.vLead - lead_v) < max_offset_vision_vel) or (second_track.vLead > 3)
        y_sane2 = abs(second_track.yRel + lead_y) < 2.0
        if d_sane2 and y_sane2 and v_sane2:
          best_track = second_track

      if not best_track:
        if track.selected_count > 0:
          best_track = track
        else:
          track.is_stopped_car_count += 2
          if track.is_stopped_car_count > int(1.0 / DT_MDL):
            best_track = track
    elif offset_vision_dist < 90 and lead_prob > 0.65:
      # cut-in
      if extra_track and extra_score > first_score:
        d_sane_e = min_vision_dist2 < extra_track.dRel < max_vision_dist2
        v_sane_e = (abs(extra_track.vLead - lead_v) < max_offset_vision_vel) or (extra_track.vLead > 3)
        y_sane_e = abs(extra_track.yRel + lead_y) < 4.0
        if d_sane_e and v_sane_e and y_sane_e:
          best_track = extra_track

      if not best_track:
        d_sane_t2 = min_vision_dist2 < track.dRel < max_vision_dist2
        v_sane_t2 = (abs(track.vLead - lead_v) < max_offset_vision_vel) or (track.vLead > 3)
        y_sane_t2 = abs(track.yRel + lead_y) < 4.0
        if d_sane_t2 and v_sane_t2 and y_sane_t2:
          best_track = track

      if not best_track and second_track and second_score > 0.0001:
        d_sane_s2 = min_vision_dist2 < second_track.dRel < max_vision_dist2
        v_sane_s2 = (abs(second_track.vLead - lead_v) < max_offset_vision_vel) or (second_track.vLead > 3)
        y_sane_s2 = abs(second_track.yRel + lead_y) < 4.0
        if d_sane_s2 and v_sane_s2 and y_sane_s2:
          best_track = second_track

  for c in tracks.values():
    if c is best_track:
      c.selected_count += 1
    else:
      c.selected_count = 0
      if c.is_stopped_car_count > 0:
        c.is_stopped_car_count -= 1

  return best_track


class VisionTrack:
  __slots__ = ['radar_ts', 'dRel', 'vRel', 'yRel', 'vLead', 'aLead', 'vLeadK', 'aLeadK',
               'aLeadTau', 'prob', 'status', 'dRel_last', 'vLead_last', 'alpha', 'alpha_a',
               'vLat', 'v_ego', 'cnt', 'dPath']

  def __init__(self, radar_ts):
    self.radar_ts = radar_ts
    self.dRel = 0.0
    self.vRel = 0.0
    self.yRel = 0.0
    self.vLead = 0.0
    self.aLead = 0.0
    self.vLeadK = 0.0
    self.aLeadK = 0.0
    self.aLeadTau = _LEAD_ACCEL_TAU
    self.prob = 0.0
    self.status = False
    self.dRel_last = 0.0
    self.vLead_last = 0.0
    self.alpha = 0.02
    self.alpha_a = 0.02
    self.vLat = 0.0
    self.v_ego = 0.0
    self.cnt = 0
    self.dPath = 0.0

  def reset(self):
    self.status = False
    self.aLeadTau = _LEAD_ACCEL_TAU
    self.vRel = 0.0
    self.vLead = self.vLeadK = self.v_ego
    self.aLead = self.aLeadK = 0.0
    self.vLat = 0.0
    self.cnt = 0
    self.dPath = 0.0

  def get_lead(self, model):
    return {
      "dRel": self.dRel,
      "vRel": self.vRel,
      "yRel": self.yRel,
      "dPath": self.dPath,
      "vLead": self.vLead,
      "vLeadK": self.vLeadK,
      "aLead": self.aLead,
      "aLeadK": self.aLeadK,
      "aLeadTau": self.aLeadTau,
      "jLead": 0.0,
      "vLat": 0.0,
      "fcw": False,
      "modelProb": self.prob,
      "status": self.status,
      "radar": False,
      "radarTrackId": -1,
    }

  def update(self, lead_msg: capnp._DynamicStructReader, lead_prob: float, model_v_ego: float, v_ego: float,
             model: capnp._DynamicStructReader):
    lead_v_rel_pred = lead_msg.v[0] - model_v_ego
    self.prob = lead_prob
    self.v_ego = v_ego

    has_msg = len(lead_msg.x) > 0 and len(lead_msg.y) > 0 and len(lead_msg.a) > 0
    has_model_pos = len(model.position.x) > 0

    if self.prob > 0.5 and has_msg:
      dRel_current = float(lead_msg.x[0]) - RADAR_TO_CAMERA
      if abs(self.dRel - dRel_current) > 5.0:
        self.cnt = 0
      self.dRel = dRel_current
      self.yRel = float(-lead_msg.y[0])

      if has_model_pos:
        dPath_current = self.yRel + np.interp(self.dRel, model.position.x, model.position.y)
      else:
        dPath_current = self.yRel

      a_lead_vision = lead_msg.a[0]

      if self.cnt < 20 or self.prob < 0.97:
        self.vRel = lead_v_rel_pred
        self.vLead = float(v_ego + lead_v_rel_pred)
        self.aLead = a_lead_vision
        self.vLat = 0.0
      else:
        v_rel_derived = (self.dRel - self.dRel_last) / self.radar_ts
        v_rel_filtered = self.vRel * (1. - self.alpha) + v_rel_derived * self.alpha

        model_weight = np.interp(self.prob, [0.97, 1.0], [0.4, 0.0])
        self.vRel = float(lead_v_rel_pred * model_weight + v_rel_filtered * (1. - model_weight))
        self.vLead = float(v_ego + self.vRel)

        a_lead_derived = (self.vLead - self.vLead_last) / self.radar_ts * 0.2
        self.aLead = self.aLead * (1. - self.alpha_a) + a_lead_derived * self.alpha_a
        if abs(a_lead_vision) > abs(self.aLead):
          self.aLead = a_lead_vision

        vision_vlat_alpha = 0.002
        self.vLat = self.vLat * (1. - vision_vlat_alpha) + (
            dPath_current - self.dPath) / self.radar_ts * vision_vlat_alpha

      self.dPath = float(dPath_current)
      self.vLeadK = self.vLead
      self.aLeadK = self.aLead
      self.status = True
      self.cnt += 1
    else:
      self.reset()
      self.cnt = 0
      if has_model_pos:
        self.dPath = float(self.yRel + np.interp(v_ego ** 2 / (2 * 2.5), model.position.x, model.position.y))
      else:
        self.dPath = float(self.yRel)

    self.dRel_last = self.dRel
    self.vLead_last = self.vLead

    if abs(self.aLead) < 0.3:
      self.aLeadTau = 0.2
    else:
      self.aLeadTau *= 0.9


class RadarD:
  def __init__(self, delay: float = 0.0):
    self.current_time = 0.0

    self.tracks: dict[int, Track] = {}
    self.lead_prob_filters = [FirstOrderFilter(0.0, 0.2, DT_MDL) for _ in range(2)]

    self.v_ego = 0.0
    self.v_ego_hist = deque([0.0], maxlen=int(round(delay / DT_MDL)) + 1)
    self.last_v_ego_frame = -1
    self.ready = False
    self.vision_tracks = [VisionTrack(DT_MDL), VisionTrack(DT_MDL)]
    self.params = Params()
    self.radar_track_enable = self.params.get_bool("RadarTrackEnable")
    self.radar_detected = False

    self.leadCenter = EMPTY_LEAD_DICT
    self.leadCutIn = EMPTY_LEAD_DICT
    self.leadTwo = None
    self.lane_line_available = False

    self._left_list = []
    self._right_list = []
    self._center_list = []
    self._cutin_list = []

    self.dat = None

    try:
      test_msg = log.RadarState.new_message()
      self.has_leads_cutin = hasattr(test_msg, 'leadsCutIn')
      self.has_leads_left2 = hasattr(test_msg, 'leadsLeft2')
      self.has_leads_right2 = hasattr(test_msg, 'leadsRight2')
    except:
      self.has_leads_cutin = False
      self.has_leads_left2 = False
      self.has_leads_right2 = False

  def _get_fused_lead_data(self, model, tracks: dict[int, Track], index: int, lead_msg: capnp._DynamicStructReader,
                           lead_prob: float, low_speed_override: bool = True) \
    -> tuple[dict[str, Any], bool]:
    v_ego = self.v_ego
    ready = self.ready

    if not self.radar_track_enable:
      track_scc = tracks.get(0)
    else:
      track_scc = tracks.pop(0, None)

    track = None
    if tracks and ready and lead_prob > .4:
      track = match_vision_to_track(v_ego, lead_msg, lead_prob, tracks)

    if (track is None or lead_prob < .6) and track_scc is not None and track_scc.cnt > 2:
      if not self.radar_track_enable or track_scc.vLead < 5.0:
        track = track_scc

    lead_dict = EMPTY_LEAD_DICT
    radar_detected = False

    if track is not None:
      lead_dict = track.get_RadarState(lead_prob, self.vision_tracks[0].yRel)
      radar_detected = True
    elif (track is None) and ready and (lead_prob > .5):
      lead_dict = self.vision_tracks[index].get_lead(model)

    if low_speed_override:
      closest_track = None
      min_dRel = float('inf')

      for c in tracks.values():
        if c.potential_low_speed_lead(v_ego) and c.dRel < min_dRel:
          closest_track = c
          min_dRel = c.dRel

      if closest_track is not None:
        if (not lead_dict['status']) or (closest_track.dRel < lead_dict['dRel']):
          lead_dict = closest_track.get_RadarState(lead_prob, self.vision_tracks[0].yRel)
          radar_detected = True

    return lead_dict, radar_detected

  def _compute_all_leads(self, tracks: dict[int, Track], model: capnp._DynamicStructReader, lead_prob: float,
                         radar_state):
    self._left_list.clear()
    self._right_list.clear()
    self._center_list.clear()
    self._cutin_list.clear()

    self.leadCutIn = EMPTY_LEAD_DICT
    self.leadTwo = None

    lead_msg = None
    if model is not None and len(model.position.x) == 33 and len(model.leadsV3) > 0:
      lead_msg = model.leadsV3[0]

    if lead_msg is None:
      radar_state.leadsLeft = self._left_list
      radar_state.leadsCenter = self._center_list
      radar_state.leadsRight = self._right_list
      if self.has_leads_cutin:
        radar_state.leadsCutIn = self._cutin_list
      radar_state.leadLeft = EMPTY_LEAD_DICT
      radar_state.leadRight = EMPTY_LEAD_DICT
      return

    lead_msg_y = float(-lead_msg.y[0]) if len(lead_msg.y) > 0 else 0.0

    for c in tracks.values():
      y_rel_neg = -c.yRel

      if c.in_lane_prob > 0.1:
        if c.cnt > 3:
          ld = c.get_RadarState(lead_prob, lead_msg_y)
          ld['modelProb'] = 0.01
          self._center_list.append(ld)
      elif y_rel_neg < 0:
        ld = c.get_RadarState(0.0, 0.0)
        if self.lane_line_available and c.in_lane_prob > 0.1 and c.cnt > int(2.0 / DT_MDL):
          if c.cut_in_count > int(0.1 / DT_MDL):
            ld['modelProb'] = 0.03
            self._cutin_list.append(ld)
          c.cut_in_count += 2
        self._left_list.append(ld)
      else:
        ld = c.get_RadarState(0.0, 0.0)
        if self.lane_line_available and c.in_lane_prob > 0.1 and c.cnt > int(2.0 / DT_MDL):
          if c.cut_in_count > int(0.1 / DT_MDL):
            ld['modelProb'] = 0.03
            self._cutin_list.append(ld)
          c.cut_in_count += 2
        self._right_list.append(ld)

      if c.cut_in_count > 0:
        c.cut_in_count -= 1

    radar_state.leadsLeft = self._left_list
    radar_state.leadsRight = self._right_list
    radar_state.leadsCenter = self._center_list
    if self.has_leads_cutin:
      radar_state.leadsCutIn = self._cutin_list

    best_cutin = EMPTY_LEAD_DICT
    min_cutin_dRel = float('inf')
    for ld in self._cutin_list:
      if 3 < ld['dRel'] < 50 and ld['vLead'] > 4 and ld['dRel'] < min_cutin_dRel:
        best_cutin = ld
        min_cutin_dRel = ld['dRel']
    self.leadCutIn = best_cutin

    best_left = EMPTY_LEAD_DICT
    min_left_dRel = float('inf')
    for lead in self._left_list:
      if lead['dRel'] > 5.0 and abs(lead['dPath']) < 3.5 and lead['dRel'] < min_left_dRel:
        best_left = lead
        min_left_dRel = lead['dRel']
    radar_state.leadLeft = best_left

    best_right = EMPTY_LEAD_DICT
    min_right_dRel = float('inf')
    for lead in self._right_list:
      if lead['dRel'] > 5.0 and abs(lead['dPath']) < 3.5 and lead['dRel'] < min_right_dRel:
        best_right = lead
        min_right_dRel = lead['dRel']
    radar_state.leadRight = best_right

    if self.lane_line_available:
      best_center = None
      min_center_dRel = float('inf')
      for ld in self._center_list:
        if ld['vLead'] > 5 and ld['radar'] and ld['dRel'] > 3.5 and ld['dRel'] < min_center_dRel:
          best_center = ld
          min_center_dRel = ld['dRel']
      self.leadCenter = best_center if best_center else EMPTY_LEAD_DICT

      if radar_state.leadOne.status and radar_state.leadOne.radar:
        best_two = None
        min_two_dRel = float('inf')
        for ld in self._center_list:
          if ld['vLead'] > 5 and ld['radar'] and radar_state.leadOne.dRel < ld['dRel'] < 80 and ld[
            'dRel'] < min_two_dRel:
            best_two = ld
            min_two_dRel = ld['dRel']

        if best_two is not None:
          self.leadTwo = best_two.copy()
          self.leadTwo['dRel'] = max(radar_state.leadOne.dRel + 3.0, self.leadTwo['dRel'] - 8.0)
    else:
      self.leadCenter = EMPTY_LEAD_DICT

    if self.has_leads_left2:
      radar_state.leadsLeft2 = _pick_two_with_gap(self._left_list, 5.0)
    if self.has_leads_right2:
      radar_state.leadsRight2 = _pick_two_with_gap(self._right_list, 5.0)

  def _select_final_lead(self, radar_state):
    chosen = None
    detected = self.radar_detected

    if self.leadCenter.get("status"):
      if self.radar_detected:
        if radar_state.leadOne.status and self.leadCenter["dRel"] < radar_state.leadOne.dRel:
          chosen = self.leadCenter.copy()
          chosen["modelProb"] = 0.01
      else:
        chosen = self.leadCenter.copy()
        chosen["modelProb"] = 0.02
        detected = True

    if chosen is not None:
      radar_state.leadOne = chosen
      self.radar_detected = detected

  def update(self, sm: messaging.SubMaster, radar_data: car.RadarData):
    self.ready = sm.seen['modelV2']
    self.current_time = 1e-9 * max(sm.logMonoTime.values())

    if sm.recv_frame['carState'] != self.last_v_ego_frame:
      self.v_ego = sm['carState'].vEgo
      self.v_ego_hist.append(self.v_ego)
      self.last_v_ego_frame = sm.recv_frame['carState']

    valid_ids = {radar_point.trackId for radar_point in radar_data.points}

    for radar_point in radar_data.points:
      track_id = radar_point.trackId
      if track_id not in self.tracks:
        self.tracks[track_id] = Track(track_id)
      self.tracks[track_id].update(sm['modelV2'], radar_point, self.ready)

    for tid in list(self.tracks.keys()):
      if tid not in valid_ids:
        del self.tracks[tid]

    dat = messaging.new_message("radarState")
    dat.valid = sm.all_checks()
    radar_state = dat.radarState

    radar_state.mdMonoTime = sm.logMonoTime['modelV2']
    radar_state.radarErrors = radar_data.errors
    radar_state.carStateMonoTime = sm.logMonoTime['carState']

    model_v_ego = self.v_ego
    if len(sm['modelV2'].velocity.x) > 0:
      model_v_ego = sm['modelV2'].velocity.x[0]

    leads_v3 = sm['modelV2'].leadsV3
    if len(leads_v3) > 1:
      for i in range(2):
        lead_prob = leads_v3[i].prob
        if lead_prob > self.lead_prob_filters[i].x:
          self.lead_prob_filters[i].x = lead_prob
        else:
          self.lead_prob_filters[i].update(lead_prob)

      if radar_state.mdMonoTime != sm.logMonoTime['modelV2']:
        if self.radar_detected:
          self.vision_tracks[0].cnt = 0
          self.vision_tracks[1].cnt = 0
        self.vision_tracks[0].update(leads_v3[0], self.lead_prob_filters[0].x, model_v_ego, self.v_ego, sm['modelV2'])
        self.vision_tracks[1].update(leads_v3[1], self.lead_prob_filters[1].x, model_v_ego, self.v_ego, sm['modelV2'])

      alive_tracks = {}
      for tid, trk in self.tracks.items():
        if trk.cnt > 2:
          alive_tracks[tid] = trk

      radar_state.leadOne, self.radar_detected = self._get_fused_lead_data(sm['modelV2'], alive_tracks, 0, leads_v3[0],
                                                                           self.lead_prob_filters[0].x,
                                                                           low_speed_override=False)
      radar_state.leadTwo, _ = self._get_fused_lead_data(sm['modelV2'], alive_tracks, 1, leads_v3[1],
                                                         self.lead_prob_filters[1].x, low_speed_override=False)

      self.lane_line_available = False
      if len(sm['modelV2'].laneLineProbs) > 2:
        self.lane_line_available = sm['modelV2'].laneLineProbs[1] > 0.5 and sm['modelV2'].laneLineProbs[2] > 0.5

      self._compute_all_leads(alive_tracks, sm['modelV2'], self.lead_prob_filters[0].x, radar_state)

      if self.leadTwo is not None:
        radar_state.leadTwo = self.leadTwo

      if self.radar_track_enable:
        self._select_final_lead(radar_state)

    self.dat = dat

  def publish(self, pm: messaging.PubMaster):
    if hasattr(self, 'dat') and self.dat is not None:
      pm.send("radarState", self.dat)
      self.dat = None


def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("radard is waiting for CarParams")
  CP = messaging.log_from_bytes(Params().get("CarParams", block=True), car.CarParams)
  cloudlog.info("radard got CarParams")

  sm = messaging.SubMaster(['modelV2', 'carState', 'liveTracks'], poll='modelV2')
  pm = messaging.PubMaster(['radarState'])

  RD = RadarD(CP.radarDelay)

  while True:
    sm.update()
    RD.update(sm, sm['liveTracks'])
    RD.publish(pm)


if __name__ == "__main__":
  main()
