from typing import Optional, Callable

import pyray as rl

from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import button_item
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr

class DialogTemplate(Widget):
  """
  Provides a common template for all modal dialogs (ConfirmDialog, SelectDialog, etc.).
  It handles the title, content widget, and an optional cancel button/callback.
  """

  # 다이얼로그의 기본적인 크기 및 패딩 상수
  PADDING = 50
  TITLE_HEIGHT = 150
  LIST_ITEM_HEIGHT = 120

  def __init__(self, title: str, content_widget: Widget, cancel_callback: Optional[Callable] = None):
    super().__init__()
    self.title = title
    self.content_widget = content_widget
    self.cancel_callback = cancel_callback

    self._cancel_button = None
    if self.cancel_callback:
      # 취소 버튼 정의
      self._cancel_button = button_item(
        title="",
        button_text=tr("Cancel"),
        callback=self.cancel_callback,
      )

  def size_hint(self):
    # 다이얼로그 전체의 권장 크기 (화면의 약 80% 사용)
    screen_w, screen_h = gui_app.get_screen_size()
    w = int(screen_w * 0.8)
    h = int(screen_h * 0.9)
    return w, h

  def _render(self, rect):
    title_rect = rl.Rectangle(
        int(rect.x) + self.PADDING,
        int(rect.y) + self.PADDING,
        int(rect.width) - 2 * self.PADDING,
        self.TITLE_HEIGHT
    )
    content_y = rect.y + self.TITLE_HEIGHT + self.PADDING * 2
    content_h = rect.height - self.TITLE_HEIGHT - self.PADDING * 3

    if self._cancel_button:
      content_h -= self.LIST_ITEM_HEIGHT + self.PADDING

    content_rect = rl.Rectangle(
        int(rect.x) + self.PADDING,
        int(content_y),
        int(rect.width) - 2 * self.PADDING,
        int(content_h)
    )

    self.content_widget.render(content_rect)

    if self._cancel_button:
      cancel_h = self.LIST_ITEM_HEIGHT

      cancel_y = rect.y + rect.height - cancel_h - self.PADDING

      cancel_rect = rl.Rectangle(
          int(rect.x) + self.PADDING,
          int(cancel_y),
          int(rect.width) - 2 * self.PADDING,
          cancel_h
      )
      self._cancel_button.set_rect(cancel_rect)
      self._cancel_button._parent_rect = rect

      self._cancel_button.render()
