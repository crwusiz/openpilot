import os
from pathlib import Path
from types import SimpleNamespace

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

DISPLAY = SimpleNamespace(
  transport="usb",  # "usb" or "network"
  rotate_180=False,
  fps=20,
  status_interval_s=10,
  border_size=10,
)

IMAGE = SimpleNamespace(
  jpeg_quality=68,
  camera_contrast=1.08,
)

USB = SimpleNamespace(
  width=1920,
  height=462,
  timeout_ms=2500,
  image_timeout_ms=1000,
  clear_halt_on_timeout=True,
  max_consecutive_failures=3,
)

# 8.8-inch HDMI panel; keep the standalone Orange Pi receiver defaults in sync.
HDMI = SimpleNamespace(
  width=1920,
  height=480,
)

# C4 and the Orange Pi join the same hotspot. C4 listens on every Wi-Fi
# address, and the Orange Pi discovers this port in its current IPv4 subnet.
NETWORK = SimpleNamespace(
  bind_host="0.0.0.0",
  port=9200,
  accept_timeout_s=0.25,
  ack_timeout_s=2.5,
)

RGBColor = tuple[int, int, int]
RGBAColor = tuple[int, int, int, int]


def colors_alpha(color: RGBColor | RGBAColor, alpha: int = 255) -> RGBAColor:
  return (*color[:3], alpha)


class Colors:
  """UI palette layout with RGBA tuples for the PIL/NumPy renderer."""
  BLACK = (0, 0, 0, 255)
  PANEL = (7, 12, 18, 255)
  DIVIDER = (42, 54, 68, 255)
  MUTED_TEXT = (120, 132, 148, 255)
  SIGN_TEXT = (20, 25, 30, 255)
  DISTANCE_BADGE = (18, 25, 34, 255)
  WHITE = (255, 255, 255, 255)

  RED = (201, 34, 49, 255)
  STEERING = (0, 191, 255, 255)
  ORANGE = (255, 149, 0, 255)

  MAX_ACTIVE = (128, 216, 166, 255)
  CAUTION = (255, 200, 100, 255)

  Border = SimpleNamespace(
    DISENGAGED=(18, 40, 57, 255),
    OVERRIDE=(137, 146, 141, 255),
    ENGAGED=(22, 127, 64, 255),
    RED=RED,
    STEERING=STEERING,
    BLINKER=ORANGE,
    ACTIVE=(111, 192, 201, 255),
    READY=(143, 201, 192, 255),
  )

  THROTTLE = [
    (13, 248, 122, 102),
    (114, 255, 92, 89),
    (114, 255, 92, 0),
  ]

  NO_THROTTLE = [
    (242, 242, 242, 102),
    (242, 242, 242, 89),
    (242, 242, 242, 0),
  ]

  STEERING_PRESSED = [
    (0, 191, 255, 102),
    (0, 191, 255, 89),
    (0, 191, 255, 0),
  ]


class ClusterConfig:
  def __init__(self):
    cloudlog.info("Loading Lightweight Cluster Configuration...")

    self.params = Params()
    self.display_transport = self.params.get("ClusterDisplayTransport") or DISPLAY.transport
    if self.display_transport not in ("network", "usb"):
      raise ValueError(f"Unsupported cluster display transport: {self.display_transport}")
    if self.display_transport == "network":
      self.width, self.height = HDMI.width, HDMI.height
    else:
      self.width, self.height = USB.width, USB.height
    # Road camera and model data are published at 20 Hz on-device.
    self.fps = DISPLAY.fps
    self.usb_fps = DISPLAY.fps
    self.status_interval_frames = self.fps * DISPLAY.status_interval_s
    # carrot-pilot's field-tested JPEG default. This improves camera detail
    # over quality 60 without materially increasing encode time or link load.
    self.jpeg_quality = IMAGE.jpeg_quality
    self.border_size = DISPLAY.border_size
    self.content_width = self.width - self.border_size * 2
    self.content_height = self.height - self.border_size * 2
    self.side_panel_width = self.content_height // 3
    self.camera_panel_width = self.content_width - self.side_panel_width * 4
    self.camera_panel_height = self.content_height
    self.camera_contrast = IMAGE.camera_contrast

    self.usb_timeout_ms = USB.timeout_ms
    self.usb_image_timeout_ms = USB.image_timeout_ms
    self.usb_clear_halt_on_timeout = USB.clear_halt_on_timeout
    self.usb_max_consecutive_failures = USB.max_consecutive_failures

    self.network_bind_host = NETWORK.bind_host
    self.network_port = NETWORK.port
    self.network_accept_timeout = NETWORK.accept_timeout_s
    self.network_ack_timeout = NETWORK.ack_timeout_s

    self.BASEDIR = Path(__file__).resolve().parents[3]
    self.font_bold = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Bold.ttf")
    self.font_regular = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Regular.ttf")

    self.is_metric = self.params.get_bool("IsMetric")
    self.rotate_180 = DISPLAY.rotate_180

    self.speed_unit = "km/h" if self.is_metric else "mph"

    cloudlog.info(
      f"Cluster Config Loaded: {self.width}x{self.height} @ {self.fps}fps, Unit: {self.speed_unit}, "
      + f"Rotate 180: {self.rotate_180}, Transport: {self.display_transport}",
    )
