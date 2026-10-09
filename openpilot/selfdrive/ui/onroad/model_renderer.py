import colorsys
import numpy as np
import pyray as rl
from openpilot.cereal import log, messaging
from opendbc.car.structs import car
from dataclasses import dataclass, field
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.selfdrive.controls.radard import RADAR_TO_CAMERA
from openpilot.selfdrive.locationd.calibrationd import HEIGHT_INIT
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.shader_polygon import draw_polygon, Gradient
from openpilot.system.ui.widgets import Widget

from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.selfdrive.ui import Colors, colors_alpha


CLIP_MARGIN = 500
MIN_DRAW_DISTANCE = 10.0
MAX_DRAW_DISTANCE = 100.0
LEAD_BAR_LENGTH = 12.0  # px
LEAD_BAR_WIDTH = 1.8  # m


@dataclass
class ModelPoints:
  raw_points: np.ndarray = field(default_factory=lambda: np.empty((0, 3), dtype=np.float32))
  projected_points: np.ndarray = field(default_factory=lambda: np.empty((0, 2), dtype=np.float32))


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


class ModelRenderer(Widget):
  def __init__(self):
    super().__init__()
    self._longitudinal_control = False
    self._experimental_mode = False
    self._blend_filter = FirstOrderFilter(1.0, 0.25, 1 / gui_app.target_fps)
    self._prev_allow_throttle = True
    self._lane_line_probs = np.zeros(4, dtype=np.float32)
    self._road_edge_stds = np.zeros(2, dtype=np.float32)
    self._lead_vehicles = [LeadVehicle(), LeadVehicle()]
    self._lead_info = [LeadInfo(), LeadInfo()]
    self._path_offset_z = HEIGHT_INIT[0]
    self._speed = 0.0
    self._left_blindspot = False
    self._right_blindspot = False
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)

    # Initialize ModelPoints objects
    self._path = ModelPoints()
    self._lane_lines = [ModelPoints() for _ in range(4)]
    self._road_edges = [ModelPoints() for _ in range(2)]
    self._lane_barriers = [ModelPoints(), ModelPoints()]
    self._acceleration_x = np.empty((0,), dtype=np.float32)

    # 3x3 car space -> screen space (including rect.x/y)
    self._car_space_transform = np.zeros((3, 3), dtype=np.float32)
    self._transform_dirty = True
    self._clip_region = None

    self._exp_gradient = Gradient(
      start=(0.0, 1.0),  # Bottom of path
      end=(0.0, 0.0),  # Top of path
      colors=[],
      stops=[],
    )

    self._steering_pressed_gradient = Gradient(
      start=(0.0, 1.0),
      end=(0.0, 0.0),
      colors=Colors.STEERING_PRESSED,
      stops=[0.0, 0.5, 1.0],
    )

    # Get longitudinal control setting from car parameters
    if car_params := Params().get("CarParams"):
      cp = messaging.log_from_bytes(car_params, car.CarParams)
      self._longitudinal_control = cp.openpilotLongitudinalControl

  def set_transform(self, transform: np.ndarray):
    self._car_space_transform = transform.astype(np.float32)
    self._transform_dirty = True

  def _render(self, rect: rl.Rectangle):
    sm = ui_state.sm

    # Check if data is up-to-date
    if (sm.recv_frame["extrinsicsCalibration"] < ui_state.started_frame or
        sm.recv_frame["modelV2"] < ui_state.started_frame):
      return

    # Set up clipping region
    self._clip_region = rl.Rectangle(
      rect.x - CLIP_MARGIN, rect.y - CLIP_MARGIN, rect.width + 2 * CLIP_MARGIN, rect.height + 2 * CLIP_MARGIN
    )

    # Update state
    self._experimental_mode = sm['selfdriveState'].experimentalMode

    # Update speed and blindspot info
    car_state = sm['carState']
    if sm.valid['carState']:
      v_ego = car_state.vEgoCluster if car_state.vEgoCluster != 0.0 else car_state.vEgo
      self._speed = max(0.0, v_ego * (3.6 if ui_state.is_metric else 2.23694))
      self._left_blindspot = car_state.leftBlindspot
      self._right_blindspot = car_state.rightBlindspot

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

    # Draw elements
    self._draw_lane_lines()
    self._draw_path(sm)

    if render_lead_indicator:
      self._update_leads(sm)
      self._draw_lead_indicator()
    else:
      self._lead_vehicles = [LeadVehicle(), LeadVehicle()]
      self._lead_info = [LeadInfo(), LeadInfo()]

  def _update_raw_points(self, model):
    """Update raw 3D points from model data"""
    self._path.raw_points = np.array([model.position.x, model.position.y, model.position.z], dtype=np.float32).T

    for i, lane_line in enumerate(model.laneLines):
      self._lane_lines[i].raw_points = np.array([lane_line.x, lane_line.y, lane_line.z], dtype=np.float32).T

    for i, road_edge in enumerate(model.roadEdges):
      self._road_edges[i].raw_points = np.array([road_edge.x, road_edge.y, road_edge.z], dtype=np.float32).T

    self._lane_line_probs = np.array(model.laneLineProbs, dtype=np.float32)
    self._road_edge_stds = np.array(model.roadEdgeStds, dtype=np.float32)
    self._acceleration_x = np.array(model.acceleration.x, dtype=np.float32)

  def _update_leads(self, sm):
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
    left, right = self._lane_lines[1].raw_points, self._lane_lines[2].raw_points
    lane = (left + right) / 2 if left.shape == right.shape else np.empty((0, 3), dtype=np.float32)
    opacity = 0.4 if ui_state.status == UIStatus.DISENGAGED else 0.8
    for i, (lead, (present, d_rel, y_rel)) in enumerate(zip(self._lead_vehicles, leads, strict=True)):
      visible = available and present and 0.0 < d_rel < MAX_DRAW_DISTANCE and len(lane) > 0 and len(self._path.raw_points) > 0
      visible = visible and np.isfinite(y_rel)
      # snap to a new vehicle instead of sliding over
      if not visible or abs(y_rel - lead.y_filter.x) > 3.0:
        lead.d_filter.initialized = lead.y_filter.initialized = False
      lead.fade_filter.update(opacity if visible else 0.0)
      if visible:
        lead.bar = self._get_lead_bar(lane, lead.d_filter.update(d_rel), lead.y_filter.update(y_rel))
        self._lead_info[i] = lead_info[i]

  def _get_lead_bar(self, lane, d_rel, y_rel):
    # bar on the road behind the lead, following the lane
    x = np.array([d_rel, d_rel - min(6.0, 0.25 * d_rel)])
    y = np.interp(x, lane[:, 0], lane[:, 1]) - np.interp(d_rel, lane[:, 0], lane[:, 1]) - y_rel
    z = np.interp(x, self._path.raw_points[:, 0], self._path.raw_points[:, 2]) + self._path_offset_z
    corners = np.vstack((np.column_stack((x, y + LEAD_BAR_WIDTH / 2, z)), np.column_stack((x, y - LEAD_BAR_WIDTH / 2, z))[::-1]))
    pts = self._car_space_transform @ corners.T
    if not np.all(np.isfinite(pts)) or np.any(pts[2] <= 1e-6):
      return np.empty((0, 2), dtype=np.float32)
    bar = (pts[:2] / pts[2]).T

    far, near = bar[[0, 3]], bar[[1, 2]]
    length = np.linalg.norm(near.mean(axis=0) - far.mean(axis=0))
    if length <= 1e-6:
      return np.empty((0, 2), dtype=np.float32)
    bar[[1, 2]] = far + (near - far) * np.clip(length, 3.0, LEAD_BAR_LENGTH) / length
    return bar.astype(np.float32)

  def _update_model(self, lead, path_x_array):
    """Update model visualization data based on model message"""
    max_distance = np.clip(path_x_array[-1], MIN_DRAW_DISTANCE, MAX_DRAW_DISTANCE)
    max_idx = self._get_path_length_idx(self._lane_lines[0].raw_points[:, 0], max_distance)

    # Update lane lines using raw points
    for i, lane_line in enumerate(self._lane_lines):
      lane_line.projected_points = self._map_line_to_polygon(
        lane_line.raw_points, 0.025 * self._lane_line_probs[i], 0.0, max_idx, max_distance
      )

    # Update lane barriers for blindspot visualization (using lane lines 1 and 2)
    if self._left_blindspot or self._right_blindspot:
      self._lane_barriers[0].projected_points = self._map_line_to_polygon(
        self._lane_lines[1].raw_points, 0.025, 0.0, max_idx, max_distance
      )
      self._lane_barriers[1].projected_points = self._map_line_to_polygon(
        self._lane_lines[2].raw_points, 0.025, 0.0, max_idx, max_distance
      )

    # Update road edges using raw points
    for road_edge in self._road_edges:
      road_edge.projected_points = self._map_line_to_polygon(road_edge.raw_points, 0.025, 0.0, max_idx, max_distance)

    # Update path using raw points
    if lead and lead.present:
      lead_d = lead.dRel * 2.0
      max_distance = np.clip(lead_d - min(lead_d * 0.35, 10.0), 0.0, max_distance)

    max_idx = self._get_path_length_idx(path_x_array, max_distance)
    self._path.projected_points = self._map_line_to_polygon(
      self._path.raw_points, 0.9, self._path_offset_z, max_idx, max_distance, allow_invert=False
    )

    self._update_experimental_gradient()

  def _update_experimental_gradient(self):
    """Pre-calculate experimental mode gradient colors"""
    #if not self._experimental_mode:
    #  return

    max_len = min(len(self._path.projected_points) // 2, len(self._acceleration_x))

    segment_colors = []
    gradient_stops = []

    i = 0
    while i < max_len:
      # Some points (screen space) are out of frame (rect space)
      track_y = self._path.projected_points[i][1]
      if track_y < self._rect.y or track_y > (self._rect.y + self._rect.height):
        i += 1
        continue

      # Calculate color based on acceleration (0 is bottom, 1 is top)
      lin_grad_point = 1 - (track_y - self._rect.y) / self._rect.height

      # speed up: 120, slow down: 0
      path_hue = np.clip(60 + self._acceleration_x[i] * 35, 0, 120)

      saturation = min(abs(self._acceleration_x[i] * 1.5), 1)
      lightness = np.interp(saturation, [0.0, 1.0], [0.95, 0.62])
      alpha = np.interp(lin_grad_point, [0.75 / 2.0, 0.75], [0.4, 0.0])

      # Use HSL to RGB conversion
      color = self._hsla_to_color(path_hue / 360.0, saturation, lightness, alpha)

      gradient_stops.append(lin_grad_point)
      segment_colors.append(color)

      # Skip a point, unless next is last
      i += 1 + (1 if (i + 2) < max_len else 0)

    # Store the gradient in the path object
    self._exp_gradient = Gradient(
      start=(0.0, 1.0),  # Bottom of path
      end=(0.0, 0.0),  # Top of path
      colors=segment_colors,
      stops=gradient_stops,
    )

  def _draw_lane_lines(self):
    """Draw lane lines and road edges"""
    for i, lane_line in enumerate(self._lane_lines):
      if lane_line.projected_points.size == 0:
        continue

      alpha = np.clip(self._lane_line_probs[i], 0.0, 0.7)
      color = colors_alpha(rl.WHITE, int(alpha * 255))

      draw_polygon(self._rect, lane_line.projected_points, color)

    # Draw blindspot barriers
    if self._left_blindspot and self._lane_barriers[0].projected_points.size > 0:
      draw_polygon(self._rect, self._lane_barriers[0].projected_points, colors_alpha(Colors.RED, 100))

    if self._right_blindspot and self._lane_barriers[1].projected_points.size > 0:
      draw_polygon(self._rect, self._lane_barriers[1].projected_points, colors_alpha(Colors.RED, 100))

    for i, road_edge in enumerate(self._road_edges):
      if road_edge.projected_points.size == 0:
        continue

      alpha = np.clip(1.0 - self._road_edge_stds[i], 0.0, 1.0)
      color = colors_alpha(colors_alpha(Colors.RED, 100), int(alpha * 255))
      draw_polygon(self._rect, road_edge.projected_points, color)

  def _draw_path(self, sm):
    """Draw path with dynamic coloring based on mode and throttle state."""
    if not self._path.projected_points.size:
      return

    allow_throttle = sm['longitudinalPlan'].allowThrottle or not self._longitudinal_control
    self._blend_filter.update(int(allow_throttle))

    if ui_state.enabled:
      if ui_state.steeringPressed:
        draw_polygon(self._rect, self._path.projected_points, gradient=self._steering_pressed_gradient)
      elif len(self._exp_gradient.colors) > 1:
        draw_polygon(self._rect, self._path.projected_points, gradient=self._exp_gradient)
      else:
        draw_polygon(self._rect, self._path.projected_points, colors_alpha(rl.WHITE, 30))
    else:
      # Blend throttle/no throttle colors based on transition
      blend_factor = round(self._blend_filter.x * 100) / 100
      blended_colors = self._blend_colors(Colors.NO_THROTTLE, Colors.THROTTLE, blend_factor)
      gradient = Gradient(
        start=(0.0, 1.0),  # Bottom of path
        end=(0.0, 0.0),  # Top of path
        colors=blended_colors,
        stops=[0.0, 0.5, 1.0],
      )
      draw_polygon(self._rect, self._path.projected_points, gradient=gradient)

  def _draw_lead_indicator(self):
    for lead, lead_info in zip(self._lead_vehicles, self._lead_info, strict=True):
      alpha = int(255 * lead.fade_filter.x)
      if lead.bar.size == 0 or alpha == 0:
        continue

      # The big UI transform already includes the screen offset.
      draw_polygon(self._rect, lead.bar, colors_alpha(Colors.RED, alpha))

      center_x, center_y = lead.bar[[0, 3]].mean(axis=0)
      d_rel, v_rel = lead_info.d_rel, lead_info.v_rel
      d_color = Colors.RED if d_rel < 5 else Colors.ORANGE if d_rel < 15 else rl.WHITE
      v_color = Colors.RED if v_rel < -5 else Colors.ORANGE if v_rel < 0 else rl.WHITE
      self._draw_text_centered(center_x, center_y - 30, f"{d_rel:.0f} m", 32, colors_alpha(d_color, alpha), self._font_bold)

      spd_val = v_rel * (3.6 if ui_state.is_metric else 2.236936)
      spd_unit = "km/h" if ui_state.is_metric else "mph"
      sign = "+" if spd_val > 0 else ""
      speed_text = f"{sign} {spd_val:.0f} {spd_unit}"
      self._draw_text_centered(center_x, center_y + 20, speed_text, 32, colors_alpha(v_color, alpha), self._font_bold)

  def _draw_text_centered(self, x: float, y: float, text: str, font_size: int, color: rl.Color, font: rl.Font):
    text_size = measure_text_cached(font, text, font_size)
    text_width = text_size.x
    text_height = text_size.y

    x_pos = int(x - text_width / 2)
    y_pos = int(y - text_height / 2)

    rl.draw_text_ex(font, text, rl.Vector2(x_pos, y_pos), font_size, 0, color)

  @staticmethod
  def _get_path_length_idx(pos_x_array: np.ndarray, path_distance: float) -> int:
    """Get the index corresponding to the given path distance"""
    if len(pos_x_array) == 0:
      return 0
    indices = np.where(pos_x_array <= path_distance)[0]
    return indices[-1] if indices.size > 0 else 0

  def _map_line_to_polygon(self, line: np.ndarray, y_off: float, z_off: float, max_idx: int, max_distance: float, allow_invert: bool = True) -> np.ndarray:
    """Convert 3D line to 2D polygon for rendering."""
    if line.shape[0] == 0:
      return np.empty((0, 2), dtype=np.float32)

    # Slice points and filter non-negative x-coordinates
    points = line[:max_idx + 1]

    # Interpolate around max_idx so path end is smooth (max_distance is always >= p0.x)
    if 0 < max_idx < line.shape[0] - 1:
      p0 = line[max_idx]
      p1 = line[max_idx + 1]
      x0, x1 = p0[0], p1[0]
      interp_y = np.interp(max_distance, [x0, x1], [p0[1], p1[1]])
      interp_z = np.interp(max_distance, [x0, x1], [p0[2], p1[2]])
      interp_point = np.array([max_distance, interp_y, interp_z], dtype=points.dtype)
      points = np.concatenate((points, interp_point[None, :]), axis=0)

    points = points[points[:, 0] >= 0]
    if points.shape[0] == 0:
      return np.empty((0, 2), dtype=np.float32)

    N = points.shape[0]
    # Generate left and right 3D points in one array using broadcasting
    offsets = np.array([[0, -y_off, z_off], [0, y_off, z_off]], dtype=np.float32)
    points_3d = points[None, :, :] + offsets[:, None, :]  # Shape: 2xNx3
    points_3d = points_3d.reshape(2 * N, 3)  # Shape: (2*N)x3

    # Transform all points to projected space in one operation
    proj = self._car_space_transform @ points_3d.T  # Shape: 3x(2*N)
    proj = proj.reshape(3, 2, N)
    left_proj = proj[:, 0, :]
    right_proj = proj[:, 1, :]

    # Filter points where z is sufficiently large
    valid_proj = (np.abs(left_proj[2]) >= 1e-6) & (np.abs(right_proj[2]) >= 1e-6)
    if not np.any(valid_proj):
      return np.empty((0, 2), dtype=np.float32)

    # Compute screen coordinates
    left_screen = left_proj[:2, valid_proj] / left_proj[2, valid_proj][None, :]
    right_screen = right_proj[:2, valid_proj] / right_proj[2, valid_proj][None, :]

    # Define clip region bounds
    clip = self._clip_region
    x_min, x_max = clip.x, clip.x + clip.width
    y_min, y_max = clip.y, clip.y + clip.height

    # Filter points within clip region
    left_in_clip = (
      (left_screen[0] >= x_min) & (left_screen[0] <= x_max) &
      (left_screen[1] >= y_min) & (left_screen[1] <= y_max)
    )
    right_in_clip = (
      (right_screen[0] >= x_min) & (right_screen[0] <= x_max) &
      (right_screen[1] >= y_min) & (right_screen[1] <= y_max)
    )
    both_in_clip = left_in_clip & right_in_clip

    if not np.any(both_in_clip):
      return np.empty((0, 2), dtype=np.float32)

    # Select valid and clipped points
    left_screen = left_screen[:, both_in_clip]
    right_screen = right_screen[:, both_in_clip]

    # Handle Y-coordinate inversion on hills
    if not allow_invert and left_screen.shape[1] > 1:
      y = left_screen[1, :]  # y-coordinates
      keep = y == np.minimum.accumulate(y)
      if not np.any(keep):
        return np.empty((0, 2), dtype=np.float32)
      left_screen = left_screen[:, keep]
      right_screen = right_screen[:, keep]

    return np.vstack((left_screen.T, right_screen[:, ::-1].T)).astype(np.float32)

  @staticmethod
  def _hsla_to_color(h, s, l, a):
    rgb = colorsys.hls_to_rgb(h, l, s)
    return rl.Color(
      int(rgb[0] * 255),
      int(rgb[1] * 255),
      int(rgb[2] * 255),
      int(a * 255)
    )

  @staticmethod
  def _blend_colors(begin_colors, end_colors, t):
    if t >= 1.0:
      return end_colors
    if t <= 0.0:
      return begin_colors

    inv_t = 1.0 - t
    return [rl.Color(
      int(inv_t * start.r + t * end.r),
      int(inv_t * start.g + t * end.g),
      int(inv_t * start.b + t * end.b),
      int(inv_t * start.a + t * end.a)
    ) for start, end in zip(begin_colors, end_colors, strict=True)]
