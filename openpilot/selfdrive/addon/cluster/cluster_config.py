import os
from pathlib import Path
from types import SimpleNamespace

from openpilot.selfdrive.addon.cluster.cluster_render_support import Colors as Colors, colors_alpha as colors_alpha

DISPLAY = SimpleNamespace(
  transport="usb",  # "usb" or "network"
  rotate_180=False,
  status_interval_s=10,
  border_size=10,
)

IMAGE = SimpleNamespace(
  camera_contrast=1.08,
)

USB = SimpleNamespace(
  width=1920,
  height=462,
  write_timeout_ms=1000,
  max_frame_age_s=0.25,
  h264_fps=20,
  h264_bitrate=4_666_667,  # Match the tested 7 Mbps / 30 Hz USB encoder budget per frame.
  h264_gop=1,  # Independent frames avoid persistent USB display decoder artifacts.
  h264_slice_max_bytes=4096,
  h264_device="/dev/v4l/by-path/platform-aa00000.qcom_vidc-video-index1",
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
  fps=20,  # Reuse the hardware camera stream; compose the HUD on the Pi.
)


class ClusterConfig:
  def __init__(self):
    from openpilot.common.params import Params
    from openpilot.common.swaglog import cloudlog

    cloudlog.info("Loading Lightweight Cluster Configuration...")

    self.params = Params()
    self.display_transport = self.params.get("ClusterDisplayTransport") or DISPLAY.transport
    if self.display_transport not in ("network", "usb"):
      raise ValueError(f"Unsupported cluster display transport: {self.display_transport}")
    if self.display_transport == "network":
      self.width, self.height = HDMI.width, HDMI.height
    else:
      self.width, self.height = USB.width, USB.height
    # Keep the cluster cadence independent of the C4 UI's FPS override.
    self.fps = min(max(int(os.getenv("CLUSTER_FPS", NETWORK.fps)), 1), 60) if self.display_transport == "network" else USB.h264_fps
    self.status_interval_frames = self.fps * DISPLAY.status_interval_s
    self.border_size = DISPLAY.border_size
    self.content_width = self.width - self.border_size * 2
    self.content_height = self.height - self.border_size * 2
    self.side_panel_width = self.content_height // 3
    self.camera_panel_width = self.content_width - self.side_panel_width * 4
    self.camera_panel_height = self.content_height
    self.camera_contrast = IMAGE.camera_contrast

    self.usb_write_timeout_ms = USB.write_timeout_ms
    self.usb_max_frame_age = USB.max_frame_age_s
    self.usb_h264_fps = USB.h264_fps
    self.usb_h264_bitrate = USB.h264_bitrate
    self.usb_h264_gop = USB.h264_gop
    self.usb_h264_slice_max_bytes = USB.h264_slice_max_bytes
    self.usb_h264_device = USB.h264_device

    self.network_bind_host = NETWORK.bind_host
    self.network_port = NETWORK.port

    self.BASEDIR = Path(__file__).resolve().parents[3]
    self.font_bold = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "NotoSansKR-Bold.ttf")
    self.font_regular = self.font_bold

    self.is_metric = self.params.get_bool("IsMetric")
    self.rotate_180 = DISPLAY.rotate_180

    self.speed_unit = "km/h" if self.is_metric else "mph"

    cloudlog.info(
      f"Cluster Config Loaded: {self.width}x{self.height} @ {self.fps}fps, Unit: {self.speed_unit}, "
      + f"Rotate 180: {self.rotate_180}, Transport: {self.display_transport}, Codec: H.264",
    )
