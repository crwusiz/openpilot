import subprocess
import shutil
import time
import threading
import pyray as rl

from datetime import datetime
from pathlib import Path
from typing import List, Callable, Union

from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.list_view import toggle_item, button_item, ListItem
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
from openpilot.system.ui.widgets.html_render import HtmlModal, HtmlRenderer, ElementType
from openpilot.system.ui.lib.scroll_panel import GuiScrollPanel
from openpilot.system.ui.widgets.button import Button, ButtonStyle

DESCRIPTIONS = {
  'pcm_cruise': tr_noop(
    "Allow openpilot to engage using the stock PCM cruise buttons."
  ),
  'cruise_state_control': tr_noop(
    "Openpilot controls the cruise state (on/off) and set speed."
  ),
  'is_hda2': tr_noop(
    "Enable this option for Highway Driving Assist 2 (HDA2) vehicles."
  ),
  'camera_scc': tr_noop(
    "Enable Camera SCC. (For CANFD HDA1 (Connect Camera) or HDA2 (Connect ADAS Module))"
  ),
  'radar_track': tr_noop(
    "Enable the use of Radar Tracks (Note: This disables AEB)."
  ),
  'driver_cam_reverse': tr_noop(
    "Switch to the driver camera view when in reverse gear."
  ),
  'driver_cam_missing': tr_noop(
    "Enable driving without the driver monitoring camera (hardware failure)."
  ),
  'logger_enable': tr_noop(
    "Turn off this option to reduce system load"
  ),
  'prebuilt_enable': tr_noop(
    "Create prebuilt file to speed bootup"
  ),
}

HEADER_ROW_HEIGHT = 120
FIXED_HEADER_HEIGHT = HEADER_ROW_HEIGHT * 2
FONT_SIZE = 40
BUTTON_PADDING = 20

def get_list(path: str) -> List[str]:
  try:
    with open(path, 'r', encoding='utf-8') as f:
      return [line.strip() for line in f if line.strip()]
  except FileNotFoundError:
    return []


def execute_script(script_path: str, *args) -> int:
  try:
    cmd = [script_path] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode
  except Exception as e:
    print(f"Error executing script: {e}")
    return 1


class StaticLogModal(Widget):
  def __init__(self, text: str):
    super().__init__()
    self._content = HtmlRenderer(text=text)

    self._content.styles[ElementType.P]['size'] = 40
    self._content.styles[ElementType.P]['margin_top'] = 0
    self._content.styles[ElementType.P]['margin_bottom'] = 0
    self._content.styles[ElementType.BR]['margin_bottom'] = 0

    self._scroll_panel = GuiScrollPanel()
    self._ok_button = Button(tr("OK"), click_callback=lambda: gui_app.pop_widget(), button_style=ButtonStyle.PRIMARY)

  def _render(self, rect: rl.Rectangle):
    margin = 50
    content_rect = rl.Rectangle(rect.x + margin, rect.y + margin, rect.width - (margin * 2), rect.height - (margin * 2))

    button_height = 160
    button_spacing = 20
    scrollable_height = content_rect.height - button_height - button_spacing

    scrollable_rect = rl.Rectangle(content_rect.x, content_rect.y, content_rect.width, scrollable_height)

    total_height = self._content.get_total_height(int(scrollable_rect.width))
    scroll_content_rect = rl.Rectangle(scrollable_rect.x, scrollable_rect.y, scrollable_rect.width, total_height)
    scroll_offset = self._scroll_panel.update(scrollable_rect, scroll_content_rect)
    scroll_content_rect.y += scroll_offset

    rl.begin_scissor_mode(int(scrollable_rect.x), int(scrollable_rect.y), int(scrollable_rect.width), int(scrollable_rect.height))
    self._content.render(scroll_content_rect)
    rl.end_scissor_mode()

    button_width = (rect.width - 3 * 50) // 3
    button_x = content_rect.x + content_rect.width - button_width
    button_y = content_rect.y + content_rect.height - button_height
    button_rect = rl.Rectangle(button_x, button_y, button_width, button_height)
    self._ok_button.render(button_rect)

    return -1


class TmuxLogModal(Widget):
  def __init__(self):
    super().__init__()
    self._content = HtmlRenderer(text="Loading...")

    self._content.styles[ElementType.P]['size'] = 40
    self._content.styles[ElementType.P]['margin_top'] = 0
    self._content.styles[ElementType.P]['margin_bottom'] = 0
    self._content.styles[ElementType.BR]['margin_bottom'] = 0

    self._scroll_panel = GuiScrollPanel()
    self._ok_button = Button(tr("OK"), click_callback=lambda: gui_app.pop_widget(), button_style=ButtonStyle.PRIMARY)
    self._last_update = 0.0

  def _render(self, rect: rl.Rectangle):
    t = time.monotonic()
    if t - self._last_update > 0.5:
      self._last_update = t
      try:
        cmd = ["sh", "-c", "tmux capture-pane -p -t 0 -S -500"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
          text = result.stdout.replace('\n', '<br>')
          self._content.parse_html_content(text)
      except Exception:
        pass

    margin = 50
    content_rect = rl.Rectangle(rect.x + margin, rect.y + margin, rect.width - (margin * 2), rect.height - (margin * 2))

    button_height = 160
    button_spacing = 20
    scrollable_height = content_rect.height - button_height - button_spacing

    scrollable_rect = rl.Rectangle(content_rect.x, content_rect.y, content_rect.width, scrollable_height)

    total_height = self._content.get_total_height(int(scrollable_rect.width))
    scroll_content_rect = rl.Rectangle(scrollable_rect.x, scrollable_rect.y, scrollable_rect.width, total_height)
    scroll_offset = self._scroll_panel.update(scrollable_rect, scroll_content_rect)
    scroll_content_rect.y += scroll_offset

    rl.begin_scissor_mode(int(scrollable_rect.x), int(scrollable_rect.y), int(scrollable_rect.width), int(scrollable_rect.height))
    self._content.render(scroll_content_rect)
    rl.end_scissor_mode()

    button_width = (rect.width - 3 * 50) // 3
    button_x = content_rect.x + content_rect.width - button_width
    button_y = content_rect.y + content_rect.height - button_height
    button_rect = rl.Rectangle(button_x, button_y, button_width, button_height)
    self._ok_button.render(button_rect)

    return -1


class CommunityLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()

    self._current_tab = 0

    self._toggle_items = []
    self._function_items = []
    self._log_items = []

    self._manufacturer_dialog = None
    self._car_dialog = None
    self._branch_dialog = None

    self._build_toggle_items()
    self._build_function_items()
    self._build_log_items()

    self._content_scroller = None
    self._update_content_scroller()

  def _on_select_manufacturer(self):
    manufacturers = ["[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"]
    current_selection = self._params.get("SelectedManufacturer")

    def handle_manufacturer_selection(result: DialogResult):
      if result != DialogResult.CONFIRM or not self._manufacturer_dialog:
        self._manufacturer_dialog = None
        return

      selected_manufacturer = self._manufacturer_dialog.selection

      if selected_manufacturer == "[ Not Selected ]":
        self._params.remove("SelectedManufacturer")
        self._params.remove("SelectedCar")
      else:
        car_list_file = ""
        if selected_manufacturer == "HYUNDAI":
          car_list_file = "/data/params/crwusiz/CarList_Hyundai"
        elif selected_manufacturer == "KIA":
          car_list_file = "/data/params/crwusiz/CarList_Kia"
        elif selected_manufacturer == "GENESIS":
          car_list_file = "/data/params/crwusiz/CarList_Genesis"

        if car_list_file and Path(car_list_file).exists():
          try:
            shutil.copy2(car_list_file, "/data/params/crwusiz/CarList")
            print(f"Copied {car_list_file} to CarList")
          except Exception as e:
            print(f"Error copying car list: {e}")

        self._params.put("SelectedManufacturer", str(selected_manufacturer), block=True)
        print(f"Saved manufacturer: {selected_manufacturer}")

      self._manufacturer_dialog = None

    self._manufacturer_dialog = MultiOptionDialog(
        tr("Manufacturer"),
        manufacturers,
        current=(current_selection if current_selection else manufacturers[0]),
        callback=handle_manufacturer_selection
    )
    gui_app.push_widget(self._manufacturer_dialog)

  def _on_select_car(self):
    cars = ["[ Not Selected ]"] + get_list("/data/params/crwusiz/CarList")

    if len(cars) == 1:
      dlg = ConfirmDialog(
        tr("Please select manufacturer first"),
        tr("OK")
      )
      gui_app.push_widget(dlg)
      return

    current_selection = self._params.get("SelectedCar")

    def handle_car_selection(result: DialogResult):
      if result != DialogResult.CONFIRM or not self._car_dialog:
        self._car_dialog = None
        return

      selected_car = self._car_dialog.selection

      if selected_car == "[ Not Selected ]":
        self._params.remove("SelectedCar")
        print("Removed car selection")
      else:
        self._params.put("SelectedCar", str(selected_car), block=True)
        print(f"Saved car: {selected_car}")

      self._car_dialog = None

    self._car_dialog = MultiOptionDialog(
      tr("Car"),
      cars,
      current=(current_selection if current_selection else cars[0]),
      callback=handle_car_selection
    )
    gui_app.push_widget(self._car_dialog)

  def _on_select_branch(self):
    branches = ["[ Not Selected ]"] + get_list("/data/params/crwusiz/GitBranchList")

    if len(branches) == 1:
      dlg = ConfirmDialog(
        tr("Branch list not found"),
        tr("OK")
      )
      gui_app.push_widget(dlg)
      return

    current_selection = self._params.get("SelectedBranch")

    def handle_branch_selection(result: DialogResult):
      if result != DialogResult.CONFIRM or not self._branch_dialog:
        self._branch_dialog = None
        return

      selected_branch = self._branch_dialog.selection

      if selected_branch == "[ Not Selected ]":
        self._params.remove("SelectedBranch")
        print("Removed branch selection")
      else:
        self._params.put("SelectedBranch", str(selected_branch), block=True)
        print(f"Saved branch: {selected_branch}")

      self._branch_dialog = None

    self._branch_dialog = MultiOptionDialog(
      tr("Branch"),
      branches,
      current=(current_selection if current_selection else branches[0]),
      callback=handle_branch_selection
    )
    gui_app.push_widget(self._branch_dialog)

  def _draw_button(self, rect, text, is_selected=False, is_header=False):
    if is_header:
      rl.draw_rectangle_rec(rect, rl.Color(44, 44, 226, 255))  # #2C2CE2
    elif is_selected:
      rl.draw_rectangle_rec(rect, rl.Color(60, 60, 60, 255))
    else:
      rl.draw_rectangle_rec(rect, rl.Color(40, 40, 40, 255))

    rl.draw_rectangle_lines_ex(rect, 1, rl.Color(80, 80, 80, 255))

    font = gui_app.font(FontWeight.NORMAL)

    if '\n' in text:
      lines = text.split('\n')
      line_height = FONT_SIZE * 1.2
      total_height = line_height * len(lines)
      start_y = rect.y + (rect.height - total_height) / 2

      for i, line in enumerate(lines):
        text_size = rl.measure_text_ex(font, line, FONT_SIZE, 1)
        text_x = rect.x + (rect.width - text_size.x) / 2
        text_y = start_y + (i * line_height)
        rl.draw_text_ex(font, line, rl.Vector2(text_x, text_y), FONT_SIZE, 1, rl.WHITE)
    else:
      text_size = rl.measure_text_ex(font, text, FONT_SIZE, 1)
      text_x = rect.x + (rect.width - text_size.x) / 2
      text_y = rect.y + (rect.height - text_size.y) / 2
      rl.draw_text_ex(font, text, rl.Vector2(text_x, text_y), FONT_SIZE, 1, rl.WHITE)

  def _is_point_in_rect(self, x, y, rect):
    return (rect.x <= x <= rect.x + rect.width and
            rect.y <= y <= rect.y + rect.height)

  def _build_toggle_items(self):
    self._toggle_items = [
      toggle_item(
        lambda: tr("PcmCruise"),
        description=lambda: DESCRIPTIONS["pcm_cruise"],
        initial_state=self._params.get_bool("PcmCruiseEnable"),
        callback=lambda state: self._params.put_bool("PcmCruiseEnable", state, block=True),
      ),
      toggle_item(
        lambda: tr("Cruise State Controls"),
        description=lambda: DESCRIPTIONS["cruise_state_control"],
        initial_state=self._params.get_bool("CruiseStateControl"),
        callback=lambda state: self._params.put_bool("CruiseStateControl", state, block=True),
      ),
      toggle_item(
        lambda: tr("CANFD Car HDA2"),
        description=lambda: DESCRIPTIONS["is_hda2"],
        initial_state=self._params.get_bool("IsHda2"),
        callback=lambda state: self._params.put_bool("IsHda2", state, block=True),
      ),
      toggle_item(
        lambda: tr("CameraSCC"),
        description=lambda: DESCRIPTIONS["camera_scc"],
        initial_state=self._params.get_bool("CameraSccEnable"),
        callback=lambda state: self._params.put_bool("CameraSccEnable", state, block=True),
      ),
      toggle_item(
        lambda: tr("Enable Radar Track"),
        description=lambda: DESCRIPTIONS["radar_track"],
        initial_state=self._params.get_bool("RadarTrackEnable"),
        callback=lambda state: self._params.put_bool("RadarTrackEnable", state, block=True),
      ),
      toggle_item(
        lambda: tr("Driver Camera On Reverse"),
        description=lambda: DESCRIPTIONS["driver_cam_reverse"],
        initial_state=self._params.get_bool("DriverCameraOnReverse"),
        callback=lambda state: self._params.put_bool("DriverCameraOnReverse", state, block=True),
      ),
      toggle_item(
        lambda: tr("DriverCamera Hardware Missing"),
        description=lambda: DESCRIPTIONS["driver_cam_missing"],
        initial_state=self._params.get_bool("DriverCameraHardwareMissing"),
        callback=lambda state: self._params.put_bool("DriverCameraHardwareMissing", state, block=True),
      ),
      toggle_item(
        lambda: tr("Logger Enable"),
        description=lambda: DESCRIPTIONS["logger_enable"],
        initial_state=self._params.get_bool("LoggerEnable"),
        callback=lambda state: self._params.put_bool("LoggerEnable", state, block=True),
      ),
      toggle_item(
        lambda: tr("Prebuilt Enable"),
        description=lambda: DESCRIPTIONS["prebuilt_enable"],
        initial_state=self._params.get_bool("PrebuiltEnable"),
        callback=lambda state: self._params.put_bool("PrebuiltEnable", state, block=True),
      ),
    ]

  def _build_function_items(self):
    self._function_items = [
      button_item(
        title=lambda: tr("Git Fetch & Reset"),
        button_text=lambda: tr("Run"),
        callback=self._on_git_pull,
      ),
      button_item(
        title=lambda: tr("Git Checkout"),
        button_text=lambda: tr("Run"),
        callback=self._on_git_checkout,
      ),
      button_item(
        title=lambda: tr("Git Reset -1"),
        button_text=lambda: tr("Run"),
        callback=self._on_git_reset,
      ),
      button_item(
        title=lambda: tr("Scons Build"),
        button_text=lambda: tr("Run"),
        callback=self._on_scons_rebuild,
      ),
      button_item(
        title=lambda: tr("Panda Flash"),
        button_text=lambda: tr("Run"),
        callback=self._on_panda_flash,
      ),
      button_item(
        title=lambda: tr("Panda Recover"),
        button_text=lambda: tr("Run"),
        callback=self._on_panda_recover,
      ),
      button_item(
        title=lambda: tr("Camera View"),
        button_text=lambda: tr("Run"),
        callback=self._on_camera_view,
      ),
      button_item(
        title=lambda: tr("Clear DTC"),
        button_text=lambda: tr("Run"),
        callback=self._on_clear_dtc,
      ),
    ]

  def _build_log_items(self):
    self._log_items = [
      button_item(
        title=lambda: tr("CAN missing Log"),
        button_text=lambda: tr("View"),
        callback=lambda: self._view_log("/data/can_missing.log", "CAN Missing Log"),
      ),
      button_item(
        title=lambda: tr("CAN timeout Log"),
        button_text=lambda: tr("View"),
        callback=lambda: self._view_log("/data/can_timeout.log", "CAN Timeout Log"),
      ),
      button_item(
        title=lambda: tr("Tmux log"),
        button_text=lambda: tr("View"),
        callback=lambda: self._view_log("/data/tmux_error.log", "Tmux Error Log"),
      ),
      button_item(
        title=lambda: tr("Tmux log"),
        button_text=lambda: tr("Upload"),
        callback=lambda: self._upload_log("/data/tmux_error.log", "tmux_error.log"),
      ),
      button_item(
        title=lambda: tr("Tmux Console"),
        button_text=lambda: tr("View"),
        callback=self._on_tmux_console_view,
      ),
      button_item(
        title=lambda: tr("Tmux Console"),
        button_text=lambda: tr("Upload"),
        callback=self._on_tmux_console_upload,
      ),
      button_item(
        title=lambda: tr("CarParams dump"),
        button_text=lambda: tr("Upload"),
        callback=self._on_carparams_dump,
      ),
      button_item(
        title=lambda: tr("Realdata Route"),
        button_text=lambda: tr("Upload"),
        callback=self._on_realdata_upload,
      ),
    ]

  def _update_content_scroller(self):
    items = []

    if self._current_tab == 0:
      items = self._toggle_items
    elif self._current_tab == 1:
      items = self._function_items
    elif self._current_tab == 2:
      items = self._log_items

    self._content_scroller = Scroller(items, line_separator=True, spacing=0)

  def _render(self, rect):
    import re
    self._rect = rect

    col_width = rect.width / 3

    self._manufacturer_rect = rl.Rectangle(rect.x, rect.y, col_width, HEADER_ROW_HEIGHT)
    manufacturer_text = self._params.get("SelectedManufacturer")
    if not manufacturer_text:
      manufacturer_text = tr("Manufacturer")
    self._draw_button(self._manufacturer_rect, manufacturer_text, is_header=True)

    self._car_rect = rl.Rectangle(rect.x + col_width, rect.y, col_width, HEADER_ROW_HEIGHT)
    car_text = self._params.get("SelectedCar")

    if car_text is None:
      car_text = ""

    if isinstance(car_text, bytes):
      car_text = car_text.decode("utf-8")

    if car_text:
      first_space_index = car_text.find(' ')
      if first_space_index != -1:
        car_text = car_text[first_space_index + 1:].strip()

    match = re.match(r'^(.+?)(\s*\([^)]+\))$', car_text)
    if match:
      car_text = match.group(1).strip() + "\n" + match.group(2).strip()

    if not car_text:
      car_text = tr("Car")
    self._draw_button(self._car_rect, car_text, is_header=True)

    self._branch_rect = rl.Rectangle(rect.x + col_width * 2, rect.y, col_width, HEADER_ROW_HEIGHT)
    branch_text = self._params.get("SelectedBranch")
    if not branch_text:
      branch_text = tr("Branch")
    self._draw_button(self._branch_rect, branch_text, is_header=True)

    rl.draw_line(
      int(rect.x + 40), int(rect.y + HEADER_ROW_HEIGHT),
      int(rect.x + rect.width - 80), int(rect.y + HEADER_ROW_HEIGHT),
      rl.GRAY
    )

    tab_y = rect.y + HEADER_ROW_HEIGHT

    self._toggle_rect = rl.Rectangle(rect.x, tab_y, col_width, HEADER_ROW_HEIGHT)
    self._draw_button(self._toggle_rect, tr("Toggle"), self._current_tab == 0)

    self._function_rect = rl.Rectangle(rect.x + col_width, tab_y, col_width, HEADER_ROW_HEIGHT)
    self._draw_button(self._function_rect, tr("Function"), self._current_tab == 1)

    self._log_rect = rl.Rectangle(rect.x + col_width * 2, tab_y, col_width, HEADER_ROW_HEIGHT)
    self._draw_button(self._log_rect, tr("Log"), self._current_tab == 2)

    rl.draw_line(
      int(rect.x + 40), int(tab_y + HEADER_ROW_HEIGHT),
      int(rect.x + rect.width - 80), int(tab_y + HEADER_ROW_HEIGHT),
      rl.GRAY
    )

    content_rect = rl.Rectangle(
      rect.x,
      rect.y + FIXED_HEADER_HEIGHT,
      rect.width,
      rect.height - FIXED_HEADER_HEIGHT
    )
    self._content_scroller._rect = content_rect
    self._content_scroller.render(content_rect)

  def _handle_mouse_release(self, pos):
    x, y = pos

    if self._is_point_in_rect(x, y, self._manufacturer_rect):
      self._on_select_manufacturer()
      return True
    elif self._is_point_in_rect(x, y, self._car_rect):
      self._on_select_car()
      return True
    elif self._is_point_in_rect(x, y, self._branch_rect):
      self._on_select_branch()
      return True

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
    super().show_event()
    if self._content_scroller:
      self._content_scroller.show_event()

  def _switch_tab(self, tab_index: int):
    if self._current_tab != tab_index:
      self._current_tab = tab_index
      self._update_content_scroller()

  def _on_git_pull(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/gitpull.sh")
        if Path("/data/check_network.log").exists():
          dlg = ConfirmDialog(tr("Please Check Network Connection"), tr("OK"))
          gui_app.push_widget(dlg)

    dlg = ConfirmDialog(tr("Git Fetch and Reset\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_git_checkout(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/checkout.sh")

    dlg = ConfirmDialog(tr("Git Checkout\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_git_reset(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/reset.sh")

    dlg = ConfirmDialog(tr("Git Reset\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_scons_rebuild(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/scons_rebuild.sh")

    dlg = ConfirmDialog(tr("Scons Rebuild\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_panda_flash(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/panda/board/flash.py")

    dlg = ConfirmDialog(tr("Panda Flash\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_panda_recover(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/panda/board/recover.py")

    dlg = ConfirmDialog(tr("Panda Recover\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_camera_view(self):
    execute_script("/data/openpilot/selfdrive/ui/watch3.py")

  def _on_clear_dtc(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/cleardtc.sh")

    dlg = ConfirmDialog(tr("Clear DTC\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _view_log(self, log_path: str, title: str = "Log View"):
    if Path(log_path).exists():
      try:
        with open(log_path, 'r', encoding='utf-8') as f:
          content = f.read()
        formatted_content = content.replace(chr(10), '<br>')

        dlg = StaticLogModal(text=formatted_content)
        gui_app.push_widget(dlg)
      except Exception as e:
        dlg = ConfirmDialog(tr("Error reading log file"), tr("OK"))
        gui_app.push_widget(dlg)
    else:
      dlg = ConfirmDialog(tr("log file not found") + f"\n{log_path}", tr("OK"))
      gui_app.push_widget(dlg)

  def _upload_log(self, log_path: str, log_name: str):
    if Path(log_path).exists():
      def confirm_callback(result: DialogResult):
        if result == DialogResult.CONFIRM:
          def upload_task():
            exit_code = execute_script("/data/openpilot/scripts/log_upload.sh", log_name)

            if exit_code == 0:
              dlg = ConfirmDialog(tr("Upload completed successfully"), tr("OK"))
            else:
              dlg = ConfirmDialog(tr("Upload failed"), tr("OK"))
            gui_app.push_widget(dlg)

          t = threading.Thread(target=upload_task)
          t.start()

      dlg = ConfirmDialog(tr(f"{log_name} upload\n\nProcess?"), tr("Process"), callback=confirm_callback)
      gui_app.push_widget(dlg)
    else:
      dlg = ConfirmDialog(tr("log file not found"), tr("OK"))
      gui_app.push_widget(dlg)

  def _on_tmux_console_view(self):
    dlg = TmuxLogModal()
    gui_app.push_widget(dlg)

  def _on_tmux_console_upload(self):
    try:
      cmd = ["sh", "-c", "tmux capture-pane -p -t 0 -S -500 > /data/tmux_console.log"]
      result = subprocess.run(cmd, capture_output=True)

      if result.returncode == 0:
        def confirm_callback(result: DialogResult):
          if result == DialogResult.CONFIRM:
            def upload_task():
              execute_script("/data/openpilot/scripts/log_upload.sh", "tmux_console.log")

            threading.Thread(target=upload_task).start()

        dlg = ConfirmDialog(tr("tmux console log upload\n\nProcess?"), tr("Process"), callback=confirm_callback)
        gui_app.push_widget(dlg)
      else:
        dlg = ConfirmDialog(tr("log file not found"), tr("OK"))
        gui_app.push_widget(dlg)
    except Exception as e:
      dlg = ConfirmDialog(tr("Error creating console log"), tr("OK"))
      gui_app.push_widget(dlg)

  def _on_carparams_dump(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/dump_upload.sh", "carParams")

    dlg = ConfirmDialog(tr("carParams dump upload\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_realdata_upload(self):
    target_path = Path("/data/media/0/realdata")

    if not target_path.exists():
      dlg = ConfirmDialog(tr("Path does not exist"), tr("OK"))
      gui_app.push_widget(dlg)
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

    for r_info in route_map.values():
      r_info['segment_paths'].sort(key=lambda x: int(x.split("--")[-1]))

    if not route_map:
      dlg = ConfirmDialog(tr("Routes do not exist"), tr("OK"))
      gui_app.push_widget(dlg)
      return

    sorted_routes = sorted(
      route_map.values(),
      key=lambda x: x['last_modified'],
      reverse=True
    )

    options = []
    for route in sorted_routes:
      dt_object = datetime.fromtimestamp(route['last_modified'])
      formatted_date = dt_object.strftime('%Y-%m-%d %H:%M')
      options.append(f"[{formatted_date}] {route['route_name']} ({route['segment_count']} segments)")

    def handle_route_selection(result: DialogResult):
      if result != DialogResult.CONFIRM:
        return

      selected_text = dialog.selection
      if selected_text not in options:
        return

      selected_index = options.index(selected_text)
      selected_route_info = sorted_routes[selected_index]
      route_name = selected_route_info['route_name']
      segment_paths = selected_route_info['segment_paths']

      def handle_final_confirm(res: DialogResult):
        if res != DialogResult.CONFIRM:
          return

        script_path = "/data/openpilot/scripts/realdata_upload.sh"
        cmd = [script_path] + segment_paths

        def upload_thread_task():
          try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode == 0:
              dlg = ConfirmDialog(tr("Upload completed successfully"), tr("OK"))
            else:
              dlg = ConfirmDialog(tr("Upload failed") + f"\nExit Code: {result.returncode}", tr("OK"))

          except Exception as e:
             dlg = ConfirmDialog(tr("Error executing script:") + f"\n{e}", tr("OK"))

          gui_app.push_widget(dlg)

        t = threading.Thread(target=upload_thread_task)
        t.start()

      upload_dlg = ConfirmDialog(tr(f"Upload route {route_name}?"), tr("Yes"), tr("No"), callback=handle_final_confirm)
      gui_app.push_widget(upload_dlg)

    dialog = MultiOptionDialog(tr("Select Route to Upload"), options, current=options[0], callback=handle_route_selection)
    gui_app.push_widget(dialog)
