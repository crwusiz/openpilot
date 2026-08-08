import numpy as np
import os
import time
import cv2
import usb.util
import usb.core
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
    self._send_jpeg = None
    self._resp_ok = None
    self._build_header = None
    self._encrypt = None
    self._cmd_upload_jpeg = None
    self._ep_out = None
    flog("TuringUsbDisplay initialized.")

  def open(self):
    if self.connected:
      return True

    flog("Attempting to connect to Turing USB Display...")

    try:
      os.environ['LC_ALL'] = 'C.UTF-8'
      os.environ['LANG'] = 'C.UTF-8'
      os.environ['LANGUAGE'] = 'C.UTF-8'

      from library.lcd.lcd_comm_turing_usb import (
        find_usb_device, send_image, send_jpeg, send_sync_command,
        send_frame_rate_command, _resp_ok,
        build_command_packet_header, encrypt_command_packet, CMD_UPLOAD_JPEG,
      )

      self._find_usb_device = find_usb_device
      self._send_image = send_image
      self._send_jpeg = send_jpeg
      self._resp_ok = _resp_ok
      self._build_header = build_command_packet_header
      self._encrypt = encrypt_command_packet
      self._cmd_upload_jpeg = CMD_UPLOAD_JPEG

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

          cfg = self.device.get_active_configuration()
          intf = usb.util.find_descriptor(cfg, bInterfaceNumber=0)
          self._ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)

          for attempt in range(3):
            flog(f"[CLUSTER_USB] Sending sync handshake (Attempt {attempt+1})...")
            resp = send_sync_command(self.device)
            flog(f"[CLUSTER_USB] Sync response: ok={self._resp_ok(resp)}")
            time.sleep(0.3)

          resp = send_frame_rate_command(self.device, self.config.fps)
          flog(f"[CLUSTER_USB] Frame rate response: ok={self._resp_ok(resp)}")
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

  def _write_image_no_wait(self, jpeg_bytes):
    """
    이미지 업로드 커맨드는 펌웨어가 ACK를 안 주는 것으로 확인됨.
    응답을 기다리지 않고 write만 수행 (fire-and-forget).
    """
    img_size = len(jpeg_bytes)
    cmd_packet = self._build_header(self._cmd_upload_jpeg)
    cmd_packet[8] = (img_size >> 24) & 0xFF
    cmd_packet[9] = (img_size >> 16) & 0xFF
    cmd_packet[10] = (img_size >> 8) & 0xFF
    cmd_packet[11] = img_size & 0xFF
    full_payload = self._encrypt(cmd_packet) + jpeg_bytes

    # 타임아웃을 1000 -> 150으로 대폭 축소 (Fast-Fail)
    # 버퍼가 찼을 때 1초간 멈추는 프리징(Stuttering) 현상 제거
    self._ep_out.write(full_payload, 150)

  def send_image(self, frame_image):
    if not self.connected or self.device is None:
      return

    try:
      if not isinstance(frame_image, np.ndarray):
        frame_image = np.array(frame_image)

      rotated = cv2.rotate(frame_image, cv2.ROTATE_90_CLOCKWISE)
      bgr_img = cv2.cvtColor(rotated, cv2.COLOR_RGB2BGR)

      encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
      success, encoded_img = cv2.imencode('.jpg', bgr_img, encode_param)

      if success:
        jpg_bytes = encoded_img.tobytes()
        size_kb = len(jpg_bytes) // 1024

        t0 = time.time()
        self._write_image_no_wait(jpg_bytes)
        elapsed = time.time() - t0

        self.frame_count += 1
        if self.frame_count <= 20 or self.frame_count % self.config.fps == 0:
          flog(f"[CLUSTER_USB_TX] frame#{self.frame_count} | Size: {size_kb} KB | elapsed={elapsed:.3f}s (write-only, no ACK wait)")

    except usb.core.USBError as e:
      if e.errno == 110 or 'timed out' in str(e).lower():
        # Errno 110 발생 시 1프레임 버림 (로그는 너무 많이 쌓이지 않도록 주석 처리 또는 유지)
        flog(f"[CLUSTER_USB_WARN] Buffer full - 1 frame skipped (Fast-Fail)")
        try:
          if self._ep_out and self.device:
            self.device.clear_halt(self._ep_out)
        except Exception as clear_err:
          # Errno None 에러가 로그를 덮는 것을 방지
          pass
      else:
        err_msg = f"Critical USB Error: {e}"
        flog(f"[CLUSTER_USB_ERROR] {err_msg}")
        self.connected = False
        self.device = None
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
