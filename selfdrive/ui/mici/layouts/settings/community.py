import subprocess
import pyray as rl
from collections.abc import Callable

from openpilot.common.params import Params
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.selfdrive.ui.mici.widgets.button import BigParamControl, BigMultiParamToggle, BigButton
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.widgets.nav_widget import NavWidget
from openpilot.selfdrive.ui.layouts.settings.common import restart_needed_callback
from openpilot.selfdrive.ui.ui_state import ui_state

from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.lib.multilang import tr

def execute_script(script_path: str, *args) -> int:
  try:
    cmd = [script_path] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode
  except Exception as e:
    print(f"Error executing script: {e}")
    return 1

class CommunityLayoutMici(NavWidget):
  def __init__(self, back_callback: Callable):
    super().__init__()
    self.set_back_callback(back_callback)

    pcm_cruise = BigParamControl("Pcm Cruise", "PcmCruiseEnable", toggle_callback=restart_needed_callback)
    cruise_state_control = BigParamControl("Cruise State Controls", "CruiseStateControl", toggle_callback=restart_needed_callback)
    is_hda2 = BigParamControl("CANFD Car HDA2", "IsHda2", toggle_callback=restart_needed_callback)
    camera_scc = BigParamControl("CameraSCC (LongControl)", "CameraSccEnable", toggle_callback=restart_needed_callback)
    radar_track = BigParamControl("Radar Track", "RadarTrackEnable", toggle_callback=restart_needed_callback)
    driver_cam_reverse = BigParamControl("Driver Camera On Reverse Gear", "DriverCameraOnReverse", toggle_callback=restart_needed_callback)
    driver_cam_missing = BigParamControl("Driver Camera Hardware Missing", "DriverCameraHardwareMissing", toggle_callback=restart_needed_callback)

    logger_enable = BigParamControl("Logger Enable", "LoggerEnable", toggle_callback=restart_needed_callback)
    prebuilt_enable = BigParamControl("Prebuilt Enable", "PrebuiltEnable", toggle_callback=restart_needed_callback)

    btn_git_pull = BigButton("Git Fetch & Reset", "Run")
    btn_git_pull.set_click_callback(self._on_git_pull)

    btn_git_checkout = BigButton("Git Checkout", "Run")
    btn_git_checkout.set_click_callback(self._on_git_checkout)

    btn_scons = BigButton("Scons Build", "Run")
    btn_scons.set_click_callback(self._on_scons_rebuild)

    btn_panda_flash = BigButton("Panda Flash", "Run")
    btn_panda_flash.set_click_callback(self._on_panda_flash)

    btn_clear_dtc = BigButton("Clear DTC", "Run")
    btn_clear_dtc.set_click_callback(self._on_clear_dtc)

    self._scroller = Scroller([
      pcm_cruise,
      cruise_state_control,
      is_hda2,
      camera_scc,
      radar_track,
      driver_cam_reverse,
      driver_cam_missing,
      logger_enable,
      prebuilt_enable,
      btn_git_pull,
      btn_git_checkout,
      btn_scons,
      btn_panda_flash,
      btn_clear_dtc,
    ])

    # Toggle lists
    self._refresh_toggles = (
      ("PcmCruiseEnable", pcm_cruise),
      ("CruiseStateControl", cruise_state_control),
      ("IsHda2", is_hda2),
      ("CameraSccEnable", camera_scc),
      ("RadarTrackEnable", radar_track),
      ("DriverCameraOnReverse", driver_cam_reverse),
      ("DriverCameraHardwareMissing", driver_cam_missing),
      ("LoggerEnable", logger_enable),
      ("PrebuiltEnable", prebuilt_enable),
    )

    if ui_state.params.get_bool("ShowDebugInfo"):
      gui_app.set_show_touches(True)
      gui_app.set_show_fps(True)

    ui_state.add_engaged_transition_callback(self._update_toggles)

  def _update_state(self):
    super()._update_state()

  def show_event(self):
    super().show_event()
    self._scroller.show_event()
    self._update_toggles()

  def hide_event(self):
    super().hide_event()
    self._scroller.hide_event()

  def _update_toggles(self):
    ui_state.update_params()

    # Refresh toggles from params to mirror external changes
    for key, item in self._refresh_toggles:
      item.set_checked(ui_state.params.get_bool(key))

  def _render(self, rect: rl.Rectangle):
    self._scroller.render(rect)

  def _on_git_pull(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/gitpull.sh")
    dlg = ConfirmDialog(tr("Git Fetch and Reset\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)

  def _on_git_checkout(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/checkout.sh")
    dlg = ConfirmDialog(tr("Git Checkout\n\nProcess?"), tr("Process"), callback=confirm_callback)
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

  def _on_clear_dtc(self):
    def confirm_callback(result: DialogResult):
      if result == DialogResult.CONFIRM:
        execute_script("/data/openpilot/scripts/cleardtc.sh")
    dlg = ConfirmDialog(tr("Clear DTC\n\nProcess?"), tr("Process"), callback=confirm_callback)
    gui_app.push_widget(dlg)
