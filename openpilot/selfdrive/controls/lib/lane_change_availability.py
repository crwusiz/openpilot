from collections import deque
from math import isfinite

import numpy as np

from openpilot.common.realtime import DT_MDL


class LaneChangeAvailability:
  """Per-side cluster availability, independent of the lane-change state machine."""

  def __init__(self, side):
    self.side = side
    self.widths = deque(maxlen=round(1.0 / DT_MDL))
    self.space_clear_frames = 0
    self.object_clear_frames = 0
    self.bsd_hold_frames = 0

  @staticmethod
  def _sample_y(line, distance):
    xs, ys = np.asarray(line.x), np.asarray(line.y)
    if (len(xs) < 2 or len(xs) != len(ys) or not np.all(np.isfinite(xs)) or not np.all(np.isfinite(ys))
        or not np.all(np.diff(xs) > 0) or not xs[0] <= distance <= xs[-1]):
      return None
    return float(np.interp(distance, xs, ys))

  def _update_space(self, model_data, v_ego):
    outer_idx, current_idx, edge_idx = (0, 1, 0) if self.side == 'left' else (3, 2, 1)
    lines = model_data.get('laneLines', ()) if model_data is not None else ()
    edges = model_data.get('roadEdges', ()) if model_data is not None else ()
    probs = model_data.get('laneLineProbs', ()) if model_data is not None else ()
    edge_stds = model_data.get('roadEdgeStds', ()) if model_data is not None else ()
    if (len(lines) <= max(outer_idx, current_idx) or len(edges) <= edge_idx
        or len(probs) <= max(outer_idx, current_idx) or len(edge_stds) <= edge_idx or not isfinite(v_ego)):
      self.widths.clear()
      self.space_clear_frames = 0
      return False

    # This model publishes lane/edge points at X_IDXS, not at time indices.
    near, far = max(5.0, v_ego), max(10.0, 2.0 * v_ego)
    samples = (self._sample_y(lines[outer_idx], near), self._sample_y(lines[current_idx], near),
               self._sample_y(edges[edge_idx], near), self._sample_y(lines[current_idx], far),
               self._sample_y(edges[edge_idx], far))
    if any(value is None for value in samples):
      self.widths.clear()
      self.space_clear_frames = 0
      return False

    outer, current, edge, current_far, edge_far = samples
    direction = -1 if self.side == 'left' else 1
    width = min(direction * (outer - current), direction * (edge - current))
    far_width = direction * (edge_far - current_far)
    self.widths.append(width)
    probability, outer_probability, edge_std = probs[current_idx], probs[outer_idx], edge_stds[edge_idx]
    quality_ok = (isfinite(probability) and 0.5 < probability <= 1.0
                  and isfinite(outer_probability) and 0.0 <= outer_probability <= 1.0
                  and isfinite(edge_std) and edge_std >= 0.0
                  and (outer_probability > 0.5 or edge_std < 0.5))
    space_ok = quality_ok and width > 2.5 and far_width > 2.5 and sum(self.widths) / len(self.widths) > 2.5
    self.space_clear_frames = self.space_clear_frames + 1 if space_ok else 0
    return self.space_clear_frames >= round(0.2 / DT_MDL)

  @staticmethod
  def _lead_is_unsafe(v_ego, lead):
    if not lead.present:
      return False
    d_rel, v_rel = float(lead.dRel), float(lead.vRel)
    if not isfinite(d_rel) or not isfinite(v_rel):
      return True
    distance = abs(d_rel)
    if distance <= 5.0:
      return True
    if distance >= 160.0:
      return False

    # Rear objects use the opposite sign for closing speed.
    relative_speed = v_rel if d_rel >= 0.0 else -v_rel
    min_gap = float(np.clip(v_ego * 0.30, 6.0, 12.0))
    closing_speed = max(0.0, -relative_speed)
    if closing_speed > 0.5:
      ttc_limit = float(np.interp(v_ego, [0.0, 15.0, 30.0], [2.0, 3.0, 3.5]))
      near_enough = distance < max(min_gap + 6.0, v_ego * 1.2)
      if near_enough and distance / closing_speed < ttc_limit:
        return True
      if distance + relative_speed * 1.5 < min_gap:
        return True
    return distance < min_gap and relative_speed < 1.0

  def update(self, carstate, model_data, radar_state, line_allowed):
    blindspot = getattr(carstate, f'{self.side}Blindspot')
    self.bsd_hold_frames = round(2.0 / DT_MDL) if blindspot else max(0, self.bsd_hold_frames - 1)
    radar_ok = radar_state is not None
    if radar_ok:
      errors = radar_state.radarErrors
      radar_ok = not (errors.canError or errors.radarFault or errors.wrongConfig or errors.radarUnavailableTemporary)
    v_ego = max(0.0, carstate.vEgo) if isfinite(carstate.vEgo) else 0.0
    if radar_ok:
      suffix = self.side.capitalize()
      leads = [getattr(radar_state, f'lead{suffix}'), *getattr(radar_state, f'leads{suffix}')]
      unsafe = any(self._lead_is_unsafe(v_ego, lead) for lead in leads)
      self.object_clear_frames = 0 if unsafe else min(round(0.3 / DT_MDL), self.object_clear_frames + 1)
    else:
      self.object_clear_frames = 0

    space_ok = self._update_space(model_data, carstate.vEgo)
    return (space_ok and line_allowed and radar_ok and self.bsd_hold_frames == 0
            and self.object_clear_frames >= round(0.3 / DT_MDL))
