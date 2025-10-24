import pyray as rl
import time
from dataclasses import dataclass
from collections.abc import Callable
from cereal import log
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos, FONT_SCALE
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

import subprocess
import threading
from pathlib import Path
from openpilot.common.params import Params

SIDEBAR_WIDTH = 300
METRIC_HEIGHT = 126
METRIC_WIDTH = 240
METRIC_MARGIN = 30
FONT_SIZE = 35

SETTINGS_BTN = rl.Rectangle(50, 35, 200, 117)
HOME_BTN = rl.Rectangle(60, 860, 180, 180)
COMMIT_BTN = rl.Rectangle(30, 762, 240, 126)

ThermalStatus = log.DeviceState.ThermalStatus
NetworkType = log.DeviceState.NetworkType


# Color scheme
class Colors:
  WHITE = rl.WHITE
  WHITE_DIM = rl.Color(255, 255, 255, 85)
  GRAY = rl.Color(84, 84, 84, 255)

  # Status colors
  GOOD = rl.WHITE
  WARNING = rl.Color(218, 202, 37, 255)
  DANGER = rl.Color(201, 34, 49, 255)
  UP_TO_DATE = rl.Color(128, 216, 166, 255)

  # UI elements
  METRIC_BORDER = rl.Color(255, 255, 255, 85)
  BUTTON_NORMAL = rl.WHITE
  BUTTON_PRESSED = rl.Color(255, 255, 255, 166)


NETWORK_TYPES = {
  NetworkType.none: tr_noop("--"),
  NetworkType.wifi: tr_noop("Wi-Fi"),
  NetworkType.ethernet: tr_noop("ETH"),
  NetworkType.cell2G: tr_noop("2G"),
  NetworkType.cell3G: tr_noop("3G"),
  NetworkType.cell4G: tr_noop("LTE"),
  NetworkType.cell5G: tr_noop("5G"),
}


@dataclass(slots=True)
class MetricData:
  label: str
  value: str
  color: rl.Color

  def update(self, label: str, value: str, color: rl.Color):
    self.label = label
    self.value = value
    self.color = color


class Sidebar(Widget):
  def __init__(self):
    super().__init__()
    self._net_type = NETWORK_TYPES.get(NetworkType.none)
    self._net_strength = 0

    self._temp_status = MetricData(tr_noop("TEMP"), tr_noop("GOOD"), Colors.GOOD)
    self._panda_status = MetricData(tr_noop("VEHICLE"), tr_noop("ONLINE"), Colors.GOOD)
    self._connect_status = MetricData(tr_noop("CONNECT"), tr_noop("OFFLINE"), Colors.WARNING)
    self._commit_status = MetricData(tr_noop("UPDATE"), tr_noop("CHECK"), Colors.WARNING)
    self._recording_audio = False

    self._home_img = gui_app.texture("images/button_home.png", HOME_BTN.width, HOME_BTN.height)
    self._flag_img = gui_app.texture("images/button_flag.png", HOME_BTN.width, HOME_BTN.height)
    self._settings_img = gui_app.texture("images/button_settings.png", SETTINGS_BTN.width, SETTINGS_BTN.height)
    self._c3x_img = gui_app.texture("icons/c3x.png", HOME_BTN.width, HOME_BTN.height)
    self._mic_img = gui_app.texture("icons/microphone.png", 30, 30)
    self._mic_indicator_rect = rl.Rectangle(0, 0, 0, 0)
    self._font_regular = gui_app.font(FontWeight.NORMAL)
    self._font_bold = gui_app.font(FontWeight.SEMI_BOLD)

    # Commit check & git pull state
    self._params = Params()
    self._is_processing = False
    self._is_update_available = False
    self._initial_commit_check_done = False
    self._commit_pressed = False
    self._progress_dots = 0
    self._last_progress_update = 0

    # File paths
    self._git_pull_exit_file = Path("/data/gitpull_exit_code.log")
    self._commit_check_exit_file = Path("/data/commit_check_exit_code.log")

    # Callbacks
    self._on_settings_click: Callable | None = None
    self._on_flag_click: Callable | None = None
    self._open_settings_callback: Callable | None = None

  def set_callbacks(self, on_settings: Callable | None = None, on_flag: Callable | None = None,
                    open_settings: Callable | None = None):
    self._on_settings_click = on_settings
    self._on_flag_click = on_flag
    self._open_settings_callback = open_settings

  def _is_network_connected(self) -> bool:
    """Check if network is connected"""
    return self._connect_status.color == Colors.GOOD

  def _handle_commit_button_press(self):
    """Handle commit button click"""
    if self._is_processing:
      print("Script execution already in progress, ignoring click")
      self._commit_status.update(tr_noop("BUSY"), tr_noop("WAIT"), Colors.WARNING)
      return

    if not self._is_network_connected():
      print("Network not connected, cannot perform git operations")
      self._commit_status.update(tr_noop("NO NETWORK"), tr_noop("OFFLINE"), Colors.DANGER)
      return

    if self._is_update_available:
      self._start_git_pull()
    else:
      self._start_commit_check()

  def _start_git_pull(self):
    """Start git pull process in background"""
    self._is_processing = True
    self._git_pull_exit_file.unlink(missing_ok=True)

    def run_git_pull():
      try:
        subprocess.Popen(["/bin/sh", "/data/openpilot/scripts/gitpull.sh"])

        # Wait for exit code file with timeout
        start_time = time.time()
        while time.time() - start_time < 60:  # 60 second timeout
          if self._git_pull_exit_file.exists():
            self._on_git_pull_finished()
            return
          time.sleep(1)

        # Timeout
        self._on_git_pull_failed(tr_noop("TIMEOUT"))
      except Exception as e:
        print(f"Failed to start git pull: {e}")
        self._on_git_pull_failed(tr_noop("FAILED TO START"))

    thread = threading.Thread(target=run_git_pull, daemon=True)
    thread.start()

  def _on_git_pull_finished(self):
    """Handle git pull completion"""
    try:
      exit_code = self._git_pull_exit_file.read_text().strip()
      self._git_pull_exit_file.unlink(missing_ok=True)

      # Regardless of exit code, mark as completed
      # In original code, exit code wasn't actually checked
      self._is_processing = False
      self._commit_status.update(tr_noop("UPDATE"), tr_noop("COMPLETE"), Colors.GOOD)

    except Exception as e:
      print(f"Failed to read git pull exit code: {e}")
      self._on_git_pull_failed(tr_noop("FILE READ ERROR"))

  def _on_git_pull_failed(self, reason: str):
    """Handle git pull failure"""
    self._is_processing = False
    print(f"Git pull failed: {reason}")
    self._commit_status.update(tr_noop("git pull"), reason, Colors.DANGER)

  def _start_commit_check(self):
    """Start commit check process in background"""
    if self._is_processing:
      return

    self._is_processing = True
    self._commit_check_exit_file.unlink(missing_ok=True)

    def run_commit_check():
      try:
        subprocess.Popen(["/bin/sh", "/data/openpilot/scripts/commit_compare.sh"])

        # Wait for exit code file with timeout
        start_time = time.time()
        while time.time() - start_time < 15:  # 15 second timeout
          if self._commit_check_exit_file.exists():
            self._on_commit_check_finished()
            return
          time.sleep(1)

        # Timeout
        self._on_commit_check_failed(tr_noop("TIMEOUT"))
      except Exception as e:
        print(f"Failed to start commit check: {e}")
        self._on_commit_check_failed(tr_noop("FAILED TO START"))

    thread = threading.Thread(target=run_commit_check, daemon=True)
    thread.start()

  def _on_commit_check_finished(self):
    """Handle commit check completion"""
    try:
      exit_code_str = self._commit_check_exit_file.read_text().strip()
      self._commit_check_exit_file.unlink(missing_ok=True)

      exit_code = int(exit_code_str)

      if exit_code == 0:
        output = self._params.get("CommitCompare")
        self._parse_commit_compare_result(output)
      else:
        self._on_commit_check_failed(tr_noop("CHECK FAILED"))

      self._is_processing = False

    except Exception as e:
      print(f"Failed to read commit check exit code: {e}")
      self._on_commit_check_failed(tr_noop("FILE READ ERROR"))

  def _on_commit_check_failed(self, reason: str):
    """Handle commit check failure"""
    self._is_processing = False
    self._is_update_available = False
    print(f"Commit check failed: {reason}")
    self._commit_status.update(tr_noop("CHECK"), reason, Colors.DANGER)

  def _parse_commit_compare_result(self, output: str):
    """Parse commit comparison result"""
    if not output:
      self._on_commit_check_failed(tr_noop("EMPTY RESULT"))
      return

    output = output.strip().strip('"')

    if " == " in output:
      parts = output.split(" == ")
      operator = "=="
    elif " != " in output:
      parts = output.split(" != ")
      operator = "!="
    else:
      self._on_commit_check_failed(tr_noop("PARSE ERROR"))
      return

    if len(parts) != 2:
      self._on_commit_check_failed(tr_noop("INVALID FORMAT"))
      return

    local_commit = parts[0].strip().strip('"')
    remote_commit = parts[1].strip().strip('"')

    if operator == "==":
      self._commit_status.update(tr_noop("UP TO DATE"), local_commit, Colors.UP_TO_DATE)
      self._is_update_available = False
    else:
      self._commit_status.update(local_commit, remote_commit, Colors.DANGER)
      self._is_update_available = True

  def _update_progress_indicator(self):
    """Update progress dots animation"""
    if not self._is_processing:
      return

    current_time = time.time()
    if current_time - self._last_progress_update >= 1.0:
      self._progress_dots = (self._progress_dots + 1) % 4
      self._last_progress_update = current_time

      dot_str = "." * self._progress_dots
      if self._is_update_available:
        self._commit_status.update(tr_noop("git pull"), tr_noop("progress") + dot_str, Colors.WARNING)
      else:
        self._commit_status.update(tr_noop("check"), tr_noop("progress") + dot_str, Colors.WARNING)

  def _render(self, rect: rl.Rectangle):
    # Background
    rl.draw_rectangle_rec(rect, rl.BLACK)

    self._draw_buttons(rect)
    self._draw_c3x_position(rect)
    self._draw_network_indicator(rect)
    self._draw_metrics(rect)

  def _update_state(self):
    sm = ui_state.sm
    if not sm.updated['deviceState']:
      return

    device_state = sm['deviceState']

    self._recording_audio = ui_state.recording_audio
    self._update_network_status(device_state)
    self._update_temperature_status(device_state)
    self._update_connection_status(device_state)
    self._update_panda_status()

    # Automatic initial commit check when network connects
    if self._is_network_connected() and not self._initial_commit_check_done and not self._is_processing:
      print("Network connected, starting initial commit check")
      self._initial_commit_check_done = True
      self._start_commit_check()

    # Update progress indicator
    self._update_progress_indicator()

  def _update_network_status(self, device_state):
    self._net_type = NETWORK_TYPES.get(device_state.networkType.raw, tr_noop("Unknown"))
    strength = device_state.networkStrength
    self._net_strength = max(0, min(5, strength.raw + 1)) if strength > 0 else 0

  def _update_temperature_status(self, device_state):
    thermal_status = device_state.thermalStatus
    max_temp = device_state.maxTempC

    if thermal_status == ThermalStatus.green:
      #self._temp_status.update(tr_noop("TEMP"), tr_noop("GOOD"), Colors.GOOD)
      self._temp_status.update(tr_noop("TEMP"), f"{max_temp}°C", Colors.GOOD)
    elif thermal_status == ThermalStatus.yellow:
      #self._temp_status.update(tr_noop("TEMP"), tr_noop("OK"), Colors.WARNING)
      self._temp_status.update(tr_noop("TEMP"), f"{max_temp}°C", Colors.WARNING)
    else:
      #self._temp_status.update(tr_noop("TEMP"), tr_noop("HIGH"), Colors.DANGER)
      self._temp_status.update(tr_noop("TEMP"), f"{max_temp}°C", Colors.DANGER)

  def _update_connection_status(self, device_state):
    last_ping = device_state.lastAthenaPingTime
    if last_ping == 0:
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("OFFLINE"), Colors.WARNING)
    elif time.monotonic_ns() - last_ping < 80_000_000_000:  # 80 seconds in nanoseconds
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("ONLINE"), Colors.GOOD)
    else:
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("ERROR"), Colors.DANGER)

  def _update_panda_status(self):
    if ui_state.panda_type == log.PandaState.PandaType.unknown:
      self._panda_status.update(tr_noop("NO"), tr_noop("PANDA"), Colors.DANGER)
    else:
      self._panda_status.update(tr_noop("VEHICLE"), tr_noop("ONLINE"), Colors.GOOD)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if rl.check_collision_point_rec(mouse_pos, SETTINGS_BTN):
      if self._on_settings_click:
        self._on_settings_click()
    elif rl.check_collision_point_rec(mouse_pos, HOME_BTN) and ui_state.started:
      #if self._on_flag_click:
      #  self._on_flag_click()
      # Home button click - reset calibration and request onroad cycle
      self._params.remove("CalibrationParams")
      self._params.remove("LiveTorqueParameters")
      self._params.remove("LiveParameters")
      self._params.remove("LiveParametersV2")
      self._params.remove("LiveDelay")
      self._params.put_bool("OnroadCycleRequested", True)
    elif self._recording_audio and rl.check_collision_point_rec(mouse_pos, self._mic_indicator_rect):
      if self._open_settings_callback:
        self._open_settings_callback()
    elif rl.check_collision_point_rec(mouse_pos, COMMIT_BTN):
      self._handle_commit_button_press()

    self._commit_pressed = False

  def _handle_mouse_press(self, mouse_pos: MousePos):
    if rl.check_collision_point_rec(mouse_pos, COMMIT_BTN):
      self._commit_pressed = True

  def _draw_buttons(self, rect: rl.Rectangle):
    mouse_pos = rl.get_mouse_position()
    mouse_down = self.is_pressed and rl.is_mouse_button_down(rl.MouseButton.MOUSE_BUTTON_LEFT)

    # Settings button
    settings_down = mouse_down and rl.check_collision_point_rec(mouse_pos, SETTINGS_BTN)
    tint = Colors.BUTTON_PRESSED if settings_down else Colors.BUTTON_NORMAL
    rl.draw_texture(self._settings_img, int(SETTINGS_BTN.x), int(SETTINGS_BTN.y), tint)

    # Home/Flag button
    #flag_pressed = mouse_down and rl.check_collision_point_rec(mouse_pos, HOME_BTN)
    #button_img = self._flag_img if ui_state.started else self._home_img

    #tint = Colors.BUTTON_PRESSED if (ui_state.started and flag_pressed) else Colors.BUTTON_NORMAL
    #rl.draw_texture(button_img, int(HOME_BTN.x), int(HOME_BTN.y), tint)

    # C3X image (always shown, not flag/home toggle)
    rl.draw_texture(self._c3x_img, int(HOME_BTN.x), int(HOME_BTN.y), Colors.BUTTON_NORMAL)

    # Microphone button
    if self._recording_audio:
      self._mic_indicator_rect = rl.Rectangle(rect.x + rect.width - 130, rect.y + 245, 75, 40)

      mic_pressed = mouse_down and rl.check_collision_point_rec(mouse_pos, self._mic_indicator_rect)
      bg_color = rl.Color(Colors.DANGER.r, Colors.DANGER.g, Colors.DANGER.b, int(255 * 0.65)) if mic_pressed else Colors.DANGER

      rl.draw_rectangle_rounded(self._mic_indicator_rect, 1, 10, bg_color)
      rl.draw_texture(self._mic_img, int(self._mic_indicator_rect.x + (self._mic_indicator_rect.width - self._mic_img.width) / 2),
                      int(self._mic_indicator_rect.y + (self._mic_indicator_rect.height - self._mic_img.height) / 2), Colors.WHITE)

  def _draw_c3x_position(self, rect: rl.Rectangle):
    """Draw C3X device position text"""
    c3x_position = self._params.get("DevicePosition")
    if not c3x_position:
      c3x_position = "--"

    # Draw position text below C3X image
    text_rect = rl.Rectangle(rect.x, rect.y + 1020, rect.width, 40)
    text_size = measure_text_cached(self._font_bold, c3x_position, 30)
    text_pos = rl.Vector2(
      text_rect.x + (text_rect.width - text_size.x) / 2,
      text_rect.y + (text_rect.height - text_size.y) / 2
    )
    rl.draw_text_ex(self._font_bold, c3x_position, text_pos, 30, 0, Colors.WHITE)

  def _draw_network_indicator(self, rect: rl.Rectangle):
    # Signal strength dots
    x_start = rect.x + 58
    y_pos = rect.y + 196
    dot_size = 27
    dot_spacing = 37

    for i in range(5):
      color = Colors.WHITE if i < self._net_strength else Colors.GRAY
      x = int(x_start + i * dot_spacing + dot_size // 2)
      y = int(y_pos + dot_size // 2)
      rl.draw_circle(x, y, dot_size // 2, color)

    # Network type text
    text_y = rect.y + 247
    text_pos = rl.Vector2(rect.x + 58, text_y)
    rl.draw_text_ex(self._font_regular, tr(self._net_type), text_pos, FONT_SIZE, 0, Colors.WHITE)

  def _draw_metrics(self, rect: rl.Rectangle):
    metrics = [
      (self._temp_status, 288),
      (self._panda_status, 288*1.5),
      (self._connect_status, 288*2),
      (self._commit_status, 288*2.5)
    ]

    for metric, y_offset in metrics:
      is_commit_metric = metric is self._commit_status
      self._draw_metric(rect, metric, rect.y + y_offset, is_commit_metric)

  def _draw_metric(self, rect: rl.Rectangle, metric: MetricData, y: float, is_commit: bool = False):
    metric_rect = rl.Rectangle(rect.x + METRIC_MARGIN, y, METRIC_WIDTH, METRIC_HEIGHT)

    # Apply opacity for commit button press
    if is_commit and self._commit_pressed:
      # Draw with reduced opacity
      pass  # Implement opacity if needed

    # Draw colored left edge (clipped rounded rectangle)
    edge_rect = rl.Rectangle(metric_rect.x + 4, metric_rect.y + 4, 100, 118)
    rl.begin_scissor_mode(int(metric_rect.x + 4), int(metric_rect.y), 18, int(metric_rect.height))
    rl.draw_rectangle_rounded(edge_rect, 0.3, 10, metric.color)
    rl.end_scissor_mode()

    # Draw border
    rl.draw_rectangle_rounded_lines_ex(metric_rect, 0.3, 10, 2, Colors.METRIC_BORDER)

    # Draw label and value
    labels = [tr(metric.label), tr(metric.value)]
    text_y = metric_rect.y + (metric_rect.height / 2 - len(labels) * FONT_SIZE * FONT_SCALE)
    for text in labels:
      text_size = measure_text_cached(self._font_bold, text, FONT_SIZE)
      text_y += text_size.y
      text_pos = rl.Vector2(
        metric_rect.x + 22 + (metric_rect.width - 22 - text_size.x) / 2,
        text_y
      )
      rl.draw_text_ex(self._font_bold, text, text_pos, FONT_SIZE, 0, Colors.WHITE)
