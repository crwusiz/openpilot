import subprocess
import shutil
import os
import time
import pyray as rl

from datetime import datetime
from pathlib import Path
from typing import List, Callable, Union

from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.list_view import toggle_item, button_item, ListItem
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog # MultiOptionDialog 임포트 추가

# Description constants
DESCRIPTIONS = {
  'pcm_cruise': tr_noop(
    "Change the openpilot cruise engagement. use the PcmCruise method"
  ),
  'cruise_state_control': tr_noop(
    "Openpilot controls cruise on/off, set speed"
  ),
  'is_hda2': tr_noop(
    "Highway Drive Assist 2, turn it on"
  ),
  'camera_scc': tr_noop(
    "HDA1 CameraSCC CAR, HDA2 Connect the ADAS ECAN line to CAMERA modify, turn it on"
  ),
  'radar_track': tr_noop(
    "Enable Radar Track use (disable AEB)"
  ),
  'driver_cam_reverse': tr_noop(
    "Displays the driver camera when in reverse"
  ),
  'driver_cam_missing': tr_noop(
    "If there is a problem with the driver camera hardware, drive without the driver camera"
  ),
  'hardware_c3x': tr_noop(
    "Enable mr.one c3x lite hardware use"
  ),
  'logger_enable': tr_noop(
    "Turn off this option to reduce system load"
  ),
  'prebuilt_enable': tr_noop(
    "Create prebuilt file to speed bootup"
  ),
}


def get_list(path: str) -> List[str]:
  """Read lines from file and return as list"""
  try:
    with open(path, 'r', encoding='utf-8') as f:
      return [line.strip() for line in f if line.strip()]
  except FileNotFoundError:
    return []


def execute_script(script_path: str, *args) -> int:
  """Execute shell script and return exit code"""
  try:
    cmd = [script_path] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode
  except Exception as e:
    print(f"Error executing script: {e}")
    return 1


class CommunityLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()

    self._current_tab = 0  # 0: toggles, 1: functions, 2: logs

    # Header Widgets
    self._header_items = []
    self._build_header_buttons()

    # Tab Widgets
    self._tab_items = []
    self._build_tab_buttons() # 🌟 초기화 시 버튼 상태를 결정

    # Content Items
    self._toggle_items = []
    self._function_items = []
    self._log_items = []

    self._build_toggle_items()
    self._build_function_items()
    self._build_log_items()

    self._update_scroller()

  # --- Dialog Action Helpers (복구된 SelectDialog 로직) ---
  def _on_select_manufacturer(self):
    manufacturers = ["[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"]
    current_selection = self._params.get("SelectedManufacturer")

    # MultiOptionDialog 사용
    dialog = MultiOptionDialog(tr("Select your Manufacturer"), manufacturers,
                               current=(current_selection if current_selection else manufacturers[0]))
    gui_app.set_modal_overlay(dialog)

    # MultiOptionDialog는 선택 결과를 dialog.result가 아닌 dialog._result (DialogResult)와 dialog.selection (선택된 값)에 저장
    # MultiOptionDialog의 _render 메서드는 self._result를 반환하며, 이는 gui_app.set_modal_overlay의 반환값으로 사용될 수 있지만,
    # 여기서는 modal_overlay가 닫힌 후 dialog 객체에서 직접 결과를 가져옵니다.
    dialog_result = dialog._result

    if dialog_result is DialogResult.CONFIRM: # 'Select' 버튼을 눌렀을 때
      selected_manufacturer = dialog.selection # MultiOptionDialog는 선택된 문자열을 selection에 저장
      if selected_manufacturer == "[ Not Selected ]":
        self._params.remove("SelectedManufacturer")
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])
      else:
        car_list_file = ""
        if selected_manufacturer == "HYUNDAI":
          car_list_file = "/data/params/crwusiz/CarList_Hyundai"
        elif selected_manufacturer == "KIA":
          car_list_file = "/data/params/crwusiz/CarList_Kia"
        elif selected_manufacturer == "GENESIS":
          car_list_file = "/data/params/crwusiz/CarList_Genesis"

        if car_list_file:
          execute_script("cp", "-f", car_list_file, "/data/params/crwusiz/CarList")

        self._params.put("SelectedManufacturer", selected_manufacturer)
        dlg = ConfirmDialog(selected_manufacturer, tr("OK"))
        gui_app.set_modal_overlay(dlg)
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])

    self._build_header_buttons()
    self._update_scroller()

  def _on_select_car(self):
    cars = ["[ Not Selected ]"] + get_list("/data/params/crwusiz/CarList")
    current_selection = self._params.get("SelectedCar")

    # MultiOptionDialog 사용
    dialog = MultiOptionDialog(tr("Select your car"), cars,
                               current=(current_selection if current_selection else cars[0]))
    gui_app.set_modal_overlay(dialog)
    dialog_result = dialog._result

    if dialog_result is DialogResult.CONFIRM: # 'Select' 버튼을 눌렀을 때
      selected_car = dialog.selection
      if selected_car == "[ Not Selected ]":
        self._params.remove("SelectedCar")
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])
      else:
        self._params.put("SelectedCar", selected_car)
        dlg = ConfirmDialog(selected_car, tr("OK"))
        gui_app.set_modal_overlay(dlg)
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])
    self._build_header_buttons()
    self._update_scroller()

  def _on_select_branch(self):
    branches = ["[ Not Selected ]"] + get_list("/data/params/crwusiz/GitBranchList")
    current_selection = self._params.get("SelectedBranch")

    # MultiOptionDialog 사용
    dialog = MultiOptionDialog(tr("Select Branch"), branches,
                               current=(current_selection if current_selection else branches[0]))
    gui_app.set_modal_overlay(dialog)
    dialog_result = dialog._result

    if dialog_result is DialogResult.CONFIRM: # 'Select' 버튼을 눌렀을 때
      selected_branch = dialog.selection
      if selected_branch == "[ Not Selected ]":
        self._params.remove("SelectedBranch")
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])
      else:
        self._params.put("SelectedBranch", selected_branch)
        dlg = ConfirmDialog(selected_branch, tr("OK"))
        gui_app.set_modal_overlay(dlg)
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])
    self._build_header_buttons()
    self._update_scroller()

  def _build_header_buttons(self):
    """Build manufacturer, car, and branch selection buttons (row 1, 3-column layout)"""

    def get_manufacturer_text():
      selected = self._params.get("SelectedManufacturer")
      return selected if selected else tr("Select your Manufacturer")

    self._manufacturer_btn = button_item(
      title=lambda: tr("Manufacturer"),
      button_text=get_manufacturer_text,
      callback=self._on_select_manufacturer,
    )

    def get_car_text():
      selected = self._params.get("SelectedCar")
      return selected if selected else tr("Select your car")

    self._car_btn = button_item(
      title=lambda: tr("Car"),
      button_text=get_car_text,
      callback=self._on_select_car,
    )

    def get_branch_text():
      selected = self._params.get("SelectedBranch")
      return selected if selected else tr("Select Branch")

    self._branch_btn = button_item(
      title=lambda: tr("Branch"),
      button_text=get_branch_text,
      callback=self._on_select_branch,
    )

    self._header_items = [
      self._manufacturer_btn,
      self._car_btn,
      self._branch_btn,
    ]

  def _build_tab_buttons(self):
    """Build tab switcher buttons (row 2) - 🌟 FIX: Add visual feedback"""

    def get_button_text(index: int, default_text: str):
      text = tr(default_text)
      # 현재 탭이 선택된 경우 대괄호로 강조
      if self._current_tab == index:
          return f"[{text}]"
      return text

    self._toggle_tab_btn = button_item(
      title=lambda: tr("Tab"), # title을 'Tab'으로 통일
      button_text=lambda: get_button_text(0, "Toggle"),
      callback=lambda: self._switch_tab(0),
    )

    self._func_tab_btn = button_item(
      title=lambda: tr("Tab"),
      button_text=lambda: get_button_text(1, "Function"),
      callback=lambda: self._switch_tab(1),
    )

    self._log_tab_btn = button_item(
      title=lambda: tr("Tab"),
      button_text=lambda: get_button_text(2, "Log"),
      callback=lambda: self._switch_tab(2),
    )

    # 탭 버튼을 세로로 나열 (기존 코드 구조 유지)
    self._tab_items = [
      self._toggle_tab_btn,
      self._func_tab_btn,
      self._log_tab_btn,
    ]

  def _build_toggle_items(self):
    """Build main toggle items (shown in Toggle tab)"""
    is_c3xl = self._params.get_bool("HardwareC3xLite")

    self._toggle_items = [
      toggle_item(
        lambda: tr("PcmCruise"),
        description=lambda: DESCRIPTIONS["pcm_cruise"],
        initial_state=self._params.get_bool("PcmCruiseEnable"),
        callback=lambda state: self._params.put_bool("PcmCruiseEnable", state),
      ),
      toggle_item(
        lambda: tr("Cruise State Controls"),
        description=lambda: DESCRIPTIONS["cruise_state_control"],
        initial_state=self._params.get_bool("CruiseStateControl"),
        callback=lambda state: self._params.put_bool("CruiseStateControl", state),
      ),
      toggle_item(
        lambda: tr("CANFD Car HDA2"),
        description=lambda: DESCRIPTIONS["is_hda2"],
        initial_state=self._params.get_bool("IsHda2"),
        callback=lambda state: self._params.put_bool("IsHda2", state),
      ),
      toggle_item(
        lambda: tr("CameraSCC"),
        description=lambda: DESCRIPTIONS["camera_scc"],
        initial_state=self._params.get_bool("CameraSccEnable"),
        callback=lambda state: self._params.put_bool("CameraSccEnable", state),
      ),
      toggle_item(
        lambda: tr("Enable Radar Track use"),
        description=lambda: DESCRIPTIONS["radar_track"],
        initial_state=self._params.get_bool("RadarTrackEnable"),
        callback=lambda state: self._params.put_bool("RadarTrackEnable", state),
      ),
    ]

    if not is_c3xl:
      self._toggle_items.append(
        toggle_item(
          lambda: tr("Driver Camera On Reverse"),
          description=lambda: DESCRIPTIONS["driver_cam_reverse"],
          initial_state=self._params.get_bool("DriverCameraOnReverse"),
          callback=lambda state: self._params.put_bool("DriverCameraOnReverse", state),
        )
      )

    self._toggle_items.extend([
      toggle_item(
        lambda: tr("DriverCamera Hardware Missing"),
        description=lambda: DESCRIPTIONS["driver_cam_missing"],
        initial_state=self._params.get_bool("DriverCameraHardwareMissing"),
        callback=lambda state: self._params.put_bool("DriverCameraHardwareMissing", state),
      ),
      toggle_item(
        lambda: tr("Hardware is C3x Lite"),
        description=lambda: DESCRIPTIONS["hardware_c3x"],
        initial_state=self._params.get_bool("HardwareC3xLite"),
        callback=lambda state: self._params.put_bool("HardwareC3xLite", state),
      ),
      toggle_item(
        lambda: tr("Logger Enable"),
        description=lambda: DESCRIPTIONS["logger_enable"],
        initial_state=self._params.get_bool("LoggerEnable"),
        callback=lambda state: self._params.put_bool("LoggerEnable", state),
      ),
      toggle_item(
        lambda: tr("Prebuilt Enable"),
        description=lambda: DESCRIPTIONS["prebuilt_enable"],
        initial_state=self._params.get_bool("PrebuiltEnable"),
        callback=lambda state: self._params.put_bool("PrebuiltEnable", state),
      ),
    ])

  def _build_function_items(self):
    """Build function button items (shown in Function tab, 2-column grid)"""
    # 2열 그리드 구현을 위해 ListItem을 세로로 나열하는 기존 구조 유지
    self._function_items = [
      # Row 1
      button_item(
        title=lambda: tr("Git"),
        button_text=lambda: tr("Git Pull"),
        callback=self._on_git_pull,
      ),
      button_item(
        title=lambda: tr("Git"),
        button_text=lambda: tr("Git Checkout"),
        callback=self._on_git_checkout,
      ),
      # Row 2
      button_item(
        title=lambda: tr("Git"),
        button_text=lambda: tr("Git Reset -1"),
        callback=self._on_git_reset,
      ),
      button_item(
        title=lambda: tr("Build"),
        button_text=lambda: tr("Scons Rebuild"),
        callback=self._on_scons_rebuild,
      ),
      # Row 3
      button_item(
        title=lambda: tr("Panda"),
        button_text=lambda: tr("Panda Flash"),
        callback=self._on_panda_flash,
      ),
      button_item(
        title=lambda: tr("Panda"),
        button_text=lambda: tr("Panda Recover"),
        callback=self._on_panda_recover,
      ),
      # Row 4
      button_item(
        title=lambda: tr("Camera"),
        button_text=lambda: tr("Camera View"),
        callback=self._on_camera_view,
      ),
      button_item(
        title=lambda: tr("DTC"),
        button_text=lambda: tr("Clear DTC"),
        callback=self._on_clear_dtc,
      ),
    ]

  def _build_log_items(self):
    """Build log view and upload button items (shown in Log tab, 2-column grid)"""
    # 2열 그리드 구현을 위해 ListItem을 세로로 나열하는 기존 구조 유지
    self._log_items = [
      # Row 1
      button_item(
        title=lambda: tr("CAN Log"),
        button_text=lambda: tr("can missing log View"),
        callback=lambda: self._view_log("/data/can_missing.log"),
      ),
      button_item(
        title=lambda: tr("CAN Log"),
        button_text=lambda: tr("can timeout log View"),
        callback=lambda: self._view_log("/data/can_timeout.log"),
      ),
      # Row 2
      button_item(
        title=lambda: tr("Tmux"),
        button_text=lambda: tr("tmux log View"),
        callback=lambda: self._view_log("/data/tmux_error.log"),
      ),
      button_item(
        title=lambda: tr("Upload"),
        button_text=lambda: tr("tmux log Upload"),
        callback=lambda: self._upload_log("/data/tmux_error.log", "tmux_error.log"),
      ),
      # Row 3
      button_item(
        title=lambda: tr("Tmux Console"),
        button_text=lambda: tr("tmux console View"),
        callback=self._on_tmux_console_view,
      ),
      button_item(
        title=lambda: tr("Upload"),
        button_text=lambda: tr("tmux console Upload"),
        callback=self._on_tmux_console_upload,
      ),
      # Row 4
      button_item(
        title=lambda: tr("Upload"),
        button_text=lambda: tr("carParams dump Upload"),
        callback=self._on_carparams_dump,
      ),
      button_item(
        title=lambda: tr("Upload"),
        button_text=lambda: tr("Realdata Routes Upload"),
        callback=self._on_realdata_upload,
      ),
    ]


  # --- Tab Logic ---
  def _update_scroller(self):
    """Update scroller with current tab's content"""
    items = self._header_items + self._tab_items

    if self._current_tab == 0:
      items.extend(self._toggle_items)
    elif self._current_tab == 1:
      items.extend(self._function_items)
    elif self._current_tab == 2:
      items.extend(self._log_items)

    self._scroller = Scroller(items, line_separator=True, spacing=0)

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    self._scroller.show_event()

  def _switch_tab(self, tab_index: int):
    if self._current_tab != tab_index:
      self._current_tab = tab_index
      # 🌟 탭이 변경되면 버튼 상태를 새로고침하여 시각적 피드백(대괄호)을 업데이트
      self._build_tab_buttons()
      # 🌟 스크롤러 내용을 새 탭으로 업데이트
      self._update_scroller()
    else:
      # 이미 선택된 탭을 눌러도 렌더링 꼬임 방지를 위해 업데이트
      self._update_scroller()

  def _on_git_pull(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/gitpull.sh")
        if Path("/data/check_network.log").exists():
          dlg = ConfirmDialog(tr("Please Check Network Connection"), tr("OK"))
          gui_app.set_modal_overlay(dlg)

    dlg = ConfirmDialog(tr("Git Fetch and Reset<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  def _on_git_checkout(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/checkout.sh")

    dlg = ConfirmDialog(tr("Git Checkout<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  def _on_git_reset(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/reset.sh")

    dlg = ConfirmDialog(tr("Git Reset<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  def _on_scons_rebuild(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/scons_rebuild.sh")

    dlg = ConfirmDialog(tr("Scons Rebuild<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  def _on_panda_flash(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/panda/board/flash.py")

    dlg = ConfirmDialog(tr("Panda Flash<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  def _on_panda_recover(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/panda/board/recover.py")

    dlg = ConfirmDialog(tr("Panda Recover<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  def _on_camera_view(self):
    execute_script("/data/openpilot/selfdrive/ui/watch3.py")

  def _on_clear_dtc(self):
    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/cleardtc.sh")

    dlg = ConfirmDialog(tr("Clear DTC<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  # Log view/upload callbacks
  def _view_log(self, log_path: str):
    """View log file content"""
    if Path(log_path).exists():
      try:
        with open(log_path, 'r', encoding='utf-8') as f:
          content = f.read()
        dlg = ConfirmDialog(content, tr("OK"), rich=True)
        gui_app.set_modal_overlay(dlg)
      except Exception as e:
        dlg = ConfirmDialog(tr("Error reading log file"), tr("OK"))
        gui_app.set_modal_overlay(dlg)
    else:
      dlg = ConfirmDialog(tr("log file not found"), tr("OK"))
      gui_app.set_modal_overlay(dlg)

  def _upload_log(self, log_path: str, log_name: str):
    """Upload log file"""
    if Path(log_path).exists():
      def confirm_callback(result: int):
        if result == DialogResult.CONFIRM:
          execute_script("/data/openpilot/scripts/log_upload.sh", log_name)

      dlg = ConfirmDialog(tr(f"{log_name} upload<br><br>Process?"), tr("Process"), rich=True)
      gui_app.set_modal_overlay(dlg, callback=confirm_callback)
    else:
      dlg = ConfirmDialog(tr("log file not found"), tr("OK"))
      gui_app.set_modal_overlay(dlg)

  def _on_tmux_console_view(self):
    """View tmux console output"""
    try:
      result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", "0", "-S", "-250"],
        capture_output=True,
        text=True
      )
      if result.returncode == 0:
        dlg = ConfirmDialog(result.stdout, tr("OK"), rich=True)
        gui_app.set_modal_overlay(dlg)
    except Exception as e:
      dlg = ConfirmDialog(tr("Error reading tmux console"), tr("OK"))
      gui_app.set_modal_overlay(dlg)

  def _on_tmux_console_upload(self):
    """Upload tmux console output"""
    try:
      result = subprocess.run(
        ["sh", "-c", "tmux capture-pane -p -t 0 -S -250 > /data/tmux_console.log"],
        capture_output=True
      )
      if result.returncode == 0:
        def confirm_callback(result: int):
          if result == DialogResult.CONFIRM:
            execute_script("/data/openpilot/scripts/log_upload.sh", "tmux_console.log")

        dlg = ConfirmDialog(tr("tmux console log upload<br><br>Process?"), tr("Process"), rich=True)
        gui_app.set_modal_overlay(dlg, callback=confirm_callback)
      else:
        dlg = ConfirmDialog(tr("log file not found"), tr("OK"))
        gui_app.set_modal_overlay(dlg)
    except Exception as e:
      dlg = ConfirmDialog(tr("Error creating console log"), tr("OK"))
      gui_app.set_modal_overlay(dlg)

  def _on_carparams_dump(self):
    """Upload carParams dump"""

    def confirm_callback(result: int):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/dump_upload.sh", "carParams")

    dlg = ConfirmDialog(tr("carParams dump upload<br><br>Process?"), tr("Process"), rich=True)
    gui_app.set_modal_overlay(dlg, callback=confirm_callback)

  def _on_realdata_upload(self):
    """Upload realdata routes"""

    target_path = Path("/data/media/0/realdata")

    if not target_path.exists():
      dlg = ConfirmDialog(tr("Path does not exist"), tr("OK"))
      gui_app.set_modal_overlay(dlg)
      return

    route_map = {}
    for item in target_path.iterdir():
      if not item.is_dir() or item.name == "boot":
        continue

      parts = item.name.split("--")
      if len(parts) >= 3:
        route_name = f"{parts[0]}--{parts[1]}"

        if route_name not in route_map:
          route_map[route_name] = {
            'route_name': route_name,
            'segment_paths': [],
            'last_modified': item.stat().st_mtime,
            'segment_count': 0
          }

        route_map[route_name]['segment_paths'].append(str(item))
        route_map[route_name]['segment_count'] += 1
        route_map[route_name]['last_modified'] = max(
          route_map[route_name]['last_modified'],
          item.stat().st_mtime
        )

    if not route_map:
      dlg = ConfirmDialog(tr("Routes do not exist"), tr("OK"))
      gui_app.set_modal_overlay(dlg)
      return

    sorted_routes = sorted(
      route_map.values(),
      key=lambda x: x['last_modified'],
      reverse=True
    )

    recent_routes = sorted_routes[:10]
    options = []
    for route in recent_routes:
      dt_object = datetime.fromtimestamp(route['last_modified'])
      formatted_date = dt_object.strftime('%Y-%m-%d %H:%M')
      options.append(f"[{formatted_date}] {route['route_name']} ({route['segment_count']} segments)")

    dialog = SelectDialog(tr("Select Route to Upload"), options)
    gui_app.set_modal_overlay(dialog)
    selected_index = dialog.result

    if selected_index is not DialogResult.CANCEL:
      selected_route_info = recent_routes[selected_index]
      route_name = selected_route_info['route_name']
      segment_paths = selected_route_info['segment_paths']

      upload_dlg = ConfirmDialog(tr(f"Upload route {route_name}?"), tr("Yes"), tr("No"))
      gui_app.set_modal_overlay(upload_dlg)

      if upload_dlg.result == DialogResult.CONFIRM:
        script_path = "/data/openpilot/scripts/upload_realdata.sh"
        cmd = [script_path] + segment_paths

        try:
          result = subprocess.run(cmd, capture_output=True, text=True, check=False)

          if result.returncode == 0:
            dlg = ConfirmDialog(tr("Upload completed successfully"), tr("OK"))
          else:
            error_msg = tr("Upload failed") + f"\nExit Code: {result.returncode}"
            dlg = ConfirmDialog(error_msg, tr("OK"))

        except Exception as e:
          dlg = ConfirmDialog(tr("Error executing script:") + f"\n{e}", tr("OK"))

        gui_app.set_modal_overlay(dlg)
