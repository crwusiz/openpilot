import os
from pathlib import Path

from openpilot.common.hardware.usb import is_chestnut_connected
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

USB_TIMEOUT_MS = 2500
USB_IMAGE_TIMEOUT_MS = 1000
USB_TARGET_FPS = 20
CLUSTER_STATUS_INTERVAL_SECONDS = 10
USB_CLEAR_HALT_ON_TIMEOUT = True
USB_MAX_CONSECUTIVE_FAILURES = 3
CLUSTER_JPEG_QUALITY = 68
CAMERA_CONTRAST = 1.08
CLUSTER_BORDER_SIZE = 10

# C4 and the Orange Pi join the same phone hotspot. C4 listens on every Wi-Fi
# address, and the Orange Pi discovers this port in its current IPv4 subnet.
CLUSTER_DISPLAY_TRANSPORT = "network"  # "network" or "usb"
CLUSTER_ROTATE_180 = False
CLUSTER_USB_WIDTH = 1920
CLUSTER_USB_HEIGHT = 462
CLUSTER_HDMI_WIDTH = 1920
CLUSTER_HDMI_HEIGHT = 480
CLUSTER_NETWORK_BIND_HOST = "0.0.0.0"
CLUSTER_NETWORK_PORT = 9200
CLUSTER_NETWORK_ACCEPT_TIMEOUT_SECONDS = 0.25
CLUSTER_NETWORK_ACK_TIMEOUT_SECONDS = 2.5

RGBColor = tuple[int, int, int]
RGBAColor = tuple[int, int, int, int]


def colors_alpha(color: RGBColor, alpha: int) -> RGBAColor:
  return (*color, alpha)


THROTTLE_COLORS = [
  (13, 248, 122, 102),
  (114, 255, 92, 89),
  (114, 255, 92, 0),
]

NO_THROTTLE_COLORS = [
  (242, 242, 242, 102),
  (242, 242, 242, 89),
  (242, 242, 242, 0),
]

STEERING_COLORS = [
  (0, 191, 255, 102),
  (0, 191, 255, 89),
  (0, 191, 255, 0),
]


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

    self.params = Params()
    self.display_transport = self.params.get("ClusterDisplayTransport") or CLUSTER_DISPLAY_TRANSPORT
    if is_chestnut_connected(include_bootloader=True):
      if self.display_transport != "network":
        cloudlog.warning("Chestnut USB detected; forcing ClusterDisplayTransport to network")
        self.params.put("ClusterDisplayTransport", "network", block=True)
      self.display_transport = "network"
    if self.display_transport not in ("network", "usb"):
      raise ValueError(f"Unsupported cluster display transport: {self.display_transport}")
    if self.display_transport == "network":
      self.width, self.height = CLUSTER_HDMI_WIDTH, CLUSTER_HDMI_HEIGHT
    else:
      self.width, self.height = CLUSTER_USB_WIDTH, CLUSTER_USB_HEIGHT
    # Road camera and model data are published at 20 Hz on-device.
    self.fps = USB_TARGET_FPS
    self.usb_fps = USB_TARGET_FPS
    self.status_interval_frames = self.fps * CLUSTER_STATUS_INTERVAL_SECONDS
    # carrot-pilot's field-tested JPEG default. This improves camera detail
    # over quality 60 without materially increasing encode time or link load.
    self.jpeg_quality = CLUSTER_JPEG_QUALITY
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

    self.network_bind_host = CLUSTER_NETWORK_BIND_HOST
    self.network_port = CLUSTER_NETWORK_PORT
    self.network_accept_timeout = CLUSTER_NETWORK_ACCEPT_TIMEOUT_SECONDS
    self.network_ack_timeout = CLUSTER_NETWORK_ACK_TIMEOUT_SECONDS

    self.BASEDIR = Path(__file__).resolve().parents[3]
    self.font_bold = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Bold.ttf")
    self.font_regular = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Regular.ttf")

    self.is_metric = self.params.get_bool("IsMetric")
    self.rotate_180 = CLUSTER_ROTATE_180

    self.speed_unit = "km/h" if self.is_metric else "mph"

    cloudlog.info(
      f"Cluster Config Loaded: {self.width}x{self.height} @ {self.fps}fps, Unit: {self.speed_unit}, "
      + f"Rotate 180: {self.rotate_180}, Transport: {self.display_transport}",
    )
