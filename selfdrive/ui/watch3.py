#!/usr/bin/env python3
import pyray as rl

from msgq.visionipc import VisionStreamType
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.onroad.cameraview import CameraView

FONT_SIZE = 30
TEXT_COLOR = rl.WHITE
PADDING = 10


if __name__ == "__main__":
  gui_app.init_window("watch3")
  road = CameraView("camerad", VisionStreamType.VISION_STREAM_ROAD)
  driver = CameraView("camerad", VisionStreamType.VISION_STREAM_DRIVER)
  wide = CameraView("camerad", VisionStreamType.VISION_STREAM_WIDE_ROAD)

  W = gui_app.width
  H = gui_app.height

  VIEW_W = W // 2
  VIEW_H = H // 2

  for _ in gui_app.render():
    road_rect = rl.Rectangle(0, 0, VIEW_W, VIEW_H)
    road.render(road_rect)
    rl.draw_text("ROAD", road_rect.x + PADDING, road_rect.y + PADDING, FONT_SIZE, TEXT_COLOR)

    wide_rect = rl.Rectangle(VIEW_W, 0, VIEW_W, VIEW_H)
    wide.render(wide_rect)
    rl.draw_text("WIDE", wide_rect.x + PADDING, wide_rect.y + PADDING, FONT_SIZE, TEXT_COLOR)

    driver_rect = rl.Rectangle(W // 4, VIEW_H, VIEW_W, VIEW_H)
    driver.render(driver_rect)
    rl.draw_text("DRIVER", driver_rect.x + PADDING, driver_rect.y + PADDING, FONT_SIZE, TEXT_COLOR)
