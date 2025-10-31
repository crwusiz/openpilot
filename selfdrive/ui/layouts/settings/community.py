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
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog

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

# 고정 헤더 높이 상수
HEADER_ROW_HEIGHT = 120
FIXED_HEADER_HEIGHT = HEADER_ROW_HEIGHT * 2
FONT_SIZE = 40
BUTTON_PADDING = 20


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

    self._current_tab = 0

    # Content Items
    self._toggle_items = []
    self._function_items = []
    self._log_items = []

    self._build_toggle_items()
    self._build_function_items()
    self._build_log_items()

    self._content_scroller = None
    self._update_content_scroller()

  def _on_select_manufacturer(self):
    manufacturers = ["[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"]
    current_selection = self._params.get("SelectedManufacturer")

    dialog = MultiOptionDialog(tr("Manufacturer"), manufacturers,
                               current=(current_selection if current_selection else manufacturers[0]))
    gui_app.set_modal_overlay(dialog)
    dialog_result = dialog._result

    if dialog_result is DialogResult.CONFIRM:
      selected_manufacturer = dialog.selection
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

  def _on_select_car(self):
    cars = ["[ Not Selected ]"] + get_list("/data/params/crwusiz/CarList")
    current_selection = self._params.get("SelectedCar")

    dialog = MultiOptionDialog(tr("Car"), cars,
                               current=(current_selection if current_selection else cars[0]))
    gui_app.set_modal_overlay(dialog)
    dialog_result = dialog._result

    if dialog_result is DialogResult.CONFIRM:
      selected_car = dialog.selection
      if selected_car == "[ Not Selected ]":
        self._params.remove("SelectedCar")
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])
      else:
        self._params.put("SelectedCar", selected_car)
        dlg = ConfirmDialog(selected_car, tr("OK"))
        gui_app.set_modal_overlay(dlg)
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])

  def _on_select_branch(self):
    branches = ["[ Not Selected ]"] + get_list("/data/params/crwusiz/GitBranchList")
    current_selection = self._params.get("SelectedBranch")

    dialog = MultiOptionDialog(tr("Branch"), branches,
                               current=(current_selection if current_selection else branches[0]))
    gui_app.set_modal_overlay(dialog)
    dialog_result = dialog._result

    if dialog_result is DialogResult.CONFIRM:
      selected_branch = dialog.selection
      if selected_branch == "[ Not Selected ]":
        self._params.remove("SelectedBranch")
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])
      else:
        self._params.put("SelectedBranch", selected_branch)
        dlg = ConfirmDialog(selected_branch, tr("OK"))
        gui_app.set_modal_overlay(dlg)
        subprocess.run(["pkill", "-9", "-f", "selfdrive.ui.ui"])

  def _draw_button(self, rect, text, is_selected=False, is_header=False):
    """커스텀 버튼 그리기"""
    # 배경 (헤더는 파란색, 선택된 탭은 어두운 회색)
    if is_header:
      rl.draw_rectangle_rec(rect, rl.Color(44, 44, 226, 255))  # #2C2CE2
    elif is_selected:
      rl.draw_rectangle_rec(rect, rl.Color(60, 60, 60, 255))
    else:
      rl.draw_rectangle_rec(rect, rl.Color(40, 40, 40, 255))

    # 테두리
    rl.draw_rectangle_lines_ex(rect, 1, rl.Color(80, 80, 80, 255))

    # 텍스트 중앙 정렬
    text_width = rl.measure_text(text, FONT_SIZE)
    text_x = rect.x + (rect.width - text_width) / 2
    text_y = rect.y + (rect.height - FONT_SIZE) / 2

    rl.draw_text(text, int(text_x), int(text_y), FONT_SIZE, rl.WHITE)

  def _is_point_in_rect(self, x, y, rect):
    """점이 사각형 안에 있는지 확인"""
    return (rect.x <= x <= rect.x + rect.width and
            rect.y <= y <= rect.y + rect.height)

  def _build_toggle_items(self):
    """Build main toggle items"""
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
    """Build function button items"""
    self._function_items = [
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
    """Build log view and upload button items"""
    self._log_items = [
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

  def _update_content_scroller(self):
    """Update content scroller with current tab's items"""
    items = []

    if self._current_tab == 0:
      items = self._toggle_items
    elif self._current_tab == 1:
      items = self._function_items
    elif self._current_tab == 2:
      items = self._log_items

    self._content_scroller = Scroller(items, line_separator=True, spacing=0)

  def _render(self, rect):
    """Render fixed headers (가로 그리드) and scrollable content"""
    self._rect = rect

    col_width = rect.width / 3

    # === 첫 번째 행: Manufacturer | Car | Branch ===
    # Manufacturer
    self._manufacturer_rect = rl.Rectangle(rect.x, rect.y, col_width, HEADER_ROW_HEIGHT)
    manufacturer_text = self._params.get("SelectedManufacturer")
    if not manufacturer_text:
      manufacturer_text = tr("Select your Manufacturer")
    self._draw_button(self._manufacturer_rect, manufacturer_text, is_header=True)

    # Car
    self._car_rect = rl.Rectangle(rect.x + col_width, rect.y, col_width, HEADER_ROW_HEIGHT)
    car_text = self._params.get("SelectedCar")
    if not car_text:
      car_text = tr("Select your car")
    self._draw_button(self._car_rect, car_text, is_header=True)

    # Branch
    self._branch_rect = rl.Rectangle(rect.x + col_width * 2, rect.y, col_width, HEADER_ROW_HEIGHT)
    branch_text = self._params.get("SelectedBranch")
    if not branch_text:
      branch_text = tr("Select Branch")
    self._draw_button(self._branch_rect, branch_text, is_header=True)

    # 구분선
    rl.draw_line(
      int(rect.x + 40), int(rect.y + HEADER_ROW_HEIGHT),
      int(rect.x + rect.width - 80), int(rect.y + HEADER_ROW_HEIGHT),
      rl.GRAY
    )

    # === 두 번째 행: Toggle | Function | Log ===
    tab_y = rect.y + HEADER_ROW_HEIGHT

    # Toggle
    self._toggle_rect = rl.Rectangle(rect.x, tab_y, col_width, HEADER_ROW_HEIGHT)
    self._draw_button(self._toggle_rect, tr("Toggle"), self._current_tab == 0)

    # Function
    self._function_rect = rl.Rectangle(rect.x + col_width, tab_y, col_width, HEADER_ROW_HEIGHT)
    self._draw_button(self._function_rect, tr("Function"), self._current_tab == 1)

    # Log
    self._log_rect = rl.Rectangle(rect.x + col_width * 2, tab_y, col_width, HEADER_ROW_HEIGHT)
    self._draw_button(self._log_rect, tr("Log"), self._current_tab == 2)

    # 구분선
    rl.draw_line(
      int(rect.x + 40), int(tab_y + HEADER_ROW_HEIGHT),
      int(rect.x + rect.width - 80), int(tab_y + HEADER_ROW_HEIGHT),
      rl.GRAY
    )

    # === 스크롤 가능한 콘텐츠 영역 ===
    content_rect = rl.Rectangle(
      rect.x,
      rect.y + FIXED_HEADER_HEIGHT,
      rect.width,
      rect.height - FIXED_HEADER_HEIGHT
    )
    self._content_scroller._rect = content_rect
    self._content_scroller.render(content_rect)

  def _handle_mouse_release(self, pos):
    """마우스 클릭 처리"""
    x, y = pos

    # 첫 번째 행 버튼 체크
    if self._is_point_in_rect(x, y, self._manufacturer_rect):
      self._on_select_manufacturer()
      return True
    elif self._is_point_in_rect(x, y, self._car_rect):
      self._on_select_car()
      return True
    elif self._is_point_in_rect(x, y, self._branch_rect):
      self._on_select_branch()
      return True

    # 두 번째 행 탭 버튼 체크
    elif self._is_point_in_rect(x, y, self._toggle_rect):
      self._switch_tab(0)
      return True
    elif self._is_point_in_rect(x, y, self._function_rect):
      self._switch_tab(1)
      return True
    elif self._is_point_in_rect(x, y, self._log_rect):
      self._switch_tab(2)
      return True

    return False

  def show_event(self):
    if self._content_scroller:
      self._content_scroller.show_event()

  def _switch_tab(self, tab_index: int):
    """Switch to a different tab"""
    if self._current_tab != tab_index:
      self._current_tab = tab_index
      self._update_content_scroller()

  # === Function Callbacks ===
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

    dialog = MultiOptionDialog(tr("Select Route to Upload"), options, current=options[0])
    gui_app.set_modal_overlay(dialog)

    if dialog._result is DialogResult.CONFIRM:
      selected_index = options.index(dialog.selection) if dialog.selection in options else 0
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
