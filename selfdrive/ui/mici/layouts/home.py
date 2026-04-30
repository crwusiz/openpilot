import datetime
import time

from cereal import log
import pyray as rl
from collections.abc import Callable
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.layouts import HBoxLayout
from openpilot.system.ui.widgets.icon_widget import IconWidget
from openpilot.system.ui.widgets.label import UnifiedLabel, gui_label
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos, FONT_SCALE
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.version import RELEASE_BRANCHES

import subprocess
import threading
from pathlib import Path
from dataclasses import dataclass
from openpilot.common.params import Params
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets.network import WifiManagerUI, WifiManager

HEAD_BUTTON_FONT_SIZE = 40
HOME_PADDING = 8
SETTINGS_ZONE_WIDTH = 280
ALERTS_ZONE_WIDTH = 180

NetworkType = log.DeviceState.NetworkType

NETWORK_TYPES = {
  NetworkType.none: "Offline",
  NetworkType.wifi: "WiFi",
  NetworkType.cell2G: "2G",
  NetworkType.cell3G: "3G",
  NetworkType.cell4G: "LTE",
  NetworkType.cell5G: "5G",
  NetworkType.ethernet: "Ethernet",
}

def colors_alpha(color, alpha):
  if isinstance(color, tuple):
    return rl.Color(color[0], color[1], color[2], alpha)
  else:
    return rl.Color(color.r, color.g, color.b, alpha)

class Colors:
  WHITE = rl.WHITE
  WHITE_DIM = colors_alpha(WHITE, 85)
  GRAY = rl.Color(84, 84, 84, 255)
  WARNING = rl.Color(218, 202, 37, 255)
  DANGER = rl.Color(201, 34, 49, 255)
  BUTTON_PRESSED = colors_alpha(WHITE, 166)
  UP_TO_DATE = rl.Color(128, 216, 166, 255)

@dataclass(slots=True)
class MetricData:
  label: str
  value: str
  color: rl.Color

  def update(self, label: str, value: str, color: rl.Color):
    self.label = label
    self.value = value
    self.color = color


class AlertsPill(Widget):
  ICON_OFFSET = 12
  COUNT_OFFSET = 40

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 104, 52))

    self._pill_bg_txt = gui_app.texture("icons_mici/alerts_pill.png", 104, 52)
    self._icon_red = gui_app.texture("icons_mici/offroad_alerts/red_warning.png", 36, 36)
    self._icon_orange = gui_app.texture("icons_mici/offroad_alerts/orange_warning.png", 36, 36)
    self._icon_green = gui_app.texture("icons_mici/offroad_alerts/green_wheel.png", 36, 36)
    self._alert_count_callback: Callable[[], int] | None = None
    self._max_severity_callback: Callable[[], int | None] | None = None

  def set_alert_count_callback(self, callback: Callable[[], int] | None,
                               severity_callback: Callable[[], int | None] | None = None):
    self._alert_count_callback = callback
    self._max_severity_callback = severity_callback

  def _render(self, _):
    alert_count = self._alert_count_callback() if self._alert_count_callback else 0
    if alert_count > 0:
      pill_w, pill_h = self._pill_bg_txt.width, self._pill_bg_txt.height
      rl.draw_texture_ex(self._pill_bg_txt, rl.Vector2(self.rect.x, self.rect.y), 0.0, 1.0, rl.WHITE)

      severity = self._max_severity_callback() if self._max_severity_callback else None
      if severity == -1:
        warning_txt = self._icon_green
      elif severity is not None and severity > 0:
        warning_txt = self._icon_red
      else:
        warning_txt = self._icon_orange

      warn_x = self.rect.x + self.ICON_OFFSET
      warn_y = self.rect.y + (pill_h - warning_txt.height) / 2
      rl.draw_texture_ex(warning_txt, rl.Vector2(warn_x, warn_y), 0.0, 1.0, rl.WHITE)

      count_rect = rl.Rectangle(self.rect.x + self.COUNT_OFFSET, self.rect.y, pill_w - self.COUNT_OFFSET, pill_h)
      gui_label(count_rect, str(alert_count), font_size=36,
                alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER,
                alignment_vertical=rl.GuiTextAlignmentVertical.TEXT_ALIGN_MIDDLE)


class NetworkIcon(Widget):
  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 54, 44))  # max size of all icons
    self._net_type = NetworkType.none
    self._net_strength = 0

    self._wifi_slash_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_slash.png", 50, 44)
    self._wifi_none_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_none.png", 50, 37)
    self._wifi_low_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_low.png", 50, 37)
    self._wifi_medium_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_medium.png", 50, 37)
    self._wifi_full_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_full.png", 50, 37)

    self._cell_none_txt = gui_app.texture("icons_mici/settings/network/cell_strength_none.png", 54, 36)
    self._cell_low_txt = gui_app.texture("icons_mici/settings/network/cell_strength_low.png", 54, 36)
    self._cell_medium_txt = gui_app.texture("icons_mici/settings/network/cell_strength_medium.png", 54, 36)
    self._cell_high_txt = gui_app.texture("icons_mici/settings/network/cell_strength_high.png", 54, 36)
    self._cell_full_txt = gui_app.texture("icons_mici/settings/network/cell_strength_full.png", 54, 36)

  def _update_state(self):
    device_state = ui_state.sm['deviceState']
    self._net_type = device_state.networkType
    strength = device_state.networkStrength
    self._net_strength = max(0, min(5, strength.raw + 1)) if strength.raw > 0 else 0

  def _render(self, _):
    if self._net_type == NetworkType.wifi:
      # There is no 1
      draw_net_txt = {0: self._wifi_none_txt,
                      2: self._wifi_low_txt,
                      3: self._wifi_medium_txt,
                      4: self._wifi_full_txt,
                      5: self._wifi_full_txt}.get(self._net_strength, self._wifi_low_txt)
    elif self._net_type in (NetworkType.cell2G, NetworkType.cell3G, NetworkType.cell4G, NetworkType.cell5G):
      draw_net_txt = {0: self._cell_none_txt,
                      2: self._cell_low_txt,
                      3: self._cell_medium_txt,
                      4: self._cell_high_txt,
                      5: self._cell_full_txt}.get(self._net_strength, self._cell_none_txt)
    else:
      draw_net_txt = self._wifi_slash_txt

    draw_x = self._rect.x + (self._rect.width - draw_net_txt.width) / 2
    draw_y = self._rect.y + (self._rect.height - draw_net_txt.height) / 2

    if draw_net_txt == self._wifi_slash_txt:
      # Offset by difference in height between slashless and slash icons to make center align match
      draw_y -= (self._wifi_slash_txt.height - self._wifi_none_txt.height) / 2

    rl.draw_texture_ex(draw_net_txt, rl.Vector2(draw_x, draw_y), 0.0, 1.0, rl.Color(255, 255, 255, int(255 * 0.9)))


class MiciHomeLayout(Widget):
  def __init__(self):
    super().__init__()
    self._on_settings_click: Callable | None = None
    self._on_alerts_click: Callable | None = None
    self._alert_count_callback: Callable[[], int] | None = None

    self._last_refresh = 0
    self._mouse_down_t: None | float = None
    self._did_long_press = False
    self._is_pressed_prev = False

    self._version_text = None
    self._experimental_mode = False
    self._ip_address = "Offline"

    self.wifi_manager = WifiManager()
    self.wifi_manager_ui = WifiManagerUI(self.wifi_manager)

    self._settings_icon = IconWidget("icons_mici/settings.png", (48, 48), opacity=0.9)
    self._experimental_icon = IconWidget("icons_mici/experimental_mode.png", (48, 48))
    self._mic_icon = IconWidget("icons_mici/microphone.png", (32, 46))
    self._body_icon = IconWidget("icons_mici/body.png", (54, 37))

    self._alerts_pill = AlertsPill()

    self._status_bar_layout = HBoxLayout([
      self._settings_icon,
      NetworkIcon(),
      self._experimental_icon,
      self._body_icon,
      self._mic_icon,
    ], spacing=16)

    self._openpilot_label = UnifiedLabel("openpilot", font_size=76, font_weight=FontWeight.DISPLAY, max_width=480, wrap_text=False)

    self._font_semi_bold = gui_app.font(FontWeight.SEMI_BOLD)

    # --- Git Pull & Commit Check Variables ---
    self._params = Params()
    self._is_processing = False
    self._is_update_available = False
    self._initial_commit_check_done = False
    self._progress_dots = 0
    self._last_progress_update = 0

    self._git_pull_exit_file = Path("/data/gitpull_exit_code.log")
    self._commit_check_exit_file = Path("/data/commit_check_exit_code.log")

    self._commit_status = MetricData("UPDATE", "CHECK", Colors.WARNING)
    self._commit_btn_rect = rl.Rectangle(0, 0, 160, 64)

    serial_val = self._params.get("HardwareSerial")
    if serial_val:
      serial = serial_val.decode('utf-8') if isinstance(serial_val, bytes) else str(serial_val)
    else:
      serial = "unknown"
    self._hostname = f"comma-{serial}"

  def show_event(self):
    super().show_event()
    self._version_text = self._get_version_text()
    ip = self.wifi_manager_ui.ip_address
    self._ip_address = ip if ip else "Offline"
    self._update_params()

  def _update_params(self):
    self._experimental_mode = ui_state.params.get_bool("ExperimentalMode")

  def _is_network_connected(self) -> bool:
    try:
      if hasattr(ui_state, 'sm') and ui_state.sm is not None:
        if 'deviceState' in ui_state.sm.data:
          return ui_state.sm['deviceState'].networkType != NetworkType.none
    except Exception:
      pass
    return False

  def _update_state(self):
    self.wifi_manager_ui._update_state()

    if self.is_pressed and not self._is_pressed_prev:
      self._mouse_down_t = time.monotonic()
    elif not self.is_pressed and self._is_pressed_prev:
      self._mouse_down_t = None
      self._did_long_press = False
    self._is_pressed_prev = self.is_pressed

    if self._mouse_down_t is not None:
      if time.monotonic() - self._mouse_down_t > 0.5:
        if ui_state.has_longitudinal_control:
          self._experimental_mode = not self._experimental_mode
          ui_state.params.put("ExperimentalMode", self._experimental_mode)
        self._mouse_down_t = None
        self._did_long_press = True

    if rl.get_time() - self._last_refresh > 5.0:
      self._version_text = self._get_version_text()
      ip = self.wifi_manager_ui.ip_address
      self._ip_address = ip if ip else "Offline"
      self._last_refresh = rl.get_time()
      self._update_params()

    if self._is_network_connected() and not self._initial_commit_check_done and not self._is_processing:
      print("Network connected, starting initial commit check")
      self._initial_commit_check_done = True
      self._start_commit_check()

    self._update_progress_indicator()

  def set_callbacks(self, on_settings: Callable | None = None, on_alerts: Callable | None = None,
                    alert_count_callback: Callable[[], int] | None = None,
                    max_severity_callback: Callable[[], int | None] | None = None):
    self._on_settings_click = on_settings
    self._on_alerts_click = on_alerts
    self._alert_count_callback = alert_count_callback
    self._alerts_pill.set_alert_count_callback(alert_count_callback, max_severity_callback)

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if not self._did_long_press:
      relative_x = mouse_pos.x - self.rect.x
      has_alerts = self._alert_count_callback and self._alert_count_callback() > 0
      #if relative_x < SETTINGS_ZONE_WIDTH:
      if rl.check_collision_point_rec(mouse_pos, self._settings_icon.rect):
        if self._on_settings_click:
          self._on_settings_click()
      elif rl.check_collision_point_rec(mouse_pos, self._commit_btn_rect):
        self._handle_commit_button_press()
      elif has_alerts and relative_x > self.rect.width - ALERTS_ZONE_WIDTH:
        if self._on_alerts_click:
          self._on_alerts_click()
    self._did_long_press = False

  # --- Git & Commit Logic Methods ---
  def _handle_commit_button_press(self):
    if self._is_processing:
      print("Script execution already in progress, ignoring click")
      self._commit_status.update("BUSY", "WAIT", Colors.WARNING)
      return

    if not self._is_network_connected():
      print("Network not connected, cannot perform git operations")
      self._commit_status.update("NO NETWORK", "OFFLINE", Colors.DANGER)
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
        subprocess.Popen(["/bin/sh", "/data/openpilot/scripts/gitpull.sh"])
        start_time = time.time()
        while time.time() - start_time < 60:
          if self._git_pull_exit_file.exists():
            self._on_git_pull_finished()
            return
          time.sleep(1)
        self._on_git_pull_failed("TIMEOUT")
      except Exception as e:
        print(f"Failed to start git pull: {e}")
        self._on_git_pull_failed("FAILED TO START")

    thread = threading.Thread(target=run_git_pull, daemon=True)
    thread.start()

  def _on_git_pull_finished(self):
    try:
      self._git_pull_exit_file.read_text().strip()
      self._git_pull_exit_file.unlink(missing_ok=True)
      self._is_processing = False
      self._commit_status.update("UPDATE", "COMPLETE", Colors.WHITE)
    except Exception as e:
      print(f"Failed to read git pull exit code: {e}")
      self._on_git_pull_failed("FILE READ ERROR")

  def _on_git_pull_failed(self, reason: str):
    self._is_processing = False
    print(f"Git pull failed: {reason}")
    self._commit_status.update("git pull", reason, Colors.DANGER)

  def _start_commit_check(self):
    if self._is_processing:
      return

    self._is_processing = True
    self._commit_check_exit_file.unlink(missing_ok=True)

    def run_commit_check():
      try:
        subprocess.Popen(["/bin/sh", "/data/openpilot/scripts/commit_compare.sh"])
        start_time = time.time()
        while time.time() - start_time < 15:
          if self._commit_check_exit_file.exists():
            self._on_commit_check_finished()
            return
          time.sleep(1)
        self._on_commit_check_failed("TIMEOUT")
      except Exception as e:
        print(f"Failed to start commit check: {e}")
        self._on_commit_check_failed("FAILED TO START")

    thread = threading.Thread(target=run_commit_check, daemon=True)
    thread.start()

  def _on_commit_check_finished(self):
    try:
      exit_code_str = self._commit_check_exit_file.read_text().strip()
      self._commit_check_exit_file.unlink(missing_ok=True)
      exit_code = int(exit_code_str)

      if exit_code == 0:
        output = self._params.get("CommitCompare")
        self._parse_commit_compare_result(output)
      else:
        self._on_commit_check_failed("CHECK FAILED")
      self._is_processing = False
    except Exception as e:
      print(f"Failed to read commit check exit code: {e}")
      self._on_commit_check_failed("FILE READ ERROR")

  def _on_commit_check_failed(self, reason: str):
    self._is_processing = False
    self._is_update_available = False
    print(f"Commit check failed: {reason}")
    self._commit_status.update("CHECK", reason, Colors.DANGER)

  def _parse_commit_compare_result(self, output: str):
    if not output:
      self._on_commit_check_failed("EMPTY RESULT")
      return

    output = output.strip().strip('"')

    if " == " in output:
      parts = output.split(" == ")
      operator = "=="
    elif " != " in output:
      parts = output.split(" != ")
      operator = "!="
    else:
      self._on_commit_check_failed("PARSE ERROR")
      return

    if len(parts) != 2:
      self._on_commit_check_failed("INVALID FORMAT")
      return

    local_commit = parts[0].strip().strip('"')
    remote_commit = parts[1].strip().strip('"')

    if operator == "==":
      self._commit_status.update("UP TO DATE", local_commit, Colors.UP_TO_DATE)
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
      if self._is_update_available:
        self._commit_status.update("git pull", "progress" + dot_str, Colors.WARNING)
      else:
        self._commit_status.update("check", "progress" + dot_str, Colors.WARNING)

  def _get_version_text(self) -> tuple[str, str, str, str] | None:
    version = ui_state.params.get("Version")
    branch = ui_state.params.get("GitBranch")
    commit = ui_state.params.get("GitCommit")

    if not all((version, branch, commit)):
      return None

    commit_date_raw = ui_state.params.get("GitCommitDate")
    try:
      unix_ts = int(commit_date_raw.strip("'").split()[0])
      date_str = datetime.datetime.fromtimestamp(unix_ts).strftime("%Y/%m/%d")
    except (ValueError, IndexError, TypeError, AttributeError):
      date_str = ""

    return version, branch, commit[:7], date_str

  def _draw_commit_button(self, start_x: float):
    btn_height = 64

    self._commit_btn_rect.x = start_x + 12

    right_edge = self.rect.x + self.rect.width - HOME_PADDING
    self._commit_btn_rect.width = right_edge - self._commit_btn_rect.x

    if self._commit_btn_rect.width < 140:
      self._commit_btn_rect.width = 140
      self._commit_btn_rect.x = right_edge - 140

    self._commit_btn_rect.y = self.rect.y + self.rect.height - btn_height - HOME_PADDING

    edge_rect = rl.Rectangle(self._commit_btn_rect.x + 4, self._commit_btn_rect.y + 4, 100, btn_height - 8)
    rl.begin_scissor_mode(int(self._commit_btn_rect.x + 4), int(self._commit_btn_rect.y), 18, int(btn_height))
    rl.draw_rectangle_rounded(edge_rect, 0.3, 10, self._commit_status.color)
    rl.end_scissor_mode()

    rl.draw_rectangle_rounded_lines_ex(self._commit_btn_rect, 0.3, 10, 2, Colors.WHITE_DIM)

    font_size = 20
    labels = [self._commit_status.label, self._commit_status.value]
    total_text_height = len(labels) * font_size
    text_y = self._commit_btn_rect.y + (btn_height - total_text_height) / 2

    for text in labels:
      text_size = measure_text_cached(self._font_semi_bold, text, font_size)
      text_pos = rl.Vector2(
        self._commit_btn_rect.x + 22 + (self._commit_btn_rect.width - 22 - text_size.x) / 2,
        text_y
      )
      rl.draw_text_ex(self._font_semi_bold, text, text_pos, font_size, 0, Colors.WHITE)
      text_y += font_size + 2

  def _render(self, _):
    text_pos = rl.Vector2(self.rect.x - 2 + HOME_PADDING, self.rect.y - 16)
    self._openpilot_label.set_position(text_pos.x, text_pos.y)
    self._openpilot_label.render()

    openpilot_font = gui_app.font(FontWeight.DISPLAY)
    openpilot_text_size = measure_text_cached(openpilot_font, "openpilot", 76)
    openpilot_end_x = text_pos.x + openpilot_text_size.x

    if self._version_text is not None:
      release_branch = self._version_text[1] in RELEASE_BRANCHES
      branch_name = "release" if release_branch else self._version_text[1]

      font_size = 30
      version_text = f"v{self._version_text[0]}"
      rl.draw_text_ex(self._font_semi_bold, version_text, rl.Vector2(openpilot_end_x + 12, text_pos.y + 32), font_size + 8, 0, Colors.WHITE)

      line2_text = f"{self._version_text[3]}   {branch_name}"
      line2_size = measure_text_cached(self._font_semi_bold, line2_text, font_size)
      line2_y = text_pos.y + openpilot_text_size.y + 5
      rl.draw_text_ex(self._font_semi_bold, line2_text, rl.Vector2(text_pos.x + 8, line2_y), font_size, 0, Colors.GRAY)

      line3_y = line2_y + line2_size.y + 5
      ip_color = Colors.UP_TO_DATE if self._ip_address != "Offline" else Colors.GRAY
      rl.draw_text_ex(self._font_semi_bold, self._ip_address, rl.Vector2(text_pos.x + 8, line3_y), font_size, 0, ip_color)

      ip_size = measure_text_cached(self._font_semi_bold, self._ip_address, font_size)
      hostname_text = f" [{self._hostname}]"
      rl.draw_text_ex(self._font_semi_bold, hostname_text, rl.Vector2(text_pos.x + 8 + ip_size.x, line3_y), font_size, 0, Colors.WHITE)

    self._experimental_icon.set_visible(self._experimental_mode)
    self._mic_icon.set_visible(ui_state.recording_audio)
    self._body_icon.set_visible(ui_state.is_body)

    footer_rect = rl.Rectangle(self.rect.x + HOME_PADDING, self.rect.y + self.rect.height - 56, self.rect.width - HOME_PADDING, 56)
    self._status_bar_layout.render(footer_rect)

    self._draw_commit_button(openpilot_end_x)

    # TODO: add alignment to hboxlayout and add to there
    self._alerts_pill.set_position(self.rect.x + self.rect.width - self._alerts_pill.rect.width - HOME_PADDING,
                                   self.rect.y + self.rect.height - self._alerts_pill.rect.height)
    self._alerts_pill.render()
