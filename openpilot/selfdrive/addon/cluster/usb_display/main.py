"""Load only the USB H.264 runtime after preparing its dependencies."""


def run_usb_cluster(config):
  from openpilot.selfdrive.addon.cluster.usb_display.h264_runtime import run_h264_usb_cluster
  run_h264_usb_cluster(config)
