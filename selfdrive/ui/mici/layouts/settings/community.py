import pyray as rl
from collections.abc import Callable

from openpilot.common.params import Params
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.selfdrive.ui.mici.widgets.button import BigParamControl, BigMultiParamToggle
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.widgets.nav_widget import NavWidget
from openpilot.selfdrive.ui.layouts.settings.common import restart_needed_callback
from openpilot.selfdrive.ui.ui_state import ui_state


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

    self._scroller = Scroller([
      pcm_cruise,
      cruise_state_control,
      is_hda2,
      camera_scc,
      radar_track,
      driver_cam_reverse,
      driver_cam_missing,
    ], snap_items=False)

    # Toggle lists
    self._refresh_toggles = (
      ("PcmCruiseEnable", pcm_cruise),
      ("CruiseStateControl", cruise_state_control),
      ("IsHda2", is_hda2),
      ("CameraSccEnable", camera_scc),
      ("RecordFront", radar_track),
      ("DriverCameraOnReverse", driver_cam_reverse),
      ("DriverCameraHardwareMissing", driver_cam_missing),
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

  def _update_toggles(self):
    ui_state.update_params()

    # Refresh toggles from params to mirror external changes
    for key, item in self._refresh_toggles:
      item.set_checked(ui_state.params.get_bool(key))

  def _render(self, rect: rl.Rectangle):
    self._scroller.render(rect)
