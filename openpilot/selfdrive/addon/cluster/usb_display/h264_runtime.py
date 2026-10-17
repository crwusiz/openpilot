"""USB full-frame H.264 runtime; preserve the shared HUD and input guards."""

from openpilot.selfdrive.addon.cluster.cluster_frame_clock import ClusterFrameClock
from openpilot.selfdrive.addon.cluster.usb_display.runtime import run_composed_cluster
from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.usb_display.h264_display import H264DisplayAdapter


def run_h264_usb_cluster(config):
  flog("[CLUSTER_USB_H264_STAGE] Loading the C4 hardware encoder bridge...")
  display = H264DisplayAdapter(config)
  flog("[CLUSTER_USB_H264_STAGE] Hardware bridge loaded; preparing USB display and composed frames.")
  clock = ClusterFrameClock(config.usb_h264_fps)

  def wait_for_frame(camera, _last_frame):
    clock.wait()
    # A fixed deadline can land just before RGB conversion completes. Consuming
    # the old image then makes the next raw arrival look unneeded, halving the
    # visible camera rate. Wait for the unconsumed image, at most one period;
    # camera outages must still allow HUD updates and runtime/error checks.
    return camera.wait_for_frame(timeout=clock.interval)

  run_composed_cluster(config, display, wait_for_frame=wait_for_frame, check_runtime=display.check_encoder)
