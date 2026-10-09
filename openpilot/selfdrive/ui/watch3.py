#!/usr/bin/env python3
import pyray as rl
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.selfdrive.ui.onroad.cameraview import CameraView

import time
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.ui import Colors


def _wait_for_main_ui_exit(timeout: float = 10.0) -> None:
  sm = messaging.SubMaster(["managerState"])
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    sm.update(100)
    if sm.updated["managerState"]:
      ui_process = next((p for p in sm["managerState"].processes if p.name == "ui"), None)
      if ui_process is None or not ui_process.running:
        return


def _camera_layout(width: float, height: float) -> tuple[float, list[rl.Rectangle]]:
  scale = min(width / 2160, height / 1080)
  margin, gap = 40 * scale, 24 * scale
  top = 164 * scale
  content_width, content_height = width - 2 * margin, height - top - margin

  if width >= height:
    road_width = (content_width - gap) * 0.62
    side_width = content_width - road_width - gap
    side_height = (content_height - gap) / 2
    side_x = margin + road_width + gap
    rects = [
      rl.Rectangle(margin, top, road_width, content_height),
      rl.Rectangle(side_x, top, side_width, side_height),
      rl.Rectangle(side_x, top + side_height + gap, side_width, side_height),
    ]
  else:
    road_height = (content_height - gap) * 0.6
    side_width = (content_width - gap) / 2
    side_height = content_height - road_height - gap
    side_y = top + road_height + gap
    rects = [
      rl.Rectangle(margin, top, content_width, road_height),
      rl.Rectangle(margin, side_y, side_width, side_height),
      rl.Rectangle(margin + side_width + gap, side_y, side_width, side_height),
    ]
  return scale, rects


def _draw_camera_card(camera: CameraView, rect: rl.Rectangle, title: str, index: int, scale: float):
  colors = Colors.Watch3
  padding, header_height = 20 * scale, 68 * scale
  font = gui_app.font(FontWeight.SEMI_BOLD)
  rl.draw_rectangle_rounded(rect, 0.06, 12, colors.PANEL)
  rl.draw_rectangle_rounded_lines_ex(rect, 0.06, 12, max(1, scale), colors.BORDER)
  rl.draw_text_ex(font, f"{index:02d}", rl.Vector2(rect.x + padding, rect.y + 20 * scale), 26 * scale, 0, colors.ACCENT)
  rl.draw_text_ex(font, title, rl.Vector2(rect.x + padding + 54 * scale, rect.y + 18 * scale), 30 * scale, 0, colors.TEXT)

  video_rect = rl.Rectangle(rect.x + padding, rect.y + header_height,
                            rect.width - 2 * padding, rect.height - header_height - padding)
  rl.draw_rectangle_rec(video_rect, colors.VIDEO)
  camera.render(video_rect)
  if camera.frame is None:
    text = "Waiting for camera..."
    font_size = 26 * scale
    text_width = rl.measure_text_ex(font, text, font_size, 0).x
    position = rl.Vector2(video_rect.x + (video_rect.width - text_width) / 2,
                          video_rect.y + (video_rect.height - font_size) / 2)
    rl.draw_text_ex(font, text, position, font_size, 0, colors.TEXT_MUTED)


def main():
  params = Params()
  try:
    _wait_for_main_ui_exit()
    gui_app.init_window("watch3")
    road = CameraView("camerad", VisionStreamType.VISION_STREAM_NARROW_ROAD)
    driver = CameraView("camerad", VisionStreamType.VISION_STREAM_CABIN)
    wide = CameraView("camerad", VisionStreamType.VISION_STREAM_WIDE_ROAD)

    font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)
    colors = Colors.Watch3
    cameras = ((road, "ROAD"), (driver, "DRIVER"), (wide, "WIDE"))

    for _ in gui_app.render():
      scale, camera_rects = _camera_layout(gui_app.width, gui_app.height)
      margin = 40 * scale
      close_button_rect = rl.Rectangle(gui_app.width - margin - 180 * scale, margin, 180 * scale, 88 * scale)

      if rl.is_mouse_button_pressed(rl.MOUSE_BUTTON_LEFT):
        touch_pos = rl.get_mouse_position()
        if rl.check_collision_point_rec(touch_pos, close_button_rect):
          break

      if rl.get_touch_point_count() > 0:
        touch_pos = rl.get_touch_position(0)
        if rl.check_collision_point_rec(touch_pos, close_button_rect):
          break

      rl.clear_background(colors.BACKGROUND)
      rl.draw_text_ex(font_medium, "CAMERAS / 03", rl.Vector2(margin, margin), 24 * scale, 2 * scale, colors.ACCENT)
      rl.draw_text_ex(font_bold, "Camera preview", rl.Vector2(margin, margin + 36 * scale), 52 * scale, 0, colors.TEXT)

      for index, ((camera, title), rect) in enumerate(zip(cameras, camera_rects), start=1):
        _draw_camera_card(camera, rect, title, index, scale)

      hovered = rl.check_collision_point_rec(rl.get_mouse_position(), close_button_rect)
      rl.draw_rectangle_rounded(close_button_rect, 0.25, 12, colors.CLOSE_HOVER if hovered else colors.PANEL)
      rl.draw_rectangle_rounded_lines_ex(close_button_rect, 0.25, 12, max(1, scale), colors.BORDER)
      rl.draw_text_ex(font_medium, "Close", rl.Vector2(close_button_rect.x + 28 * scale, close_button_rect.y + 28 * scale),
                      30 * scale, 0, colors.TEXT)
      center = rl.Vector2(close_button_rect.x + 140 * scale, close_button_rect.y + close_button_rect.height / 2)
      offset = 10 * scale
      rl.draw_line_ex(rl.Vector2(center.x - offset, center.y - offset), rl.Vector2(center.x + offset, center.y + offset),
                      max(2, 3 * scale), colors.TEXT)
      rl.draw_line_ex(rl.Vector2(center.x + offset, center.y - offset), rl.Vector2(center.x - offset, center.y + offset),
                      max(2, 3 * scale), colors.TEXT)
  finally:
    # Release the display before asking manager to restart the main UI.
    gui_app.close()
    params.put_bool("CameraPreview", False, block=True)


if __name__ == "__main__":
  main()
