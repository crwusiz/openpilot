import os
from pathlib import Path
from types import SimpleNamespace

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_render_support import Colors as Colors, colors_alpha as colors_alpha

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
  max_frame_age_s=0.25,
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
  fps=20,  # Full-frame JPEG baseline; camera/model source updates are 20 Hz.
  screen_off_fps=5,
  jpeg_quality=68,
  max_in_flight=2,
  max_in_flight_bytes=512 * 1024,
  max_frame_age_s=0.25,
  max_ack_age_s=0.25,
)

class ClusterConfig:
  def __init__(self):
    cloudlog.info("Loading Lightweight Cluster Configuration...")

    self.params = Params()
    self.display_transport = self.params.get("ClusterDisplayTransport") or DISPLAY.transport
    if self.display_transport not in ("network", "usb"):
      raise ValueError(f"Unsupported cluster display transport: {self.display_transport}")
    network_codec = self.params.get("ClusterNetworkCodec") or "jpeg"
    if isinstance(network_codec, bytes):
      network_codec = network_codec.decode("utf-8", errors="replace")
    self.network_codec = str(network_codec).strip().lower()
    if self.network_codec not in ("jpeg", "h264"):
      if self.display_transport == "network":
        cloudlog.warning(f"Unsupported cluster network codec: {network_codec!r}; using JPEG")
      self.network_codec = "jpeg"
    if self.display_transport == "network":
      self.width, self.height = HDMI.width, HDMI.height
    else:
      self.width, self.height = USB.width, USB.height
    # The UI's FPS override must not raise the full-frame JPEG workload.
    self.fps = min(max(int(os.getenv("CLUSTER_FPS", NETWORK.fps)), 1), 60) if self.display_transport == "network" else DISPLAY.fps
    self.usb_fps = DISPLAY.fps
    self.status_interval_frames = self.fps * DISPLAY.status_interval_s
    # carrot-pilot's field-tested JPEG default. This improves camera detail
    # over quality 60 without materially increasing encode time or link load.
    self.jpeg_quality = IMAGE.jpeg_quality
    self.network_jpeg_quality = NETWORK.jpeg_quality
    self.network_screen_off_fps = NETWORK.screen_off_fps
    self.border_size = DISPLAY.border_size
    self.content_width = self.width - self.border_size * 2
    self.content_height = self.height - self.border_size * 2
    self.side_panel_width = self.content_height // 3
    self.camera_panel_width = self.content_width - self.side_panel_width * 4
    self.camera_panel_height = self.content_height
    self.camera_contrast = IMAGE.camera_contrast

    self.usb_timeout_ms = USB.timeout_ms
    self.usb_image_timeout_ms = USB.image_timeout_ms
    self.usb_max_frame_age = USB.max_frame_age_s
    self.usb_clear_halt_on_timeout = USB.clear_halt_on_timeout
    self.usb_max_consecutive_failures = USB.max_consecutive_failures

    self.network_bind_host = NETWORK.bind_host
    self.network_port = NETWORK.port
    self.network_accept_timeout = NETWORK.accept_timeout_s
    self.network_ack_timeout = NETWORK.ack_timeout_s
    self.network_max_in_flight = NETWORK.max_in_flight
    self.network_max_in_flight_bytes = NETWORK.max_in_flight_bytes
    self.network_max_frame_age = NETWORK.max_frame_age_s
    self.network_max_ack_age = NETWORK.max_ack_age_s

    self.BASEDIR = Path(__file__).resolve().parents[3]
    self.font_bold = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Bold.ttf")
    self.font_regular = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Regular.ttf")

    self.is_metric = self.params.get_bool("IsMetric")
    self.rotate_180 = DISPLAY.rotate_180

    self.speed_unit = "km/h" if self.is_metric else "mph"

    cloudlog.info(
      f"Cluster Config Loaded: {self.width}x{self.height} @ {self.fps}fps, Unit: {self.speed_unit}, "
      + f"Rotate 180: {self.rotate_180}, Transport: {self.display_transport}"
      + (f", Network Codec: {self.network_codec}" if self.display_transport == "network" else ""),
    )
