from typing import List, Optional
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import button_item
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.system.ui.widgets.dialog_template import DialogTemplate
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import DialogResult


class SelectDialog(Widget):
  """
  A modal dialog widget for selecting one option from a list.

  Usage:
    dialog = SelectDialog("Choose option:", ["A", "B", "C"], "A")
    gui_app.set_modal_overlay(dialog)
    selected_option = dialog.result
  """

  def __init__(self, title: str, options: List[str], current_selection: Optional[str] = None):
    super().__init__()
    self.result = DialogResult.CANCEL  # 초기 결과는 취소

    # 선택 가능한 옵션들을 버튼 리스트 아이템으로 변환
    items = []
    for option in options:
      # 현재 선택된 옵션은 버튼 텍스트에 표시
      button_text = option
      if option == current_selection:
        button_text += " (Selected)"

      # 버튼을 누르면 _on_select가 호출되며, 결과를 설정하고 다이얼로그를 닫습니다.
      item = button_item(
        title=option,
        button_text=button_text,
        callback=lambda opt=option: self._on_select(opt),
      )
      items.append(item)

    self._scroller = Scroller(items)
    self._template = DialogTemplate(
      title=tr(title),
      content_widget=self._scroller,
      cancel_callback=self._on_cancel,
    )

  def _on_select(self, option: str):
    """Option selected: save result and close dialog."""
    self.result = option
    gui_app.set_modal_overlay(None)

  def _on_cancel(self):
    """Cancel button pressed: set result to CANCEL and close dialog."""
    self.result = DialogResult.CANCEL
    gui_app.set_modal_overlay(None)

  def _render(self, rect):
    """Render the dialog template."""
    self._template.render(rect)

  def size_hint(self):
    return self._template.size_hint()
