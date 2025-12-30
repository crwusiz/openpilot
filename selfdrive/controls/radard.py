#!/usr/bin/env python3
import math
import numpy as np
from collections import deque
from typing import Any
import copy

import capnp
from cereal import messaging, log, car
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL, Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog


# Default lead acceleration decay set to 50% at 1s
_LEAD_ACCEL_TAU = 1.5

# radar tracks
SPEED, ACCEL = 0, 1     # Kalman filter states enum

# stationary qualification parameters
V_EGO_STATIONARY = 4.   # no stationary object flag below this speed

#RADAR_TO_CENTER = 2.7   # (deprecated) RADAR is ~ 2.7m ahead from center of car
RADAR_TO_CAMERA = 1.52  # RADAR is ~ 1.5m ahead from center of mesh frame

# Constants for lead tracking
LEAD_PROB_THRESHOLD = 0.4


class Track:
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

    def d_path_interp(dRel, yRel):
      left_lane_y = np.interp(dRel, lane_xs, left_ys)
      right_lane_y = np.interp(dRel, lane_xs, right_ys)
      center_y = (left_lane_y + right_lane_y) / 2.0
      lane_half_width = abs(right_lane_y - left_lane_y) / 2.0
      if lane_half_width < 0.1:
        lane_half_width = 1.8
      dist_from_center = yRel + center_y
      in_lane_prob = max(0.0, 1.0 - (abs(dist_from_center) / lane_half_width))
      return dist_from_center, in_lane_prob

    self.dPath, self.in_lane_prob = d_path_interp(self.dRel, self.yRel)

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
      "fcw": self.is_potential_fcw(model_prob),
      "modelProb": model_prob,
      "radar": True,
      "radarTrackId": self.identifier,
      "score": self.score,
    }

  def potential_low_speed_lead(self, v_ego: float):
    return abs(self.yRel) < 1.0 and (v_ego < V_EGO_STATIONARY) and (0.75 < self.dRel < 25)

  def is_potential_fcw(self, model_prob: float):
    return model_prob > .9

  def __str__(self):
    return f"x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}"


def laplacian_pdf(x: float, mu: float, b: float):
  diff = abs(x - mu) / max(b, 1e-4)
  return 0.0 if diff > 50.0 else math.exp(-diff)


def match_vision_to_track(v_ego: float, lead: capnp._DynamicStructReader, tracks: dict[int, Track]):
  offset_vision_dist = lead.x[0] - RADAR_TO_CAMERA
  max_vision_dist = max(offset_vision_dist * 1.25, 5.0)
  min_vision_dist = max(offset_vision_dist * 0.8, 1.0)
  max_vision_dist2 = max(offset_vision_dist * 1.45, 5.0)
  min_vision_dist2 = 1.5
  max_offset_vision_vel = max(lead.v[0] * np.interp(lead.prob, [0.8, 0.98], [0.3, 0.5]), 5.0)

  def prob(c):
    prob_d = laplacian_pdf(c.dRel, offset_vision_dist, lead.xStd[0])
    prob_y = laplacian_pdf(c.yRel, -lead.y[0], lead.yStd[0])
    prob_y2 = laplacian_pdf(c.yRel, -lead.y[0], lead.yStd[0] * 2)  # for cut-in
    prob_v = laplacian_pdf(c.vLead, lead.v[0], lead.vStd[0])

    score = prob_d * prob_y * prob_v
    score2 = prob_d * prob_y2 * prob_v
    return score, score2

  def vel_sane(c):
    return (abs(c.vLead - lead.v[0]) < max_offset_vision_vel) or (c.vLead > 3)

  def dist_sane(c, second=False):
    if second:
      return min_vision_dist2 < c.dRel < max_vision_dist2
    return min_vision_dist < c.dRel < max_vision_dist

  def y_sane(c, second=False):
    if second:
      return abs(c.yRel + lead.y[0]) < 4.0
    return abs(c.yRel + lead.y[0]) < 2.0

  first_track, second_track, extra_track = None, None, None
  first_score, second_score, extra_score = -1e6, -1e6, -1e6

  for c in tracks.values():
    c.score, score2 = prob(c)
    if c.score > first_score:
      second_score = first_score
      second_track = first_track
      first_score = c.score
      first_track = c
    if score2 > extra_score:
      extra_score = score2
      extra_track = c

  def select_track(track, score, track2, score2, extra_track, extra_score):
    if score < 0.0001:
      return None

    best_track = None
    if dist_sane(track) and vel_sane(track):
      if y_sane(track):
        if lead.prob > 0.5:
          best_track = track
        elif lead.prob > 0.4 and track.selected_count > 0:
          best_track = track
      elif lead.prob > 0.6:
        best_track = track
    elif dist_sane(track) and y_sane(track, True):  # stopped-car
      if score2 > 0.00001 and dist_sane(track2) and y_sane(track2) and vel_sane(track2):
        best_track = track2
      elif track.selected_count > 0:
        best_track = track
      else:
        track.is_stopped_car_count += 2
        if track.is_stopped_car_count > int(1.0/DT_MDL):
          best_track = track
    elif offset_vision_dist < 90 and lead.prob > 0.65:
      # wide y detect, for cut-in
      if extra_score > score and dist_sane(extra_track, True) and vel_sane(extra_track) and y_sane(extra_track, True):
        best_track = extra_track
      # wide dRel, y detect, for cut-in
      elif dist_sane(track, True) and vel_sane(track) and y_sane(track, True):
        best_track = track
      elif score2 > 0.0001 and dist_sane(track2, True) and vel_sane(track2) and y_sane(track2, True):
        best_track = track2
    return best_track

  best_track = select_track(first_track, first_score, second_track, second_score, extra_track, extra_score)

  for c in tracks.values():
    if c is best_track:
      c.selected_count += 1
    else:
      c.selected_count = 0
      c.is_stopped_car_count = max(0, c.is_stopped_car_count - 1)

  return best_track


class VisionTrack:
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

  def update(self, lead_msg: capnp._DynamicStructReader, model_v_ego: float, v_ego: float, model: capnp._DynamicStructReader):
    lead_v_rel_pred = lead_msg.v[0] - model_v_ego
    self.prob = lead_msg.prob
    self.v_ego = v_ego

    if self.prob > 0.5:
      dRel_current = float(lead_msg.x[0]) - RADAR_TO_CAMERA
      if abs(self.dRel - dRel_current) > 5.0:
        self.cnt = 0
      self.dRel = dRel_current

      self.yRel = float(-lead_msg.y[0])
      dPath_current = self.yRel + np.interp(self.dRel, model.position.x, model.position.y)
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
        self.vLat = self.vLat * (1. - vision_vlat_alpha) + (dPath_current - self.dPath) / self.radar_ts * vision_vlat_alpha

      self.dPath = float(dPath_current)
      self.vLeadK = self.vLead
      self.aLeadK = self.aLead
      self.status = True
      self.cnt += 1
    else:
      self.reset()
      self.cnt = 0
      self.dPath = float(self.yRel + np.interp(v_ego ** 2 / (2 * 2.5), model.position.x, model.position.y))

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
    self.v_ego = 0.0
    self.v_ego_hist = deque([0.0], maxlen=int(round(delay / DT_MDL))+1)
    self.last_v_ego_frame = -1
    self.radar_state: capnp._DynamicStructBuilder | None = None
    self.radar_state_valid = False
    self.ready = False
    self.vision_tracks = [VisionTrack(DT_MDL), VisionTrack(DT_MDL)]
    self.params = Params()
    self.radar_track_enable = self.params.get_bool("RadarTrackEnable")
    self.radar_detected = False
    self.leadCenter = {'status': False}
    self.leadCutIn = {'status': False}
    self.leadTwo = None
    self.lane_line_available = False

  def _get_fused_lead_data(self, model, tracks: dict[int, Track], index: int, lead_msg: capnp._DynamicStructReader, low_speed_override: bool = True) \
    -> tuple[dict[str, Any], bool]:
    v_ego = self.v_ego
    ready = self.ready

    if not self.radar_track_enable:
      track_scc = tracks.get(0)
    else:
      track_scc = tracks.pop(0, None)

    track = None
    if len(tracks) > 0 and ready and lead_msg.prob > .4:
      track = match_vision_to_track(v_ego, lead_msg, tracks)

    if (track is None or lead_msg.prob < .6) and track_scc is not None and track_scc.cnt > 2:
      if not self.radar_track_enable or track_scc.vLead < 5.0:
        track = track_scc

    lead_dict: dict[str, Any] = {'status': False}
    radar_detected = False

    if track is not None:
      lead_dict = track.get_RadarState(lead_msg.prob, self.vision_tracks[0].yRel)
      radar_detected = True
    elif (track is None) and ready and (lead_msg.prob > .5):
      lead_dict = self.vision_tracks[index].get_lead(model)

    if low_speed_override:
      low_speed_tracks = [c for c in tracks.values() if c.potential_low_speed_lead(v_ego)]
      if len(low_speed_tracks) > 0:
        closest_track = min(low_speed_tracks, key=lambda c: c.dRel)

        if (not lead_dict['status']) or (closest_track.dRel < lead_dict['dRel']):
          lead_dict = closest_track.get_RadarState(lead_msg.prob, self.vision_tracks[0].yRel)
          radar_detected = True

    return lead_dict, radar_detected

  def _compute_all_leads(self, tracks: dict[int, Track], model: capnp._DynamicStructReader):
    lead_msg = model.leadsV3[0] if (model is not None and len(model.position.x) == 33) else None
    self.leadCutIn = {'status': False}

    if lead_msg is None:
      self.radar_state.leadsLeft = []
      self.radar_state.leadsCenter = []
      self.radar_state.leadsRight = []
      # Try to clear leadsCutIn if it exists in struct, else ignore
      try: self.radar_state.leadsCutIn = []
      except: pass
      self.radar_state.leadLeft = {'status': False}
      self.radar_state.leadRight = {'status': False}
      return

    left_list, right_list, center_list, cutin_list = [], [], [], []

    for c in tracks.values():
      y_rel_neg = -c.yRel

      # center
      if c.in_lane_prob > 0.1:
        if c.cnt > 3:
          ld = c.get_RadarState(lead_msg.prob, float(-lead_msg.y[0]))
          ld['modelProb'] = 0.01
          center_list.append(ld)

      # left/right
      elif y_rel_neg < 0:
        ld = c.get_RadarState(0, 0)
        if self.lane_line_available and c.in_lane_prob > 0.1 and c.cnt > int(2.0/DT_MDL):
          if c.cut_in_count > int(0.1/DT_MDL):
            ld['modelProb'] = 0.03
            cutin_list.append(ld)
          c.cut_in_count += 2
        left_list.append(ld)
      else:
        ld = c.get_RadarState(0, 0)
        if self.lane_line_available and c.in_lane_prob > 0.1 and c.cnt > int(2.0/DT_MDL):
          if c.cut_in_count > int(0.1/DT_MDL):
            ld['modelProb'] = 0.03
            cutin_list.append(ld)
          c.cut_in_count += 2
        right_list.append(ld)

      c.cut_in_count = max(c.cut_in_count - 1, 0)

    self.radar_state.leadsLeft = left_list
    self.radar_state.leadsRight = right_list
    self.radar_state.leadsCenter = center_list
    try: self.radar_state.leadsCutIn = cutin_list
    except: pass

    self.leadCutIn = min(
      (ld for ld in cutin_list if 3 < ld['dRel'] < 50 and ld['vLead'] > 4),
      key=lambda d: d['dRel'],
      default={'status': False}
    )

    self.radar_state.leadLeft = min(
      (lead for lead in left_list if lead['dRel'] > 5.0 and abs(lead['dPath']) < 3.5),
      key=lambda x: x['dRel'],
      default={'status': False}
    )
    self.radar_state.leadRight = min(
      (lead for lead in right_list if lead['dRel'] > 5.0 and abs(lead['dPath']) < 3.5),
      key=lambda x: x['dRel'],
      default={'status': False}
    )

    self.leadTwo = None
    if self.lane_line_available:
      self.leadCenter = min(
          (ld for ld in center_list if ld['vLead'] > 5 and ld['radar'] and ld['dRel'] > 3.5),
          key=lambda d: d['dRel'],
          default=None
      )
      if self.radar_state.leadOne.status and self.radar_state.leadOne.radar:
        self.leadTwo = min(
            (ld for ld in center_list if ld['vLead'] > 5 and ld['radar'] and self.radar_state.leadOne.dRel < ld['dRel'] < 80),
            key=lambda d: d['dRel'],
            default=None
        )
        if self.leadTwo is not None:
          self.leadTwo = copy.deepcopy(self.leadTwo)
          self.leadTwo['dRel'] = max(self.radar_state.leadOne.dRel + 3.0, self.leadTwo['dRel'] - 8.0)
    else:
      self.leadCenter = None

    def _ok(ld):
      return (ld.get('vLead', 0) > 2 and abs(ld.get('dPath', 0)) < 4.2 and ld.get('dRel', 0) > 2)

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

    # Try assigning extended leads if structure exists
    try:
      self.radar_state.leadsLeft2  = _pick_two_with_gap(left_list,  min_gap=5.0)
      self.radar_state.leadsRight2 = _pick_two_with_gap(right_list, min_gap=5.0)
    except: pass

  def _select_final_lead(self):
    chosen = None
    detected = self.radar_detected

    if self.leadCenter and self.leadCenter.get("status"):
      if self.radar_detected:
        if self.radar_state.leadOne.status and self.leadCenter["dRel"] < self.radar_state.leadOne.dRel:
          chosen = self.leadCenter
          chosen["modelProb"] = 0.01
      else:
        chosen = self.leadCenter
        chosen["modelProb"] = 0.02
        detected = True

    if chosen is not None:
      self.radar_state.leadOne = chosen
      self.radar_detected = detected

  def update(self, sm: messaging.SubMaster, radar_data: car.RadarData):
    self.ready = sm.seen['modelV2']
    self.current_time = 1e-9 * max(sm.logMonoTime.values())

    if sm.recv_frame['carState'] != self.last_v_ego_frame:
      self.v_ego = sm['carState'].vEgo
      self.v_ego_hist.append(self.v_ego)
      self.last_v_ego_frame = sm.recv_frame['carState']

    valid_ids = set()
    for radar_point in radar_data.points:
      track_id = radar_point.trackId
      valid_ids.add(track_id)
      if track_id not in self.tracks:
        self.tracks[track_id] = Track(track_id)
      self.tracks[track_id].update(sm['modelV2'], radar_point, self.ready)

    for tid in list(self.tracks.keys()):
      if tid not in valid_ids:
        self.tracks.pop(tid)

    self.radar_state_valid = sm.all_checks()
    self.radar_state = log.RadarState.new_message()

    model_updated = False if self.radar_state.mdMonoTime == sm.logMonoTime['modelV2'] else True

    self.radar_state.mdMonoTime = sm.logMonoTime['modelV2']
    self.radar_state.radarErrors = radar_data.errors
    self.radar_state.carStateMonoTime = sm.logMonoTime['carState']

    if len(sm['modelV2'].velocity.x):
      model_v_ego = sm['modelV2'].velocity.x[0]
    else:
      model_v_ego = self.v_ego

    leads_v3 = sm['modelV2'].leadsV3
    if len(leads_v3) > 1:
      if model_updated:
        if self.radar_detected:
          self.vision_tracks[0].cnt = 0
          self.vision_tracks[1].cnt = 0
        self.vision_tracks[0].update(leads_v3[0], model_v_ego, self.v_ego, sm['modelV2'])
        self.vision_tracks[1].update(leads_v3[1], model_v_ego, self.v_ego, sm['modelV2'])

      # Filter tracks like radard_add
      alive_tracks = {tid: trk for tid, trk in self.tracks.items() if trk.cnt > 2}

      # Use updated _get_fused_lead_data (passed CS for corner radar)
      self.radar_state.leadOne, self.radar_detected = self._get_fused_lead_data(sm['modelV2'], alive_tracks, 0, leads_v3[0], low_speed_override=False)
      self.radar_state.leadTwo, _ = self._get_fused_lead_data(sm['modelV2'], alive_tracks, 1, leads_v3[1], low_speed_override=False)

      # Check lane availability
      self.lane_line_available = sm['modelV2'].laneLineProbs[1] > 0.5 and sm['modelV2'].laneLineProbs[2] > 0.5

      # Compute all leads and select final
      self._compute_all_leads(alive_tracks, sm['modelV2'])

      if self.leadTwo is not None:
        self.radar_state.leadTwo = self.leadTwo

      if self.radar_track_enable:
        self._select_final_lead()

  def publish(self, pm: messaging.PubMaster):
    assert self.radar_state is not None

    radar_msg = messaging.new_message("radarState")
    radar_msg.valid = self.radar_state_valid
    radar_msg.radarState = self.radar_state
    pm.send("radarState", radar_msg)


def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  # wait for stats about the car to come in from controls
  cloudlog.info("radard is waiting for CarParams")
  CP = messaging.log_from_bytes(Params().get("CarParams", block=True), car.CarParams)
  cloudlog.info("radard got CarParams")

  # *** setup messaging
  sm = messaging.SubMaster(['modelV2', 'carState', 'liveTracks'], poll='modelV2')
  pm = messaging.PubMaster(['radarState'])

  RD = RadarD(CP.radarDelay)

  while True:
    sm.update()

    RD.update(sm, sm['liveTracks'])
    RD.publish(pm)


if __name__ == "__main__":
  main()
