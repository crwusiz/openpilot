import colorsys
import numpy as np
import pyray as rl
from cereal import messaging, car
from dataclasses import dataclass, field
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.selfdrive.locationd.calibrationd import HEIGHT_INIT
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.shader_polygon import draw_polygon, Gradient
from openpilot.system.ui.widgets import Widget

CLIP_MARGIN = 500
MIN_DRAW_DISTANCE = 10.0
MAX_DRAW_DISTANCE = 100.0

class Colors:
  WHITE = rl.Color(255, 255, 255, 255) # rl.WHITE
  WARNING = rl.Color(218, 202, 37, 255)
  DANGER = rl.Color(201, 34, 49, 255)
  BLACK = rl.Color(0, 0, 0, 255) # rl.BLACK
  BLACK_TRANSLUCENT = rl.Color(0, 0, 0, 166)

THROTTLE_COLORS = [
  rl.Color(13, 248, 122, 102),   # HSLF(148/360, 0.94, 0.51, 0.4)
  rl.Color(114, 255, 92, 89),    # HSLF(112/360, 1.0, 0.68, 0.35)
  rl.Color(114, 255, 92, 0),     # HSLF(112/360, 1.0, 0.68, 0.0)
]

NO_THROTTLE_COLORS = [
  rl.Color(242, 242, 242, 102), # HSLF(148/360, 0.0, 0.95, 0.4)
  rl.Color(242, 242, 242, 89),  # HSLF(112/360, 0.0, 0.95, 0.35)
  rl.Color(242, 242, 242, 0),   # HSLF(112/360, 0.0, 0.95, 0.0)
]

STEERING_COLORS = [
  rl.Color(0, 191, 255, 102),
  rl.Color(0, 191, 255, 89),
  rl.Color(0, 191, 255, 0),
]

@dataclass
class ModelPoints:
  raw_points: np.ndarray = field(default_factory=lambda: np.empty((0, 3), dtype=np.float32))
  projected_points: np.ndarray = field(default_factory=lambda: np.empty((0, 2), dtype=np.float32))


@dataclass
class LeadInfo:
  d_rel: float = 0.0
  v_rel: float = 0.0
  point: tuple[float, float] | None = None


@dataclass
class LeadVehicle:
  glow: list = field(default_factory=list)
  chevron: list = field(default_factory=list)
  fill_poly: list = field(default_factory=list)
  fill_alpha: int = 0


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

    # Transform matrix (3x3 for car space to screen space)
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
      colors=STEERING_COLORS,
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
    if (sm.recv_frame["liveCalibration"] < ui_state.started_frame or
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

    live_calib = sm['liveCalibration']
    self._path_offset_z = live_calib.height[0] if live_calib.height else HEIGHT_INIT[0]

    if sm.updated['carParams']:
      self._longitudinal_control = sm['carParams'].openpilotLongitudinalControl

    model = sm['modelV2']
    radar_state = sm['radarState'] if sm.valid['radarState'] else None
    lead_one = radar_state.leadOne if radar_state else None
    #render_lead_indicator = self._longitudinal_control and radar_state is not None
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
      if render_lead_indicator:
        self._update_leads(radar_state, path_x_array)
      self._transform_dirty = False

    # Draw elements
    self._draw_lane_lines()
    self._draw_path(sm)

    if render_lead_indicator and radar_state:
      self._draw_lead_indicators(radar_state, rect)

  def _update_raw_points(self, model):
    self._path.raw_points = np.array([model.position.x, model.position.y, model.position.z], dtype=np.float32).T

    for i, lane_line in enumerate(model.laneLines):
      self._lane_lines[i].raw_points = np.array([lane_line.x, lane_line.y, lane_line.z], dtype=np.float32).T

    for i, road_edge in enumerate(model.roadEdges):
      self._road_edges[i].raw_points = np.array([road_edge.x, road_edge.y, road_edge.z], dtype=np.float32).T

    self._lane_line_probs = np.array(model.laneLineProbs, dtype=np.float32)
    self._road_edge_stds = np.array(model.roadEdgeStds, dtype=np.float32)
    self._acceleration_x = np.array(model.acceleration.x, dtype=np.float32)

  def _update_leads(self, radar_state, path_x_array):
    self._lead_vehicles = [LeadVehicle(), LeadVehicle()]
    self._lead_info = [LeadInfo(), LeadInfo()]
    leads = [radar_state.leadOne, radar_state.leadTwo]

    for i, lead_data in enumerate(leads):
      if lead_data and lead_data.status:
        d_rel, y_rel, v_rel = lead_data.dRel, lead_data.yRel, lead_data.vRel
        idx = self._get_path_length_idx(path_x_array, d_rel)

        # Get z-coordinate from path at the lead vehicle position
        z = self._path.raw_points[idx, 2] if idx < len(self._path.raw_points) else 0.0
        point = self._map_to_screen(d_rel, -y_rel, z + self._path_offset_z)
        if point:
          self._lead_vehicles[i] = self._update_lead_vehicle(d_rel, v_rel, point, self._rect)
          self._lead_info[i] = LeadInfo(d_rel=d_rel, v_rel=v_rel, point=point)

  def _update_model(self, lead, path_x_array):
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
    if lead and lead.status:
      lead_d = lead.dRel * 2.0
      max_distance = np.clip(lead_d - min(lead_d * 0.35, 10.0), 0.0, max_distance)

    max_idx = self._get_path_length_idx(path_x_array, max_distance)
    self._path.projected_points = self._map_line_to_polygon(
      self._path.raw_points, 0.9, self._path_offset_z, max_idx, max_distance, allow_invert=False
    )

    self._update_experimental_gradient()

  def _update_experimental_gradient(self):
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

  def _update_lead_vehicle(self, d_rel, v_rel, point, rect):
    speed_buff, lead_buff = 10.0, 40.0

    # Calculate fill alpha
    fill_alpha = 0
    if d_rel < lead_buff:
      fill_alpha = 255 * (1.0 - (d_rel / lead_buff))
      if v_rel < 0:
        fill_alpha += 255 * (-1 * (v_rel / speed_buff))
      fill_alpha = min(fill_alpha, 255)

    # Calculate size and position
    sz = np.clip((25 * 30) / (d_rel / 3 + 30), 15.0, 30.0) * 3.0
    px, py = point

    gap_offset = sz * np.interp(d_rel, [10, 100], [0.8, 0.3])
    base_y = min(py + gap_offset, rect.height - sz * 0.2)
    x = np.clip(px, 0.0, rect.width)

    half_w = sz * 3.5
    tick_h = sz * 0.3
    top_half_w = half_w * 0.6

    """
    g_xo = sz / 5
    g_yo = sz / 10

    glow = [(x + (sz * 1.35) + g_xo, y + sz + g_yo), (x, y - g_yo), (x - (sz * 1.35) - g_xo, y + sz + g_yo)]
    chevron = [(x + (sz * 1.25), y + sz), (x, y), (x - (sz * 1.25), y + sz)]
    """

    glow = []

    fill_poly = [
        (x - top_half_w, py - sz*0.1),
        (x - half_w, base_y),
        (x + half_w, base_y),
        (x + top_half_w, py - sz*0.1)
    ]

    chevron = [
        (x - half_w, base_y - tick_h),
        (x - half_w, base_y),
        (x + half_w, base_y),
        (x + half_w, base_y - tick_h)
    ]

    return LeadVehicle(glow=glow, chevron=chevron, fill_poly=fill_poly, fill_alpha=int(fill_alpha))

  def _draw_lane_lines(self):
    for i, lane_line in enumerate(self._lane_lines):
      if lane_line.projected_points.size == 0:
        continue

      alpha = np.clip(self._lane_line_probs[i], 0.0, 0.7)
      color = rl.Color(255, 255, 255, int(alpha * 255))
      draw_polygon(self._rect, lane_line.projected_points, color)

    # Draw blindspot barriers
    if self._left_blindspot and self._lane_barriers[0].projected_points.size > 0:
      color = rl.Color(255, 0, 0, 100)
      draw_polygon(self._rect, self._lane_barriers[0].projected_points, color)

    if self._right_blindspot and self._lane_barriers[1].projected_points.size > 0:
      color = rl.Color(255, 0, 0, 100)
      draw_polygon(self._rect, self._lane_barriers[1].projected_points, color)

    for i, road_edge in enumerate(self._road_edges):
      if road_edge.projected_points.size == 0:
        continue

      alpha = np.clip(1.0 - self._road_edge_stds[i], 0.0, 1.0)
      color = rl.Color(255, 0, 0, int(alpha * 255))
      draw_polygon(self._rect, road_edge.projected_points, color)

  def _draw_path(self, sm):
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
        draw_polygon(self._rect, self._path.projected_points, rl.Color(255, 255, 255, 30))
    else:
      # Blend throttle/no throttle colors based on transition
      blend_factor = round(self._blend_filter.x * 100) / 100
      blended_colors = self._blend_colors(NO_THROTTLE_COLORS, THROTTLE_COLORS, blend_factor)
      gradient = Gradient(
        start=(0.0, 1.0),  # Bottom of path
        end=(0.0, 0.0),  # Top of path
        colors=blended_colors,
        stops=[0.0, 0.5, 1.0],
      )
      draw_polygon(self._rect, self._path.projected_points, gradient=gradient)

  def _draw_lead_indicators(self, radar_state, rect):
    leads_data = [radar_state.leadOne, radar_state.leadTwo]

    for i, (lead_vehicle, lead_info, lead_data) in enumerate(zip(self._lead_vehicles, self._lead_info, leads_data, strict=True)):
      if not lead_vehicle.chevron:
        continue

      if i == 1 and abs(leads_data[0].dRel - leads_data[1].dRel) <= 3.0:
        continue

      if lead_vehicle.fill_poly:
        rl.draw_triangle_fan(lead_vehicle.fill_poly, 4, Colors.BLACK_TRANSLUCENT)

      # Draw glow and chevron
      #rl.draw_triangle_fan(lead_vehicle.glow, len(lead_vehicle.glow), rl.Color(218, 202, 37, 255))
      #rl.draw_triangle_fan(lead_vehicle.chevron, len(lead_vehicle.chevron), rl.Color(201, 34, 49, lead_vehicle.fill_alpha))

      bracket_color = rl.Color(201, 34, 49, lead_vehicle.fill_alpha)
      line_thick = 8.0
      pts = lead_vehicle.chevron

      if len(pts) == 4:
        rl.draw_line_ex(rl.Vector2(*pts[0]), rl.Vector2(*pts[1]), line_thick, bracket_color)
        rl.draw_line_ex(rl.Vector2(*pts[1]), rl.Vector2(*pts[2]), line_thick, bracket_color)
        rl.draw_line_ex(rl.Vector2(*pts[2]), rl.Vector2(*pts[3]), line_thick, bracket_color)

      # Draw distance and speed text
      if lead_info.point and lead_vehicle.fill_poly:
        d_rel = lead_info.d_rel
        v_rel = lead_info.v_rel

        dist_text = f"{d_rel:.0f} m"

        if ui_state.is_metric:
          spd_val = v_rel * 3.6
          spd_unit = "km/h"
        else:
          spd_val = v_rel * 2.236936
          spd_unit = "mph"
        sign = "+" if spd_val > 0 else ""
        speed_text = f"{sign}{spd_val:.0f} {spd_unit}"

        fill_top_y = lead_vehicle.fill_poly[0][1]
        fill_bottom_y = lead_vehicle.fill_poly[1][1]
        text_y = (fill_top_y + fill_bottom_y) / 2

        bracket_w = pts[2][0] - pts[1][0]
        offset_x = bracket_w * 0.35
        x_center = pts[1][0] + bracket_w / 2

        x_dist = x_center - offset_x
        x_spd = x_center + offset_x

        d_color = Colors.WHITE
        if d_rel < 10: d_color = Colors.DANGER
        v_color = Colors.WHITE
        if v_rel < -5: v_color = Colors.DANGER

        font_size = 32
        self._draw_text_centered(x_dist, text_y, dist_text, font_size, d_color, self._font_bold)
        self._draw_text_centered(x_spd, text_y, speed_text, font_size, v_color, self._font_bold)

        """
        # Calculate size for text positioning
        sz = np.clip((25 * 30) / (d_rel / 3 + 30), 15.0, 30.0) * 2.35
        text_y = y + sz / 1.5

        # Distance text
        d_color = rl.WHITE
        if d_rel < 15:
          d_color = rl.RED if d_rel < 5 else rl.Color(255, 149, 0, 255)  # Orange

        l_dist = f"{d_rel:.1f} m"
        self._draw_text_centered(x, text_y + 70.0, l_dist, 35, d_color, self._font_medium)

        # Speed text
        v_color = rl.Color(255, 191, 191, 255)  # Pink
        if v_rel < 0:
          v_color = rl.RED if v_rel < -4.4704 else rl.Color(255, 149, 0, 255)

        if ui_state.is_metric:
          l_speed = f"{self._speed + v_rel * 3.6:.0f} km/h"
        else:
          l_speed = f"{self._speed + v_rel * 2.236936:.0f} mph"

        self._draw_text_centered(x, text_y + 120.0, l_speed, 35, v_color, self._font_medium)
        """

  def _draw_text_centered(self, x: float, y: float, text: str, font_size: int, color: rl.Color, font: rl.Font):
    text_size = rl.measure_text_ex(font, text, font_size, 0)
    text_width = text_size.x
    text_height = text_size.y

    x_pos = int(x - text_width / 2)
    y_pos = int(y - text_height / 2)

    rl.draw_text_ex(font, text, rl.Vector2(x_pos, y_pos), font_size, 0, color)

  @staticmethod
  def _get_path_length_idx(pos_x_array: np.ndarray, path_distance: float) -> int:
    if len(pos_x_array) == 0:
      return 0
    indices = np.where(pos_x_array <= path_distance)[0]
    return indices[-1] if indices.size > 0 else 0

  def _map_to_screen(self, in_x, in_y, in_z):
    input_pt = np.array([in_x, in_y, in_z])
    pt = self._car_space_transform @ input_pt

    if abs(pt[2]) < 1e-6:
      return None

    x, y = pt[0] / pt[2], pt[1] / pt[2]

    clip = self._clip_region
    if not (clip.x <= x <= clip.x + clip.width and clip.y <= y <= clip.y + clip.height):
      return None

    return (x, y)

  def _map_line_to_polygon(self, line: np.ndarray, y_off: float, z_off: float, max_idx: int, max_distance: float, allow_invert: bool = True) -> np.ndarray:
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
