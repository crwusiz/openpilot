import locale
import os
import sys
from pathlib import Path


def configure_usb_runtime() -> None:
  # The vendor logger reads the locale from the environment during import.
  for candidate in ("C.UTF-8", "C"):
    try:
      locale.setlocale(locale.LC_ALL, candidate)
    except locale.Error:
      continue
    os.environ["LC_ALL"] = candidate
    os.environ["LC_CTYPE"] = candidate
    os.environ["LANG"] = candidate
    break

  vendor_root = str(Path(__file__).resolve().parent / ".vendor" / "turing-smart-screen-python-main")
  if vendor_root not in sys.path:
    sys.path.insert(0, vendor_root)


def run_usb_cluster(config):
  configure_usb_runtime()
  if getattr(config, "usb_codec", "jpeg") == "h264":
    from openpilot.selfdrive.addon.cluster.usb_display.h264_runtime import run_h264_usb_cluster
    run_h264_usb_cluster(config)
    return

  from openpilot.selfdrive.addon.cluster.cluster_config import requested_usb_codec
  from openpilot.selfdrive.addon.cluster.cluster_jpeg_runtime import run_jpeg_cluster
  from openpilot.selfdrive.addon.cluster.usb_display.turing_usb_display import TuringUsbDisplay

  def wait_for_frame(camera, last_frame):
    return camera.wait_for_frame(last_frame, 1.0 / config.fps)

  run_jpeg_cluster(config, TuringUsbDisplay(config), wait_for_frame=wait_for_frame,
                   mode_changed=lambda: requested_usb_codec(config.params) != "jpeg")
