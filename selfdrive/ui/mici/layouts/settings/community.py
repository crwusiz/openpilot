import subprocess

from openpilot.common.params import Params
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigParamControl
from openpilot.system.ui.lib.application import gui_app
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

class LanguageToggleControl(BigParamControl):
  def __init__(self, name, param, toggle_callback=None):
    super().__init__(name, param, toggle_callback=toggle_callback)

    p = Params()
    val_raw = p.get("LanguageSetting")
    lang = ""

    if isinstance(val_raw, bytes):
      lang = val_raw.decode('utf-8').strip()
    elif isinstance(val_raw, str):
      lang = val_raw.strip()

    self.is_ko = (lang == "ko")
    self._checked = self.is_ko

  def set_checked(self, checked):
    self.is_ko = checked
    self._checked = checked

  def render(self, rect):

    self._checked = self.is_ko
    return super().render(rect)

class CommunityLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()
    pcm_cruise = BigParamControl("Pcm Cruise", "PcmCruiseEnable", toggle_callback=restart_needed_callback)
    cruise_state_control = BigParamControl("Cruise State Controls", "CruiseStateControl", toggle_callback=restart_needed_callback)
    is_hda2 = BigParamControl("CANFD Car HDA2", "IsHda2", toggle_callback=restart_needed_callback)
    camera_scc = BigParamControl("CameraSCC (LongControl)", "CameraSccEnable", toggle_callback=restart_needed_callback)
    radar_track = BigParamControl("Radar Track", "RadarTrackEnable", toggle_callback=restart_needed_callback)
    driver_cam_reverse = BigParamControl("Driver Camera On Reverse Gear", "DriverCameraOnReverse", toggle_callback=restart_needed_callback)
    driver_cam_missing = BigParamControl("Driver Camera Hardware Missing", "DriverCameraHardwareMissing", toggle_callback=restart_needed_callback)
    logger_enable = BigParamControl("Logger Enable", "LoggerEnable", toggle_callback=restart_needed_callback)
    prebuilt_enable = BigParamControl("Prebuilt Enable", "PrebuiltEnable", toggle_callback=restart_needed_callback)

    language_toggle = LanguageToggleControl("Language (en/ko)", "LanguageSetting", toggle_callback=self._language_callback)

    self._scroller.add_widgets([
      pcm_cruise,
      cruise_state_control,
      is_hda2,
      camera_scc,
      radar_track,
      driver_cam_reverse,
      driver_cam_missing,
      logger_enable,
      prebuilt_enable,
      language_toggle,
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

    self._language_toggle = language_toggle

    if ui_state.params.get_bool("ShowDebugInfo"):
      gui_app.set_show_touches(True)
      gui_app.set_show_fps(True)

    ui_state.add_engaged_transition_callback(self._update_toggles)

  def show_event(self):
    super().show_event()
    self._update_toggles()

  def _language_callback(self, *args, **kwargs):
    p = Params()
    toggle_state = args[0] if args else False

    if toggle_state:
      p.put("LanguageSetting", "ko")
    else:
      p.put("LanguageSetting", "en")

    self._language_toggle.set_checked(toggle_state)

    restart_needed_callback(toggle_state)

  def _update_toggles(self):
    ui_state.update_params()

    # Refresh toggles from params to mirror external changes
    for key, item in self._refresh_toggles:
      item.set_checked(ui_state.params.get_bool(key))

    p = Params()
    val_raw = p.get("LanguageSetting")

    if isinstance(val_raw, bytes):
      lang = val_raw.decode('utf-8').strip()
    elif isinstance(val_raw, str):
      lang = val_raw.strip()
    else:
      lang = ""

    self._language_toggle.set_checked(lang == "ko")
