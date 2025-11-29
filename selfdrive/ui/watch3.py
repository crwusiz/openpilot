#!/usr/bin/env python3
import pyray as rl

from msgq.visionipc import VisionStreamType
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.onroad.cameraview import CameraView


if __name__ == "__main__":
  gui_app.init_window("watch3")
  road = CameraView("camerad", VisionStreamType.VISION_STREAM_ROAD)
  driver = CameraView("camerad", VisionStreamType.VISION_STREAM_DRIVER)
  wide = CameraView("camerad", VisionStreamType.VISION_STREAM_WIDE_ROAD)

  for _ in gui_app.render():
    section_height = gui_app.height // 5

    title_text = "CAMERA PREVIEW"
    title_font_size = 40
    text_width = rl.measure_text(title_text, title_font_size)
    rl.draw_text(
      title_text,
      (gui_app.width - text_width) // 2,
      section_height // 2 - title_font_size // 2,
      title_font_size,
      rl.WHITE
    )

    camera_start_y = section_height * 2
    camera_height = section_height * 3
    camera_width = gui_app.width // 3

    label_font_size = 24
    label_padding = 10

    # VISION_STREAM_ROAD
    road_rect = rl.Rectangle(0, camera_start_y, camera_width, camera_height)
    road.render(road_rect)
    rl.draw_rectangle_lines(0, camera_start_y, camera_width, camera_height, rl.WHITE)
    rl.draw_text("ROAD", label_padding, camera_start_y + label_padding, label_font_size, rl.WHITE)

    # VISION_STREAM_DRIVER
    driver_rect = rl.Rectangle(camera_width, camera_start_y, camera_width, camera_height)
    driver.render(driver_rect)
    rl.draw_rectangle_lines(camera_width, camera_start_y, camera_width, camera_height, rl.WHITE)
    rl.draw_text("DRIVER", camera_width + label_padding, camera_start_y + label_padding, label_font_size, rl.WHITE)

    # VISION_STREAM_WIDE_ROAD
    wide_rect = rl.Rectangle(camera_width * 2, camera_start_y, camera_width, camera_height)
    wide.render(wide_rect)
    rl.draw_rectangle_lines(camera_width * 2, camera_start_y, camera_width, camera_height, rl.WHITE)
    rl.draw_text("WIDE", camera_width * 2 + label_padding, camera_start_y + label_padding, label_font_size, rl.WHITE)
