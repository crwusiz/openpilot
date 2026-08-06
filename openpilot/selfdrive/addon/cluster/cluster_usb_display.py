from io import BytesIO

from PIL import Image

from openpilot.common.swaglog import cloudlog

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

    self._find_usb_device = None
    self._send_image = None

    cloudlog.info("TuringUsbDisplay initialized.")

  def open(self):
    if self.connected:
      return True

    cloudlog.info(f"Attempting to connect to Turing USB Display (VID: {hex(TURZX_USB_VENDOR_ID)})...")

    try:
      from library.lcd.lcd_comm_turing_usb import find_usb_device, send_image

      self._find_usb_device = find_usb_device
      self._send_image = send_image

      self.device, self.dev_pid = self._find_usb_device()

      if self.device is not None:
        self.product_id = self.device.idProduct
        if self.product_id in TURZX_USB_PRODUCT_IDS:
          self.connected = True
          device_name = TURZX_USB_PRODUCT_IDS[self.product_id]
          cloudlog.info(f"Successfully connected to {device_name} (PID: {hex(self.product_id)}).")
          return True
        else:
          cloudlog.error(f"Found a Turing device, but unsupported PID: {hex(self.product_id)}. Expected 9.2 inch.")
          self.device = None
          return False
      else:
        cloudlog.error("Turing USB Display not found. Please check the USB connection.")
        return False

    except ImportError as e:
      cloudlog.error(f"Vendor library import failed: {e}. Check .vendor folder.")
      return False
    except Exception as e:
      cloudlog.error(f"Error opening Turing display: {e}")
      return False

  def send_image(self, frame_image):
    if not self.connected or self.device is None:
      return

    try:
      png_buf = BytesIO()
      Image.fromarray(frame_image).save(png_buf, format='PNG')
      png_bytes = png_buf.getvalue()

      self._send_image(self.device, png_bytes)

    except Exception as e:
      cloudlog.error(f"Failed to send image to Turing display: {e}")
      self.connected = False
      self.device = None

  def close(self):
    cloudlog.info("Closing Turing USB Display connection.")
    self.connected = False
    self.device = None
    self.dev_pid = None
