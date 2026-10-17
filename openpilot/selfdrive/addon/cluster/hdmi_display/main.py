"""Reuse the C4 H.264 camera stream and compose the cluster on the Pi."""


def run_network_cluster(config):
  from openpilot.selfdrive.addon.cluster.hdmi_display.network_h264 import run_h264_cluster
  run_h264_cluster(config)
