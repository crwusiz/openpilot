import numpy as np
import os
import time
import cv2
from openpilot.common.swaglog import cloudlog

LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"

def flog(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except:
        pass

TURZX_USB_VENDOR_ID = 0x1CBE
TURZX_USB_PRODUCT_IDS = {
  0x0092: "TURZX 9.2 inch",
}

class TuringUsbDisplay:
  def __init__(self, config):
    self.config = config
    self.device = None
    self.dev_pid = None
    self.connected = False
    self.product_id = None
    self.frame_count = 0

    self._find_usb_device = None
    self._send_image = None
    flog("TuringUsbDisplay initialized.")

  def open(self):
    if self.connected:
      return True

    flog("Attempting to connect to Turing USB Display...")

    try:
      os.environ['LC_ALL'] = 'C.UTF-8'
      os.environ['LANG'] = 'C.UTF-8'
      os.environ['LANGUAGE'] = 'C.UTF-8'

      from library.lcd.lcd_comm_turing_usb import find_usb_device, send_image, send_sync_command, send_frame_rate_command

      self._find_usb_device = find_usb_device
      self._send_image = send_image

      self.device, self.dev_pid = self._find_usb_device()

      if self.device is not None:
        self.product_id = getattr(self.device, 'idProduct', self.dev_pid)
        if self.product_id in TURZX_USB_PRODUCT_IDS:

          try:
            self.device.reset()
            for cfg in self.device:
              for intf in cfg:
                if self.device.is_kernel_driver_active(intf.bInterfaceNumber):
                  self.device.detach_kernel_driver(intf.bInterfaceNumber)
            self.device.set_configuration()
            flog("[CLUSTER_USB] Detached Linux kernel driver successfully.")
            time.sleep(1.0)
          except Exception as e:
            flog(f"[CLUSTER_USB_WARN] Detach warning: {e}")

          for attempt in range(3):
            flog(f"[CLUSTER_USB] Sending sync handshake (Attempt {attempt+1})...")
            send_sync_command(self.device)
            time.sleep(0.3)

          send_frame_rate_command(self.device, self.config.fps)
          time.sleep(0.1)

          self.connected = True
          flog(f"[CLUSTER_USB_SUCCESS] Connected to TURZX 9.2 inch (PID: {hex(self.product_id)}).")
          return True
        else:
          flog(f"[CLUSTER_USB_ERROR] Unsupported PID: {hex(self.product_id)}")
          self.device = None
          return False
      else:
        flog("[CLUSTER_USB_ERROR] Turing USB Display not found.")
        return False

    except Exception as e:
      flog(f"[CLUSTER_USB_ERROR] Error opening Turing display: {e}")
      return False

  def send_image(self, frame_image):
    if not self.connected or self.device is None:
      return

    try:
      if not isinstance(frame_image, np.ndarray):
        frame_image = np.array(frame_image)

      bgr_img = cv2.cvtColor(frame_image, cv2.COLOR_RGB2BGR)

      encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
      success, encoded_img = cv2.imencode('.jpg', bgr_img, encode_param)

      if success:
        jpg_bytes = encoded_img.tobytes()
        self._send_image(self.device, jpg_bytes)

        self.frame_count += 1
        if self.frame_count % self.config.fps == 0:
          size_kb = len(jpg_bytes) // 1024
          flog(f"[CLUSTER_USB_TX] Pushed frame to device | Res: {bgr_img.shape[1]}x{bgr_img.shape[0]} | Size: {size_kb} KB")

    except Exception as e:
      err_msg = f"Failed to send image to Turing display: {e}"
      flog(f"[CLUSTER_USB_ERROR] {err_msg}")
      self.connected = False
      self.device = None

  def close(self):
    flog("Closing Turing connection.")
    self.connected = False
    self.device = None
    self.dev_pid = None
