import os
from pathlib import Path

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_logging import LOG_FILE  # re-export for compatibility

USB_TIMEOUT_MS = 2500
USB_IMAGE_TIMEOUT_MS = 1000
USB_TARGET_FPS = 20
USB_CLEAR_HALT_ON_TIMEOUT = True
USB_MAX_CONSECUTIVE_FAILURES = 3
USB_JPEG_QUALITY = 68
CAMERA_CONTRAST = 1.08
CLUSTER_BORDER_SIZE = 10

RGBColor = tuple[int, int, int]
RGBAColor = tuple[int, int, int, int]


def colors_alpha(color: RGBColor, alpha: int) -> RGBAColor:
  return (*color, alpha)


class Colors:
  BLACK = (0, 0, 0)
  PANEL = (7, 12, 18)
  DIVIDER = (42, 54, 68)
  MUTED_TEXT = (120, 132, 148)
  SIGN_TEXT = (20, 25, 30)
  DISTANCE_BADGE = (18, 25, 34)
  WHITE = (255, 255, 255)

  DISENGAGED = (18, 40, 57)
  OVERRIDE = (137, 146, 141)
  ENGAGED = (22, 127, 64)
  RED = (201, 34, 49)
  STEERING = (0, 191, 255)
  ORANGE = (255, 149, 0)
  ACTIVE = (111, 192, 201)
  READY = (143, 201, 192)

  MAX_ACTIVE = (128, 216, 166)
  CAUTION = (255, 200, 100)


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
    self.usb_clear_halt_on_timeout = USB_CLEAR_HALT_ON_TIMEOUT
    self.usb_max_consecutive_failures = USB_MAX_CONSECUTIVE_FAILURES

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
