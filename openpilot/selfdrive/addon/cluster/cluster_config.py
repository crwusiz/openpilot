import os
from pathlib import Path
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

USB_TIMEOUT_MS = 2500
USB_IMAGE_TIMEOUT_MS = 1000
USB_TARGET_FPS = 20
USB_CLEAR_HALT_ON_TIMEOUT = True
USB_JPEG_QUALITY = 68
CAMERA_CONTRAST = 1.08
CLUSTER_BORDER_SIZE = 10

CLUSTER_COLORS = {
  "bg": (0, 0, 0),
  "panel": (7, 12, 18),
  "divider": (42, 54, 68),
  "text": (245, 248, 252),
  "muted_text": (120, 132, 148),
  "outline": (0, 0, 0),
  "sign_red": (255, 105, 95),
  "sign_text": (20, 25, 30),
  "distance_badge": (18, 25, 34),
  "engaged": (23, 134, 68),
  "disengaged": (23, 51, 73),
  "warning": (218, 48, 43),
  "override": (145, 155, 149),
  "status_brake": (255, 80, 70),
  "status_blinker": (255, 170, 55),
  "ignore_timer": (255, 149, 0, 150),
  "max_active": (128, 216, 166, 255),
  "speed_critical": (201, 34, 49, 255),
  "speed_warning": (255, 149, 0, 255),
  "speed_caution": (255, 200, 100, 255),
  "tpms_unknown": (230, 150, 45),
  "lane_line": (255, 255, 255),
  "path_active": (23, 134, 68),
  "path_steering": (0, 191, 255),
}


class ClusterConfig:
  def __init__(self):
    cloudlog.info("Loading Lightweight Cluster Configuration...")

    self.width = 1920
    self.height = 462
    # Road camera and model data are published at 20 Hz on-device.
    self.fps = USB_TARGET_FPS
    self.usb_fps = USB_TARGET_FPS
    # carrot-pilot's field-tested JPEG default. This improves camera detail
    # over quality 60 without materially increasing encode time or USB load.
    self.usb_jpeg_quality = USB_JPEG_QUALITY
    self.border_size = CLUSTER_BORDER_SIZE
    self.content_width = self.width - self.border_size * 2
    self.content_height = self.height - self.border_size * 2
    self.side_panel_width = self.content_height // 3
    self.camera_panel_width = self.content_width - self.side_panel_width * 4
    self.camera_panel_height = self.content_height
    self.camera_contrast = CAMERA_CONTRAST

    self.usb_timeout_ms = USB_TIMEOUT_MS
    self.usb_image_timeout_ms = USB_IMAGE_TIMEOUT_MS

    self.colors = CLUSTER_COLORS.copy()

    self.BASEDIR = Path(__file__).resolve().parents[3]
    self.font_bold = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Bold.ttf")
    self.font_regular = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Regular.ttf")

    self.params = Params()
    self.is_metric = self.params.get_bool("IsMetric")
    self.rotate_180 = self.params.get_bool("ClusterRotate")

    self.speed_unit = "km/h" if self.is_metric else "mph"

    cloudlog.info(f"Cluster Config Loaded: {self.width}x{self.height} @ {self.fps}fps, "
                  f"Unit: {self.speed_unit}, Rotate 180: {self.rotate_180}")

  def refresh(self):
    rotate_180 = self.params.get_bool("ClusterRotate")
    if rotate_180 != self.rotate_180:
      self.rotate_180 = rotate_180
      cloudlog.info(f"Cluster rotation changed: 180={self.rotate_180}")
