import numpy as np
import raylib_nb as rl
from openpilot.cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive import model_render_support
from openpilot.selfdrive.controls.radard import RADAR_TO_CAMERA
from openpilot.selfdrive.locationd.calibrationd import HEIGHT_INIT
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.mici.onroad.status import status_fade
from openpilot.selfdrive.ui.onroad.model_renderer import ModelRenderer as BaseModelRenderer, StopPoint as BaseStopPoint
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.polygon import draw_polygon

from dataclasses import dataclass
from openpilot.selfdrive.ui import Colors, colors_alpha


@dataclass
class LeadInfo:
  d_rel: float = 0.0
  v_rel: float = 0.0


class LeadVehicle:
  def __init__(self):
    self.bar = np.empty((0, 2), dtype=np.float32)
    self.d_filter = FirstOrderFilter(0.0, 0.2, 1 / gui_app.target_fps, initialized=False)
    self.y_filter = FirstOrderFilter(0.0, 0.2, 1 / gui_app.target_fps, initialized=False)
    self.fade_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)


class StopPoint(BaseStopPoint):
  def __init__(self):
    super().__init__()
    self.edges = [FirstOrderFilter(y, 0.2, 1 / gui_app.target_fps, initialized=False) for y in model_render_support.LANE_EDGES]


class ModelRenderer(BaseModelRenderer):
  LEAD_BAR_LENGTH = model_render_support.MICI_LEAD_BAR_LENGTH
  _STOP_POINT_TYPE = StopPoint

  def __init__(self):
    super().__init__()
    self._lead_bars = [LeadVehicle(), LeadVehicle()]
    self._lead_info = [LeadInfo(), LeadInfo()]
    self._visible = 0.0

  def _draw_offset(self) -> np.ndarray:
    # car space transform maps to rect-origin space
    return np.array([self._rect.x, self._rect.y], dtype=np.float32)

  def _render(self, rect: rl.Rectangle):
    sm = ui_state.sm

    # Check if data is up-to-date
    if not ui_state.received_since_started("extrinsicsCalibration") or not ui_state.received_since_started("modelV2"):
      return

    # Set up clipping region
    clip_margin = model_render_support.CLIP_MARGIN
    self._clip_region = rl.Rectangle(
      rect.x - clip_margin, rect.y - clip_margin, rect.width + 2 * clip_margin, rect.height + 2 * clip_margin
    )

    # Update state
    extrinsics_calibration = sm['extrinsicsCalibration']
    self._path_offset_z = extrinsics_calibration.height[0] if extrinsics_calibration.height else HEIGHT_INIT[0]

    if sm.updated['carParams']:
      self._longitudinal_control = sm['carParams'].openpilotLongitudinalControl

    model = sm['modelV2']
    radar_state = sm['radarState'] if sm.valid['radarState'] else None
    lead_one = radar_state.leadOne if radar_state else None
    render_lead_indicator = radar_state is not None

    # Update model data when needed
    model_updated = sm.updated['modelV2']
    if model_updated or sm.updated['radarState'] or self._transform_dirty:
      if model_updated:
        self._update_raw_points(model)

      path_x_array = self._path.raw_points[:, 0]
      if path_x_array.size == 0:
        return

      self._update_model(lead_one, path_x_array)
      self._transform_dirty = False

    # lane lines and path fade out while disengaged
    self._visible = 1 - status_fade.weight(UIStatus.DISENGAGED)
    if self._visible > 1e-2:
      self._draw_lane_lines()
      self._draw_path()

    if render_lead_indicator:
      self._update_lead_bars(sm)
      self._draw_lead_indicator()
    else:
      self._lead_bars = [LeadVehicle(), LeadVehicle()]
      self._lead_info = [LeadInfo(), LeadInfo()]
      self._stop_point = self._STOP_POINT_TYPE()

  def _update_lead_bars(self, sm):
    plan = sm['longitudinalPlan']
    model_leads = plan.longitudinalPlanSource == log.LongitudinalPlan.LongitudinalPlanSource.e2e and len(sm['modelV2'].leadsV3) > 1
    if model_leads:
      leads = [(lead.prob > 0.5, lead.x[0], -lead.y[0]) for lead in list(sm['modelV2'].leadsV3)[:2]]
      lead_info = [LeadInfo(lead.x[0], lead.v[0] - sm['carState'].vEgo) for lead in list(sm['modelV2'].leadsV3)[:2]]
    else:
      radar = sm['radarState']
      leads = [(lead.present, lead.dRel + RADAR_TO_CAMERA, lead.yRel) for lead in (radar.leadOne, radar.leadTwo)]
      lead_info = [LeadInfo(lead.dRel, lead.vRel) for lead in (radar.leadOne, radar.leadTwo)]

    # both leads can be the same vehicle
    if leads[0][0] and abs(leads[1][1] - leads[0][1]) < 3.0:
      leads[1] = (False, 0.0, 0.0)

    ss, cs = sm['selfdriveState'], sm['carState']
    # braking disengages without making openpilot unavailable
    available = ss.enabled or ss.engageable or cs.brakePressed
    lane = self._path.raw_points
    opacity = 0.8 - 0.4 * status_fade.weight(UIStatus.DISENGAGED)
    for i, (lead, (present, d_rel, y_rel)) in enumerate(zip(self._lead_bars, leads, strict=True)):
      visible = (available and present and 0.0 < d_rel < model_render_support.MAX_DRAW_DISTANCE
                 and len(lane) > 0 and len(self._path.raw_points) > 0)
      visible = visible and np.isfinite(y_rel)
      # snap to a new vehicle instead of sliding over
      if not visible or abs(y_rel - lead.y_filter.x) > 3.0:
        lead.d_filter.initialized = lead.y_filter.initialized = False
      lead.fade_filter.update(opacity if visible else 0.0)
      if visible:
        lead.bar = self._get_lead_bar(lane, lead.d_filter.update(d_rel), lead.y_filter.update(y_rel))
        self._lead_info[i] = lead_info[i]

    # A planned stop is brighter than a lead bar.
    self._update_stop_point(sm, leads, available, opacity + 0.2)

  def _get_stop_bar(self, path, d_stop):
    path_y = np.interp(d_stop, path[:, 0], path[:, 1])
    edges = []
    for edge_filter, lane_line, prob, default in zip(
        self._stop_point.edges, self._lane_lines[1:3], self._lane_line_probs[1:3], model_render_support.LANE_EDGES, strict=True):
      line = lane_line.raw_points
      offset = np.interp(d_stop, line[:, 0], line[:, 1]) - path_y if prob > model_render_support.LANE_LINE_PROB and len(line) else default
      edges.append(edge_filter.update(offset if np.isfinite(offset) else default))
    return self._get_lead_bar(path, d_stop, -path_y, edges)

  def _draw_lane_lines(self):
    """Draw white lane lines and red road edges, matching the big UI."""
    offset = self._draw_offset()

    for i, lane_line in enumerate(self._lane_lines):
      if lane_line.projected_points.size == 0:
        continue

      alpha = np.clip(self._lane_line_probs[i], 0.0, 0.7) * self._visible
      color = colors_alpha(rl.WHITE, int(alpha * 255))
      draw_polygon(self._rect, lane_line.projected_points + offset, color)

    for i, road_edge in enumerate(self._road_edges):
      if road_edge.projected_points.size == 0:
        continue

      alpha = np.clip(1.0 - self._road_edge_stds[i], 0.0, 1.0) * self._visible
      color = colors_alpha(Colors.RED, int(alpha * 255))
      draw_polygon(self._rect, road_edge.projected_points + offset, color)

  def _draw_lead_indicator(self):
    offset = self._draw_offset()
    for lead, lead_info in zip(self._lead_bars, self._lead_info, strict=True):
      alpha = int(255 * lead.fade_filter.x)
      if lead.bar.size == 0 or alpha == 0:
        continue

      draw_polygon(self._rect, lead.bar + offset, colors_alpha(Colors.RED, alpha))

      # Keep distance and relative-speed labels anchored to the vehicle end of the bar.
      center_x, center_y = lead.bar[[0, 3]].mean(axis=0) + offset
      d_rel, v_rel = lead_info.d_rel, lead_info.v_rel
      d_color = Colors.RED if d_rel < 5 else Colors.ORANGE if d_rel < 15 else rl.WHITE
      v_color = Colors.RED if v_rel < -5 else Colors.ORANGE if v_rel < 0 else rl.WHITE
      self._draw_text_centered(center_x, center_y - 19, f"{d_rel:.0f} m", 22, colors_alpha(d_color, alpha), self._font_bold)

      spd_val = v_rel * (3.6 if ui_state.is_metric else 2.236936)
      spd_unit = "km/h" if ui_state.is_metric else "mph"
      sign = "+" if spd_val > 0 else ""
      speed_text = f"{sign} {spd_val:.0f} {spd_unit}"
      self._draw_text_centered(center_x, center_y + 14, speed_text, 22, colors_alpha(v_color, alpha), self._font_bold)

    self._draw_stop_point()
