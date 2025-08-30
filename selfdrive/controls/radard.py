#!/usr/bin/env python3
import math
import numpy as np
from collections import deque
from typing import Any, Dict, Tuple

import capnp
from cereal import messaging, log, car
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL, Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.common.simple_kalman import KF1D


# Default lead acceleration decay set to 50% at 1s
_LEAD_ACCEL_TAU = 1.5

# radar tracks
SPEED, ACCEL = 0, 1     # Kalman filter states enum

# stationary qualification parameters
V_EGO_STATIONARY = 4.   # no stationary object flag below this speed

#RADAR_TO_CENTER = 2.7   # (deprecated) RADAR is ~ 2.7m ahead from center of car
RADAR_TO_CAMERA = 1.52  # RADAR is ~ 1.5m ahead from center of mesh frame

# Constants for lead tracking
LEAD_PROB_THRESHOLD = 0.5
VISION_PROB_HIGH_THRESHOLD = 0.8
VISION_PROB_VERY_HIGH_THRESHOLD = 0.97
VISION_PROB_INTERP_LOW = 0.4
VISION_PROB_INTERP_HIGH = 0.0
VISION_DREL_RESET_THRESHOLD = 5.0
VISION_CNT_THRESHOLD = 20
VISION_ACCEL_THRESHOLD = 0.3
VISION_ACCEL_TAU_LOW = 0.2
VISION_ACCEL_TAU_DECAY = 0.9
VISION_VLAT_ALPHA = 0.002
MIN_LEAD_SIDE_DRel = 5.0


class KalmanParams:
  def __init__(self, dt: float):
    # Lead Kalman Filter params, calculating K from A, C, Q, R requires the control library.
    # hardcoding a lookup table to compute K for values of radar_ts between 0.01s and 0.2s
    assert dt > .01 and dt < .2, "Radar time step must be between .01s and 0.2s"
    self.A = [[1.0, dt], [0.0, 1.0]]
    self.C = [1.0, 0.0]
    #Q = np.matrix([[10., 0.0], [0.0, 100.]])
    #R = 1e3
    #K = np.matrix([[ 0.05705578], [ 0.03073241]])
    dts = [i * 0.01 for i in range(1, 21)]
    K0 = [0.12287673, 0.14556536, 0.16522756, 0.18281627, 0.1988689,  0.21372394,
          0.22761098, 0.24069424, 0.253096,   0.26491023, 0.27621103, 0.28705801,
          0.29750003, 0.30757767, 0.31732515, 0.32677158, 0.33594201, 0.34485814,
          0.35353899, 0.36200124]
    K1 = [0.29666309, 0.29330885, 0.29042818, 0.28787125, 0.28555364, 0.28342219,
          0.28144091, 0.27958406, 0.27783249, 0.27617149, 0.27458948, 0.27307714,
          0.27162685, 0.27023228, 0.26888809, 0.26758976, 0.26633338, 0.26511557,
          0.26393339, 0.26278425]
    self.K = [[np.interp(dt, dts, K0)], [np.interp(dt, dts, K1)]]


class Track:
  def __init__(self, identifier: int):
    self.identifier = identifier
    self.cnt = 0
    self.aLeadTau = FirstOrderFilter(_LEAD_ACCEL_TAU, 0.45, DT_MDL)

    self.is_stopped_car_count = 0
    self.selected_count = 0
    self.cut_in_count = 0
    self.yRel_filtered = 0.0
    self.yvLead_filtered = 0.0
    self.dPath = 0.0

  def update(self, md, pt, ready: bool):
    self.dRel = pt.dRel
    self.yRel = pt.yRel
    self.vRel = pt.vRel

    self.vLead = self.vLeadK = pt.vLead
    self.aLead = self.aLeadK = pt.aLead
    self.jLead = pt.jLead
    self.yvLead = pt.yvRel

    self.measured = pt.measured

    if ready and md is not None and len(md.position.x) > 0:
      self.dPath = self.yRel + np.interp(self.dRel, md.position.x, md.position.y)

    if self.cnt == 0:
      self.yRel_filtered = self.yRel
      self.yvLead_filtered = self.yvLead
    else:
      self.yRel_filtered = self.yRel_filtered * 0.9 + self.yRel * 0.1
      self.yvLead_filtered = self.yvLead_filtered * 0.9 + self.yvLead * 0.1

    if abs(self.aLead) < 0.5 and abs(self.jLead) < 0.5:
      self.aLeadTau.x = _LEAD_ACCEL_TAU
    else:
      self.aLeadTau.update(0.0)
    self.cnt += 1

  def get_RadarState(self, model_msg=None, model_prob=0.0, vision_y_rel=None):
    y_rel = float(vision_y_rel) if vision_y_rel is not None else float(self.yRel)
    if model_msg is not None and len(model_msg.position.x) > 0:
      d_path = y_rel + np.interp(self.dRel, model_msg.position.x, model_msg.position.y)
    else:
      d_path = self.dPath if hasattr(self, 'dPath') else 0.0

    return {
      "dRel": float(self.dRel),
      "yRel": y_rel,
      "dPath": float(d_path),
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
    }

  def potential_low_speed_lead(self, v_ego: float):
    # stop for stuff in front of you and low speed, even without model confirmation
    # Radar points closer than 0.75, are almost always glitches on toyota radars
    return abs(self.yRel) < 1.0 and (v_ego < V_EGO_STATIONARY) and (0.75 < self.dRel < 25)

  def is_potential_fcw(self, model_prob: float):
    return model_prob > .9

  def __str__(self):
    ret = f"x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}"
    return ret


def laplacian_pdf(x: float, mu: float, b: float):
  diff = abs(x - mu) / max(b, 1e-4)
  return 0.0 if diff > 50.0 else math.exp(-diff)

def match_vision_to_track(v_ego: float, lead: capnp._DynamicStructReader, tracks: dict[int, Track]):
  offset_vision_dist = lead.x[0] - RADAR_TO_CAMERA
  max_vision_dist = max(offset_vision_dist * 1.25, 5.0)
  min_vision_dist = max(offset_vision_dist * 0.6, 1.0)
  max_offset_vision_vel = max(lead.v[0] * np.interp(lead.prob, [0.8, 0.98], [0.3, 0.5]), 5.0)

  def prob(c):
    prob_d = laplacian_pdf(c.dRel, offset_vision_dist, lead.xStd[0])
    prob_y = laplacian_pdf(c.yRel + c.yvLead, -lead.y[0], lead.yStd[0])
    prob_v = laplacian_pdf(c.vLead, lead.v[0], lead.vStd[0])
    weight_v = np.interp(c.vLead, [0, 10], [0.3, 1])

    return prob_d * prob_y * prob_v * weight_v

  best_track = None
  best_score = -1e6
  for c in tracks.values():
    score = prob(c)
    if score > best_score:
      best_score = score
      best_track = c

  y_gate = min(1.7, lead.yStd[0] * 2.0)
  v_gate = max(5.0, lead.vStd[0] * 2.0)

  if best_track is not None:
    if not (min_vision_dist < best_track.dRel < max_vision_dist) or \
       abs(best_track.yRel + best_track.yvLead + lead.y[0]) > y_gate or \
       abs(best_track.vLead - lead.v[0]) > v_gate:
      best_track = None
    elif lead.v[0] - best_track.vLead > max_offset_vision_vel:
      best_track.is_stopped_car_count += 1
      if best_track.selected_count < 1 and best_track.is_stopped_car_count < int(2.0 / DT_MDL):
        best_track = None

  for c in tracks.values():
    if c is best_track:
      c.selected_count += 1
      c.is_stopped_car_count = 0
    else:
      c.selected_count = 0

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

  def get_lead(self, model_msg):
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

  def update(self, lead_msg: capnp._DynamicStructReader, model_v_ego: float, v_ego: float, model_msg: capnp._DynamicStructReader):
    lead_v_rel_pred = lead_msg.v[0] - model_v_ego
    self.prob = lead_msg.prob
    self.v_ego = v_ego

    if self.prob > LEAD_PROB_THRESHOLD:
      dRel_current = float(lead_msg.x[0]) - RADAR_TO_CAMERA
      if abs(self.dRel - dRel_current) > VISION_DREL_RESET_THRESHOLD:
        self.cnt = 0
      self.dRel = dRel_current

      self.yRel = float(-lead_msg.y[0])
      a_lead_vision = lead_msg.a[0]

      if self.cnt < VISION_CNT_THRESHOLD or self.prob < VISION_PROB_VERY_HIGH_THRESHOLD:
        self.vRel = lead_v_rel_pred
        self.vLead = float(v_ego + lead_v_rel_pred)
        self.aLead = a_lead_vision
      else:
        v_rel_derived = (self.dRel - self.dRel_last) / self.radar_ts
        v_rel_filtered = self.vRel * (1. - self.alpha) + v_rel_derived * self.alpha
        model_weight = np.interp(self.prob, [VISION_PROB_VERY_HIGH_THRESHOLD, 1.0], [VISION_PROB_INTERP_LOW, VISION_PROB_INTERP_HIGH])
        self.vRel = float(lead_v_rel_pred * model_weight + v_rel_filtered * (1. - model_weight))
        self.vLead = float(v_ego + self.vRel)
        a_lead_derived = (self.vLead - self.vLead_last) / self.radar_ts * 0.2
        self.aLead = self.aLead * (1. - self.alpha_a) + a_lead_derived * self.alpha_a
        if abs(a_lead_vision) > abs(self.aLead):
          self.aLead = a_lead_vision

      dPath_current = self.yRel + np.interp(self.dRel, model_msg.position.x, model_msg.position.y)
      self.vLat = self.vLat * (1. - VISION_VLAT_ALPHA) + (dPath_current - self.dPath) / self.radar_ts * VISION_VLAT_ALPHA
      self.dPath = float(dPath_current)

      self.vLeadK = self.vLead
      self.aLeadK = self.aLead
      self.status = True
      self.cnt += 1
    else:
      self.reset()
      self.cnt = 0
      self.dPath = float(self.yRel + np.interp(v_ego ** 2 / (2 * 2.5), model_msg.position.x, model_msg.position.y))

    self.dRel_last = self.dRel
    self.vLead_last = self.vLead

    self.aLeadTau = VISION_ACCEL_TAU_LOW if abs(self.aLead) < VISION_ACCEL_THRESHOLD else self.aLeadTau * VISION_ACCEL_TAU_DECAY


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
    self.radar_track_enable = self.params.get_int("RadarTrackEnable")
    self.radar_detected = False
    self.leadCutIn = {'status': False}
    self.leadCenter = {'status': False}

  def _get_fused_lead_data(self, model_msg, tracks: dict[int, Track], index: int, lead_msg: capnp._DynamicStructReader, low_speed_override: bool = True) \
    -> tuple[dict[str, Any], bool]:
    v_ego = self.v_ego
    ready = self.ready

    track_scc = tracks.get(0) or tracks.get(1)
    track = None
    if len(tracks) > 0 and ready and lead_msg.prob > LEAD_PROB_THRESHOLD:
      track = match_vision_to_track(v_ego, lead_msg, tracks)

    if self.radar_track_enable in [-1, 2]:
      if track_scc is not None and track is None:
        track = track_scc

    lead_dict: Dict[str, Any] = {'status': False}
    radar_detected = False

    if track is not None:
      lead_dict = track.get_RadarState(model_msg, lead_msg.prob, self.vision_tracks[index].yRel)
      radar_detected = True
    elif ready and (lead_msg.prob > VISION_PROB_HIGH_THRESHOLD):
      lead_dict = self.vision_tracks[index].get_lead(model_msg)

    if low_speed_override:
      low_speed_tracks = [c for c in tracks.values() if c.potential_low_speed_lead(v_ego)]
      if len(low_speed_tracks) > 0:
        closest_track = min(low_speed_tracks, key=lambda c: c.dRel)

        if (not lead_dict['status']) or (closest_track.dRel < lead_dict['dRel']):
          lead_dict = closest_track.get_RadarState(model_msg, lead_msg.prob, self.vision_tracks[index].yRel)
          radar_detected = True

    return lead_dict, radar_detected

  def _compute_all_leads(self, v_ego: float, tracks: dict[int, Track], model_msg: capnp._DynamicStructReader):
    lead_msg = model_msg.leadsV3[0] if (model_msg is not None and len(model_msg.position.x) == 33) else None

    if lead_msg is None:
      self.radar_state.leadsLeft = []
      self.radar_state.leadsCenter = []
      self.radar_state.leadsRight = []
      return

    md_x, md_y = model_msg.position.x, model_msg.position.y
    lane_xs = model_msg.laneLines[1].x
    left_ys = model_msg.laneLines[1].y
    right_ys = model_msg.laneLines[2].y

    left_list, right_list, center_list = [], [], []

    for c in tracks.values():
      y_rel_neg = -c.yRel
      left_y = np.interp(c.dRel, lane_xs, left_ys)
      right_y = np.interp(c.dRel, lane_xs, right_ys)

      if left_y < y_rel_neg < right_y:
        if c.cnt > 6:
          ld = c.get_RadarState(model_msg, lead_msg.prob, float(-lead_msg.y[0]))
          center_list.append(ld)
      elif y_rel_neg < left_y:
        ld = c.get_RadarState(model_msg, 0, 0)
        left_list.append(ld)
      else:
        ld = c.get_RadarState(model_msg, 0, 0)
        right_list.append(ld)

      if self._is_cut_in(c, model_msg, left_y, right_y):
        if not self.leadCutIn['status'] or c.dRel < self.leadCutIn['dRel']:
          c.cut_in_count += 1
        else:
          c.cut_in_count = 0
        if c.cut_in_count > int(1.0 / DT_MDL):
          self.leadCutIn = c.get_RadarState(model_msg, lead_msg.prob)
      else:
        c.cut_in_count = 0

    if lead_msg.prob > LEAD_PROB_THRESHOLD:
      ld = self.vision_tracks[0].get_lead(model_msg)
      center_list.append(ld)

    self.radar_state.leadsLeft = left_list
    self.radar_state.leadsCenter = center_list
    self.radar_state.leadsRight = right_list

    self.radar_state.leadLeft = min(
      (lead for lead in left_list if lead['dRel'] > MIN_LEAD_SIDE_DRel and abs(lead['dPath']) < 3.5),
      key=lambda x: x['dRel'],
      default={'status': False}
    )
    self.radar_state.leadRight = min(
      (lead for lead in right_list if lead['dRel'] > MIN_LEAD_SIDE_DRel and abs(lead['dPath']) < 3.5),
      key=lambda x: x['dRel'],
      default={'status': False}
    )
    self.leadCenter = min(
      (lead for lead in center_list if lead['vLead'] > 5 and lead['radar']),
      key=lambda x: x['dRel'],
      default={'status': False}
    )

  def _is_cut_in(self, track: Track, model_msg, left_y, right_y) -> bool:
      dy = track.yRel_filtered + np.interp(track.dRel, model_msg.position.x, model_msg.position.y)
      dy_with_vel = dy + track.yvLead_filtered

      return (left_y < -dy_with_vel < right_y and
              3 < track.dRel < 20 and track.vLead > 4 and
              track.cnt > int(2.0 / DT_MDL) and
              track.yRel_filtered * track.yvLead_filtered < 0)

  def _select_final_lead(self):
    chosen_lead = None

    if self.leadCutIn and self.leadCutIn.get("status"):
      chosen_lead = self.leadCutIn
      chosen_lead["modelProb"] = 0.03

    elif self.leadCenter and self.leadCenter.get("status"):
      if self.radar_state.leadOne['status'] and self.leadCenter["dRel"] < self.radar_state.leadOne['dRel']:
        chosen_lead = self.leadCenter
        chosen_lead["modelProb"] = 0.01
      elif not self.radar_state.leadOne['status']:
        chosen_lead = self.leadCenter
        chosen_lead["modelProb"] = 0.02

    if chosen_lead is not None:
      self.radar_state.leadOne = chosen_lead
      self.radar_detected = chosen_lead.get('radar', False)

  def update(self, sm: messaging.SubMaster, rr: car.RadarData):
    self.ready = sm.seen['modelV2']
    self.current_time = 1e-9 * max(sm.logMonoTime.values())

    leads_v3 = sm['modelV2'].leadsV3
    if sm.recv_frame['carState'] != self.last_v_ego_frame:
      self.v_ego = sm['carState'].vEgo
      self.v_ego_hist.append(self.v_ego)
      self.last_v_ego_frame = sm.recv_frame['carState']

    valid_ids = set()
    for pt in rr.points:
      track_id = pt.trackId
      valid_ids.add(track_id)
      if track_id not in self.tracks:
        self.tracks[track_id] = Track(track_id)
      self.tracks[track_id].update(sm['modelV2'], pt, self.ready)

    for tid in list(self.tracks.keys()):
      if tid not in valid_ids:
        self.tracks.pop(tid)

    self.radar_state_valid = sm.all_checks()
    self.radar_state = log.RadarState.new_message()

    model_updated = self.radar_state.mdMonoTime != sm.logMonoTime['modelV2']

    self.radar_state.mdMonoTime = sm.logMonoTime['modelV2']
    self.radar_state.radarErrors = rr.errors
    self.radar_state.carStateMonoTime = sm.logMonoTime['carState']

    model_v_ego = sm['modelV2'].velocity.x[0] if sm['modelV2'].velocity.x else self.v_ego

    if len(leads_v3) > 1:
      if model_updated:
        if self.radar_detected:
          self.vision_tracks[0].reset()
          self.vision_tracks[1].reset()
        self.vision_tracks[0].update(leads_v3[0], model_v_ego, self.v_ego, sm['modelV2'])
        self.vision_tracks[1].update(leads_v3[1], model_v_ego, self.v_ego, sm['modelV2'])

      self.radar_state.leadOne, self.radar_detected = self._get_fused_lead_data(sm['modelV2'], self.tracks, 0, leads_v3[0], low_speed_override=False)
      self.radar_state.leadTwo, _ = self._get_fused_lead_data(sm['modelV2'], self.tracks, 1, leads_v3[1], low_speed_override=False)

      self._compute_all_leads(self.v_ego, self.tracks, sm['modelV2'])
      self._select_final_lead()

  def publish(self, pm: messaging.PubMaster):
    assert self.radar_state is not None

    radar_msg = messaging.new_message("radarState")
    radar_msg.valid = self.radar_state_valid
    radar_msg.radarState = self.radar_state
    pm.send("radarState", radar_msg)


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
