#!/usr/bin/env python3
import pyray as rl

from msgq.visionipc import VisionStreamType
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.selfdrive.ui.onroad.cameraview import CameraView


if __name__ == "__main__":
  gui_app.init_window("watch3")
  road = CameraView("camerad", VisionStreamType.VISION_STREAM_ROAD)
  driver = CameraView("camerad", VisionStreamType.VISION_STREAM_DRIVER)
  wide = CameraView("camerad", VisionStreamType.VISION_STREAM_WIDE_ROAD)

  font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
  font_bold: rl.Font = gui_app.font(FontWeight.BOLD)

  for _ in gui_app.render():
    section_height = gui_app.height // 5

    close_button_size = 200
    close_button_margin = 20
    close_button_rect = rl.Rectangle(
      gui_app.width - close_button_size - close_button_margin,
      close_button_margin,
      close_button_size,
      close_button_size
    )

    if rl.is_mouse_button_pressed(rl.MOUSE_BUTTON_LEFT):
      touch_pos = rl.get_mouse_position()
      if rl.check_collision_point_rec(touch_pos, close_button_rect):
        break

    if rl.get_touch_point_count() > 0:
      touch_pos = rl.get_touch_position(0)
      if rl.check_collision_point_rec(touch_pos, close_button_rect):
        break

    title_text = "CAMERA PREVIEW"
    title_font_size = 100
    text_width = rl.measure_text_ex(font_bold, title_text, title_font_size, 0).x
    rl.draw_text_ex(
      font_bold,
      title_text,
      rl.Vector2((gui_app.width - text_width) / 2, section_height / 2 - title_font_size / 2),
      title_font_size,
      0,
      rl.WHITE
    )

    camera_start_y = section_height * 2
    camera_height = section_height * 3
    camera_width = gui_app.width // 3

    label_font_size = 50
    label_padding = 15

    # VISION_STREAM_ROAD
    road_rect = rl.Rectangle(0, camera_start_y, camera_width, camera_height)
    road.render(road_rect)
    rl.draw_rectangle_lines(0, camera_start_y, camera_width, camera_height, rl.WHITE)
    rl.draw_text_ex(
      font_semi_bold,
      "ROAD",
      rl.Vector2(label_padding, camera_start_y + label_padding),
      label_font_size,
      0,
      rl.WHITE
    )

    # VISION_STREAM_DRIVER
    driver_rect = rl.Rectangle(camera_width, camera_start_y, camera_width, camera_height)
    driver.render(driver_rect)
    rl.draw_rectangle_lines(camera_width, camera_start_y, camera_width, camera_height, rl.WHITE)
    rl.draw_text_ex(
      font_semi_bold,
      "DRIVER",
      rl.Vector2(camera_width + label_padding, camera_start_y + label_padding),
      label_font_size,
      0,
      rl.WHITE
    )

    # VISION_STREAM_WIDE_ROAD
    wide_rect = rl.Rectangle(camera_width * 2, camera_start_y, camera_width, camera_height)
    wide.render(wide_rect)
    rl.draw_rectangle_lines(camera_width * 2, camera_start_y, camera_width, camera_height, rl.WHITE)
    rl.draw_text_ex(
      font_semi_bold,
      "WIDE",
      rl.Vector2(camera_width * 2 + label_padding, camera_start_y + label_padding),
      label_font_size,
      0,
      rl.WHITE
    )

    rl.draw_rectangle_rounded(close_button_rect, 0.3, 10, rl.Color(80, 80, 80, 200))
    rl.draw_rectangle_rounded_lines_ex(close_button_rect, 0.3, 10, 2, rl.WHITE)

    button_center_x = close_button_rect.x + close_button_size / 2
    button_center_y = close_button_rect.y + close_button_size / 2
    x_size = 100
    rl.draw_line_ex(
      rl.Vector2(button_center_x - x_size / 2, button_center_y - x_size / 2),
      rl.Vector2(button_center_x + x_size / 2, button_center_y + x_size / 2),
      3,
      rl.WHITE
    )
    rl.draw_line_ex(
      rl.Vector2(button_center_x + x_size / 2, button_center_y - x_size / 2),
      rl.Vector2(button_center_x - x_size / 2, button_center_y + x_size / 2),
      3,
      rl.WHITE
    )
