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
    self.consecutive_upload_failures = 0

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

          # find_usb_device() has already configured the device and, on Linux,
          # detached the kernel driver.  Resetting here forces USB re-enumeration
          # and invalidates this PyUSB handle (Errno 19 on the first connection).
          try:
            if self.device.is_kernel_driver_active(0):
              self.device.detach_kernel_driver(0)
              self.device.set_configuration()
              flog("[CLUSTER_USB] Detached Linux kernel driver.")
          except usb.core.USBError as e:
            # Errno 2 simply means no kernel driver is bound; it is safe to ignore.
            if e.errno != 2:
              flog(f"[CLUSTER_USB_WARN] Driver detach warning: {e}")

          # 이미지 업로드용 OUT 엔드포인트를 미리 찾아서 캐싱
          cfg = self.device.get_active_configuration()
          intf = usb.util.find_descriptor(cfg, bInterfaceNumber=0)
          self._ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)

          for attempt in range(3):
            flog(f"[CLUSTER_USB] Sending sync handshake (Attempt {attempt+1})...")
            resp = send_sync_command(self.device)
            flog(f"[CLUSTER_USB] Sync response: ok={self._resp_ok(resp)}")
            time.sleep(0.3)

          resp = send_frame_rate_command(self.device, self.config.usb_fps)
          flog(f"[CLUSTER_USB] Frame rate response: ok={self._resp_ok(resp)}")
          time.sleep(0.1)

          self.connected = True
          self.consecutive_upload_failures = 0
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

      rotated = cv2.rotate(frame_image, cv2.ROTATE_90_CLOCKWISE)
      bgr_img = cv2.cvtColor(rotated, cv2.COLOR_RGB2BGR)

      # MCU 부하 감소를 위해 압축률을 60으로 하향 조정
      encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 60]
      success, encoded_img = cv2.imencode('.jpg', bgr_img, encode_param)

      if success:
        jpg_bytes = encoded_img.tobytes()
        size_kb = len(jpg_bytes) // 1024

        t0 = time.time()
        # The device returns an ACK for every image.  This must be read: leaving
        # ACKs in the IN endpoint fills the device queue after ~17 frames, then
        # the following OUT write blocks until its USB timeout.
        response = self._send_jpeg(self.device, jpg_bytes)
        elapsed = time.time() - t0

        if not self._resp_ok(response):
          self.consecutive_upload_failures += 1
          flog(f"[CLUSTER_USB_WARN] JPEG upload was not acknowledged "
               f"({self.consecutive_upload_failures}/3); skipping frame.")
          # A single stale/late response is recoverable. Reconnecting on every
          # miss causes an endless reset loop and makes recovery impossible.
          if self.consecutive_upload_failures >= 3:
            raise usb.core.USBError("JPEG upload was not acknowledged 3 times")
          return

        self.consecutive_upload_failures = 0

        self.frame_count += 1
        if self.frame_count <= 20 or self.frame_count % self.config.fps == 0:
          flog(f"[CLUSTER_USB_TX] frame#{self.frame_count} | Size: {size_kb} KB | elapsed={elapsed:.3f}s (ACK received)")

    except usb.core.USBError as e:
      if e.errno == 110 or 'timed out' in str(e).lower():
        # [복구] 타임아웃 발생 시 전체 재연결(흰화면/깜빡임)을 막기 위해
        # 연결을 끊지 않고 파이프라인 락(Stall)만 해제(Soft-Recovery)합니다.
        flog(f"[CLUSTER_USB_WARN] Write timeout (Errno 110). Clearing halt & skipping frame...")
        try:
          if self._ep_out and self.device:
            self.device.clear_halt(self._ep_out)
        except Exception as clear_err:
          flog(f"[CLUSTER_USB_WARN] Failed clear_halt: {clear_err}")
        # self.connected = False 를 삭제하여 재연결 방지 (매우 중요)
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
