"""Network display startup for JPEG and H.264 transports."""


def run_network_cluster(config):
  if getattr(config, "network_codec", "jpeg") == "h264":
    from openpilot.selfdrive.addon.cluster.hdmi_display.network_h264 import run_h264_cluster
    run_h264_cluster(config)
    return

  from openpilot.selfdrive.addon.cluster.cluster_frame_clock import ClusterFrameClock
  from openpilot.selfdrive.addon.cluster.cluster_jpeg_runtime import run_jpeg_cluster
  from openpilot.selfdrive.addon.cluster.hdmi_display.network_codec import requested_codec
  from openpilot.selfdrive.addon.cluster.hdmi_display.network_display import ClusterNetworkDisplay

  clock = ClusterFrameClock(config.fps)

  def wait_for_frame(_camera, last_frame):
    clock.wait()
    return last_frame

  run_jpeg_cluster(
    config, ClusterNetworkDisplay(config), wait_for_frame=wait_for_frame,
    mode_changed=lambda: requested_codec(config.params) != "jpeg",
    screen_off_fps=getattr(config, "network_screen_off_fps", 5),
  )
