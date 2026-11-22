import raylib_nb as rl
from openpilot.cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive import hud_render_support
from openpilot.selfdrive.ui.mici.onroad import RIGHT_ICON_CENTER_OFFSET
from openpilot.selfdrive.ui.mici.onroad.status import status_fade
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget

class LongIndicator(Widget):
  def __init__(self, scale: float = 1.0):
    super().__init__()
    self._icons = [(self._texture(name, round(width * scale), round(height * scale)), y * scale)
                   for name, (width, height), y in hud_render_support.LONG_INDICATOR_ICONS]
    self._indicator_state = hud_render_support.LongIndicatorState(1 / gui_app.target_fps, FirstOrderFilter)
    self._should_draw = False

  @staticmethod
  def _texture(name: str, width: int, height: int) -> rl.Texture:
    return gui_app.texture(f'{hud_render_support.LONG_INDICATOR_ASSET_DIR}/{name}.png', width, height, keep_aspect_ratio=False)

  def set_should_draw(self, should_draw: bool):
    self._should_draw = should_draw

  def _render(self, rect: rl.Rectangle) -> None:
    # offroad shows the disengaged state
    if (ui_state.cluster_connected or not ui_state.has_longitudinal_control
        or (ui_state.started and not ui_state.received_since_started('selfdriveState'))):
      self._indicator_state.reset()
      return

    personality = ui_state.sm['selfdriveState'].personality.raw if ui_state.started else ui_state.personality_param
    self.draw(rect.x + rect.width - RIGHT_ICON_CENTER_OFFSET, rect.y + rect.height / 2, personality + 1,
              disengaged=status_fade.weight(UIStatus.DISENGAGED), visible=self._should_draw)

  def draw(self, center_x: float, center_y: float, distance_level: int, *, disengaged: float, visible: bool = True) -> None:
    sm = ui_state.sm
    plan = sm['longitudinalPlan']
    has_lead = sm.alive['longitudinalPlan'] and plan.hasLead
    e2e = has_lead and plan.longitudinalPlanSource == log.LongitudinalPlan.LongitudinalPlanSource.e2e
    alphas = self._indicator_state.update(rl.get_time(), distance_level=distance_level, has_lead=has_lead,
                                          e2e_lead=e2e, disengaged=disengaged, visible=visible)
    for (texture, y), alpha in zip(self._icons, alphas, strict=True):
      self._draw_centered(texture, center_x, center_y + y, alpha)

  @staticmethod
  def _draw_centered(texture: rl.Texture, center_x: float, center_y: float, alpha: float) -> None:
    pos = rl.Vector2(center_x - texture.width / 2, center_y - texture.height / 2)
    rl.draw_texture_ex(texture, pos, 0.0, 1.0, rl.Color(255, 255, 255, round(255 * alpha)))
