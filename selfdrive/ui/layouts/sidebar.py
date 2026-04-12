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
from openpilot.system.ui.widgets.network import WifiManagerUI, WifiManager

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


def colors_alpha(color, alpha):
  if isinstance(color, tuple):
    return rl.Color(color[0], color[1], color[2], alpha)
  return rl.Color(color.r, color.g, color.b, alpha)


class Colors:
  WHITE = rl.WHITE
  WHITE_DIM = colors_alpha(WHITE, 85)
  GRAY = rl.Color(84, 84, 84, 255)
  WARNING = rl.Color(218, 202, 37, 255)
  DANGER = rl.Color(201, 34, 49, 255)
  BUTTON_PRESSED = colors_alpha(WHITE, 166)
  UP_TO_DATE = rl.Color(128, 216, 166, 255)


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

    self.wifi_manager = WifiManager()
    self.wifi_manager_ui = WifiManagerUI(self.wifi_manager)

    self._temp_status = MetricData(tr_noop("TEMP"), tr_noop("GOOD"), Colors.WHITE)
    self._panda_status = MetricData(tr_noop("VEHICLE"), tr_noop("ONLINE"), Colors.WHITE)
    self._connect_status = MetricData(tr_noop("CONNECT"), tr_noop("OFFLINE"), Colors.WARNING)
    self._commit_status = MetricData(tr_noop("UPDATE"), tr_noop("CHECK"), Colors.WARNING)
    self._recording_audio = False

    self._home_img = gui_app.texture("images/button_home.png", HOME_BTN.width, HOME_BTN.height)
    self._flag_img = gui_app.texture("images/button_flag.png", HOME_BTN.width, HOME_BTN.height)
    self._settings_img = gui_app.texture("images/button_settings.png", SETTINGS_BTN.width, SETTINGS_BTN.height)
    self._c3x_img = gui_app.texture("icons/c3x.png")
    self._mic_img = gui_app.texture("icons/microphone.png", 30, 30)
    self._mic_indicator_rect = rl.Rectangle(0, 0, 0, 0)
    self._font_regular = gui_app.font(FontWeight.NORMAL)
    self._font_semi_bold = gui_app.font(FontWeight.SEMI_BOLD)

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
    return self._connect_status.color == Colors.WHITE

  def _handle_commit_button_press(self):
    if self._is_processing:
      print("Script execution in progress. Click ignored.")
      self._commit_status.update(tr_noop("BUSY"), tr_noop("WAIT"), Colors.WARNING)
      return

    if not self._is_network_connected():
      print("Network not connected. Cannot perform git operations.")
      self._commit_status.update(tr_noop("NO NETWORK"), tr_noop("OFFLINE"), Colors.DANGER)
      return

    if self._is_update_available:
      self._start_git_pull()
    else:
      self._start_commit_check()

  def _start_git_pull(self):
    self._is_processing = True
    self._git_pull_exit_file.unlink(missing_ok=True)

    def run_git_pull():
      try:
        subprocess.run(["/bin/sh", "/data/openpilot/scripts/gitpull.sh"], timeout=60)

        if self._git_pull_exit_file.exists():
          self._on_git_pull_finished()
        else:
          self._on_git_pull_failed(tr_noop("NO LOG FILE"))
      except subprocess.TimeoutExpired:
        self._on_git_pull_failed(tr_noop("TIMEOUT"))
      except Exception as e:
        print(f"Failed to start git pull: {e}")
        self._on_git_pull_failed(tr_noop("FAILED TO START"))

    threading.Thread(target=run_git_pull, daemon=True).start()

  def _on_git_pull_finished(self):
    try:
      self._git_pull_exit_file.read_text().strip()  # Ignore exit code (same as original logic)
      self._git_pull_exit_file.unlink(missing_ok=True)

      self._is_processing = False
      self._commit_status.update(tr_noop("UPDATE"), tr_noop("COMPLETE"), Colors.WHITE)
    except Exception as e:
      print(f"Failed to read git pull exit code file: {e}")
      self._on_git_pull_failed(tr_noop("FILE READ ERROR"))

  def _on_git_pull_failed(self, reason: str):
    self._is_processing = False
    print(f"Git pull failed: {reason}")
    self._commit_status.update(tr_noop("git pull"), reason, Colors.DANGER)

  def _start_commit_check(self):
    if self._is_processing:
      return

    self._is_processing = True
    self._commit_check_exit_file.unlink(missing_ok=True)

    def run_commit_check():
      try:
        subprocess.run(["/bin/sh", "/data/openpilot/scripts/commit_compare.sh"], timeout=15)

        if self._commit_check_exit_file.exists():
          self._on_commit_check_finished()
        else:
          self._on_commit_check_failed(tr_noop("NO LOG FILE"))
      except subprocess.TimeoutExpired:
        self._on_commit_check_failed(tr_noop("TIMEOUT"))
      except Exception as e:
        print(f"Failed to start commit check: {e}")
        self._on_commit_check_failed(tr_noop("FAILED TO START"))

    threading.Thread(target=run_commit_check, daemon=True).start()

  def _on_commit_check_finished(self):
    try:
      exit_code_str = self._commit_check_exit_file.read_text().strip()
      self._commit_check_exit_file.unlink(missing_ok=True)

      if int(exit_code_str) == 0:
        self._parse_commit_compare_result(self._params.get("CommitCompare"))
      else:
        self._on_commit_check_failed(tr_noop("CHECK FAILED"))

      self._is_processing = False
    except Exception as e:
      print(f"Failed to read commit check exit code file: {e}")
      self._on_commit_check_failed(tr_noop("FILE READ ERROR"))

  def _on_commit_check_failed(self, reason: str):
    self._is_processing = False
    self._is_update_available = False
    print(f"Commit check failed: {reason}")
    self._commit_status.update(tr_noop("CHECK"), reason, Colors.DANGER)

  def _parse_commit_compare_result(self, output: str | None):
    if not output:
      self._on_commit_check_failed(tr_noop("EMPTY RESULT"))
      return

    output = output.strip().strip('"')

    operator = "==" if " == " in output else "!=" if " != " in output else None
    if not operator:
      self._on_commit_check_failed(tr_noop("PARSE ERROR"))
      return

    parts = output.split(f" {operator} ")
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
    if not self._is_processing:
      return

    current_time = time.time()
    if current_time - self._last_progress_update >= 1.0:
      self._progress_dots = (self._progress_dots + 1) % 4
      self._last_progress_update = current_time

      dot_str = "." * self._progress_dots
      action_text = tr_noop("git pull") if self._is_update_available else tr_noop("check")
      self._commit_status.update(action_text, tr_noop("progress") + dot_str, Colors.WARNING)

  def _render(self, rect: rl.Rectangle):
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

    # Automatic initial commit check on network connection
    if self._is_network_connected() and not self._initial_commit_check_done and not self._is_processing:
      print("Network connected. Starting initial commit check.")
      self._initial_commit_check_done = True
      self._start_commit_check()

    self._update_progress_indicator()
    self.wifi_manager_ui._update_state()

  def _update_network_status(self, device_state):
    ip_address = self.wifi_manager_ui.ip_address

    self._net_type = NETWORK_TYPES.get(device_state.networkType.raw, tr_noop("Unknown"))
    strength = device_state.networkStrength
    self._net_strength = max(0, min(5, strength.raw + 1)) if strength.raw > 0 else 0

    if self._net_strength > 0:
      self._net_type = ip_address if ip_address else NETWORK_TYPES.get(NetworkType.wifi)
    else:
      self._net_type = NETWORK_TYPES.get(NetworkType.none)

  def _update_temperature_status(self, device_state):
    thermal_status = device_state.thermalStatus
    max_temp = device_state.maxTempC
    temp_str = f"{max_temp:.1f}°C"

    if thermal_status == ThermalStatus.green:
      #self._temp_status.update(tr_noop("TEMP"), tr_noop("GOOD"), Colors.WHITE)
      self._temp_status.update(tr_noop("TEMP"), temp_str, Colors.WHITE)
    elif thermal_status == ThermalStatus.yellow:
      #self._temp_status.update(tr_noop("TEMP"), tr_noop("OK"), Colors.WARNING)
      self._temp_status.update(tr_noop("TEMP"), temp_str, Colors.WARNING)
    else:
      #self._temp_status.update(tr_noop("TEMP"), tr_noop("HIGH"), Colors.DANGER)
      self._temp_status.update(tr_noop("TEMP"), temp_str, Colors.DANGER)

  def _update_connection_status(self, device_state):
    last_ping = device_state.lastAthenaPingTime
    if last_ping == 0:
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("OFFLINE"), Colors.WARNING)
    elif time.monotonic_ns() - last_ping < 80_000_000_000:  # 80 seconds (in nanoseconds)
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("ONLINE"), Colors.WHITE)
    else:
      self._connect_status.update(tr_noop("CONNECT"), tr_noop("ERROR"), Colors.DANGER)

  def _update_panda_status(self):
    if ui_state.panda_type == log.PandaState.PandaType.unknown:
      self._panda_status.update(tr_noop("NO"), tr_noop("PANDA"), Colors.DANGER)
    else:
      self._panda_status.update(tr_noop("VEHICLE"), tr_noop("ONLINE"), Colors.WHITE)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if rl.check_collision_point_rec(mouse_pos, SETTINGS_BTN):
      if self._on_settings_click:
        self._on_settings_click()
    elif rl.check_collision_point_rec(mouse_pos, HOME_BTN) and ui_state.started:
      #if self._on_flag_click:
      #  self._on_flag_click()
      # Home button click - reset calibration and request onroad cycle
      for param in ("CalibrationParams", "LiveTorqueParameters", "LiveParameters", "LiveParametersV2", "LiveDelay"):
        self._params.remove(param)
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
    tint = Colors.BUTTON_PRESSED if settings_down else Colors.WHITE
    rl.draw_texture_ex(self._settings_img, rl.Vector2(SETTINGS_BTN.x, SETTINGS_BTN.y), 0.0, 1.0, tint)

    # Home/Flag button
    #flag_pressed = mouse_down and rl.check_collision_point_rec(mouse_pos, HOME_BTN)
    #button_img = self._flag_img if ui_state.started else self._home_img

    #tint = Colors.BUTTON_PRESSED if (ui_state.started and flag_pressed) else Colors.WHITE
    #rl.draw_texture_ex(button_img, rl.Vector2(HOME_BTN.x, HOME_BTN.y), 0.0, 1.0, tint)

    # C3X image (always shown, not flag/home toggle)
    c3x_scale = HOME_BTN.width / self._c3x_img.width if self._c3x_img.width > 0 else 1.0
    c3x_y = HOME_BTN.y + (HOME_BTN.height - (self._c3x_img.height * c3x_scale)) / 2
    rl.draw_texture_ex(self._c3x_img, rl.Vector2(HOME_BTN.x, c3x_y), 0.0, c3x_scale, Colors.WHITE)

    # Microphone button
    if self._recording_audio:
      self._mic_indicator_rect = rl.Rectangle(rect.x + rect.width - 130, rect.y + 245, 75, 40)
      mic_pressed = mouse_down and rl.check_collision_point_rec(mouse_pos, self._mic_indicator_rect)
      bg_color = rl.Color(Colors.DANGER.r, Colors.DANGER.g, Colors.DANGER.b, int(255 * 0.65)) if mic_pressed else Colors.DANGER

      rl.draw_rectangle_rounded(self._mic_indicator_rect, 1, 10, bg_color)
      mic_x = self._mic_indicator_rect.x + (self._mic_indicator_rect.width - self._mic_img.width) / 2
      mic_y = self._mic_indicator_rect.y + (self._mic_indicator_rect.height - self._mic_img.height) / 2
      rl.draw_texture_ex(self._mic_img, rl.Vector2(mic_x, mic_y), 0.0, 1.0, Colors.WHITE)

  def _draw_c3x_position(self, rect: rl.Rectangle):
    c3x_position = self._params.get("DevicePosition") or "--"

    text_rect = rl.Rectangle(rect.x, rect.y + 1000, rect.width, 40)
    text_size = measure_text_cached(self._font_semi_bold, c3x_position, 30)
    text_pos = rl.Vector2(
      text_rect.x + (text_rect.width - text_size.x) / 2,
      text_rect.y + (text_rect.height - text_size.y) / 2
    )
    rl.draw_text_ex(self._font_semi_bold, c3x_position, text_pos, 30, 0, Colors.WHITE)

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
    text_y = rect.y + 230
    text_str = tr(self._net_type)
    text_size = measure_text_cached(self._font_regular, text_str, FONT_SIZE)
    text_pos = rl.Vector2(rect.x + (rect.width - text_size.x) / 2, text_y)
    rl.draw_text_ex(self._font_regular, text_str, text_pos, FONT_SIZE, 0, Colors.WHITE)

  def _draw_metrics(self, rect: rl.Rectangle):
    metrics = [
      (self._temp_status, 288),
      (self._panda_status, 288 * 1.5),
      (self._connect_status, 288 * 2),
      (self._commit_status, 288 * 2.5)
    ]

    for metric, y_offset in metrics:
      self._draw_metric(rect, metric, rect.y + y_offset)

  def _draw_metric(self, rect: rl.Rectangle, metric: MetricData, y: float):
    metric_rect = rl.Rectangle(rect.x + METRIC_MARGIN, y, METRIC_WIDTH, METRIC_HEIGHT)

    # Colored edge (clipped rounded rectangle)
    edge_rect = rl.Rectangle(metric_rect.x + 4, metric_rect.y + 4, 100, 118)
    rl.begin_scissor_mode(int(metric_rect.x + 4), int(metric_rect.y), 18, int(metric_rect.height))
    rl.draw_rectangle_rounded(edge_rect, 0.3, 10, metric.color)
    rl.end_scissor_mode()

    # Border
    rl.draw_rectangle_rounded_lines_ex(metric_rect, 0.3, 10, 2, Colors.WHITE_DIM)

    # Text label
    labels = [tr(metric.label), tr(metric.value)]
    text_y = metric_rect.y + (metric_rect.height / 2 - len(labels) * FONT_SIZE * FONT_SCALE)
    for text in labels:
      text_size = measure_text_cached(self._font_semi_bold, text, FONT_SIZE)
      text_y += text_size.y
      text_pos = rl.Vector2(
        metric_rect.x + 22 + (metric_rect.width - 22 - text_size.x) / 2,
        text_y
      )
      rl.draw_text_ex(self._font_semi_bold, text, text_pos, FONT_SIZE, 0, Colors.WHITE)
