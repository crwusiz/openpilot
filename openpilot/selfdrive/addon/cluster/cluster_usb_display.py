from io import BytesIO
import time
from typing import NamedTuple

import cv2
import numpy as np
from PIL import Image
import usb.util
import usb.core

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
TURZX_BRIGHTNESS_PERCENT = 100


class PreparedFrame(NamedTuple):
  jpeg: memoryview
  size_kb: int
  prepare_elapsed: float


class TuringUsbDisplay:
  def __init__(self, config):
    self.config = config
    self.device = None
    self.dev_pid = None
    self.connected = False
    self.product_id = None
    self.frame_count = 0
    self.consecutive_upload_failures = 0
    self.jpeg_quality = min(max(int(getattr(config, "usb_jpeg_quality", 68)), 1), 95)
    # Baseline, non-optimized JPEG is the fastest path for continuously
    # changing camera frames. 68 matches carrot-pilot's stable USB default.
    self._encode_param = [
      int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality,
      int(cv2.IMWRITE_JPEG_PROGRESSIVE), 0,
      int(cv2.IMWRITE_JPEG_OPTIMIZE), 0,
    ]
    self._perf_started = None
    self._perf_frames = 0
    self._perf_prepare_time = 0.0
    self._perf_usb_time = 0.0

    self._find_usb_device = None
    self._send_jpeg = None
    self._resp_ok = None
    self._ep_out = None
    self._ep_in = None
    flog(f"TuringUsbDisplay initialized (JPEG quality={self.jpeg_quality}).")

  def _disconnect(self):
    device = self.device
    self.connected = False
    self.device = None
    self.dev_pid = None
    self.product_id = None
    self._ep_out = None
    self._ep_in = None
    if device is not None:
      try:
        usb.util.dispose_resources(device)
      except Exception:
        pass

  def open(self):
    if self.connected:
      return True

    # Release a partially initialized or failed handle before rediscovery.
    self._disconnect()

    flog("Attempting to connect to Turing USB Display...")

    try:
      from library.lcd.lcd_comm_turing_usb import (
        find_usb_device, send_jpeg, send_sync_command,
        send_brightness_command, send_frame_rate_command, _resp_ok,
      )

      self._find_usb_device = find_usb_device
      self._send_jpeg = send_jpeg
      self._resp_ok = _resp_ok

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
          self._ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

          if self._ep_out is None or self._ep_in is None:
            raise RuntimeError("Turing USB endpoints were not found")

          sync_ok = False
          for attempt in range(3):
            flog(f"[CLUSTER_USB] Sending sync handshake (Attempt {attempt+1})...")
            resp = send_sync_command(self.device)
            sync_ok = self._resp_ok(resp)
            flog(f"[CLUSTER_USB] Sync response: ok={sync_ok}")
            if sync_ok:
              break
            time.sleep(0.3)
          if not sync_ok:
            raise usb.core.USBError("Turing USB sync handshake failed")
          time.sleep(0.3)

          # The protocol uses 0..102 for 0..100% backlight brightness. Without
          # this command the panel keeps its previous/default (often dim) level.
          brightness = round(TURZX_BRIGHTNESS_PERCENT * 102 / 100)
          resp = send_brightness_command(self.device, brightness)
          flog(f"[CLUSTER_USB] Brightness set to {TURZX_BRIGHTNESS_PERCENT}%: "
               f"ok={self._resp_ok(resp)}")
          time.sleep(0.1)

          resp = send_frame_rate_command(self.device, self.config.usb_fps)
          flog(f"[CLUSTER_USB] Frame rate response: ok={self._resp_ok(resp)}")
          time.sleep(0.1)

          self.connected = True
          self.consecutive_upload_failures = 0
          flog(f"[CLUSTER_USB_SUCCESS] Connected to TURZX 9.2 inch (PID: {hex(self.product_id)}).")
          return True
        else:
          flog(f"[CLUSTER_USB_ERROR] Unsupported PID: {hex(self.product_id)}")
          self._disconnect()
          return False
      else:
        flog("[CLUSTER_USB_ERROR] Turing USB Display not found.")
        return False

    except Exception as e:
      flog(f"[CLUSTER_USB_ERROR] Error opening Turing display: {e}")
      self._disconnect()
      return False

  def prepare_image(self, frame_image):
    """Rotate and encode a frame without touching the USB device."""
    try:
      prepare_started = time.monotonic()
      if isinstance(frame_image, Image.Image):
        # Renderer output is already RGB/PIL. Keeping it in Pillow removes the
        # PIL -> NumPy copy plus OpenCV's rotate and RGB -> BGR passes.
        rotation = Image.Transpose.ROTATE_90 if getattr(self.config, "rotate_180", False) \
                   else Image.Transpose.ROTATE_270
        rotated = frame_image.transpose(rotation)
        encoded_buffer = BytesIO()
        rotated.save(encoded_buffer, format="JPEG", quality=self.jpeg_quality,
                     progressive=False, optimize=False, subsampling=2)
        jpg_data = encoded_buffer.getbuffer()
        success = True
      else:
        if not isinstance(frame_image, np.ndarray):
          frame_image = np.asarray(frame_image)

        # Compatibility path used by shutdown's generated black NumPy frame.
        rotation = cv2.ROTATE_90_COUNTERCLOCKWISE if getattr(self.config, "rotate_180", False) \
                   else cv2.ROTATE_90_CLOCKWISE
        rotated = cv2.rotate(frame_image, rotation)
        cv2.cvtColor(rotated, cv2.COLOR_RGB2BGR, dst=rotated)
        success, encoded_img = cv2.imencode('.jpg', rotated, self._encode_param)
        jpg_data = memoryview(encoded_img) if success else None

      if not success or jpg_data is None:
        flog("[CLUSTER_USB_ERROR] JPEG encoding failed; skipping frame.")
        return None

      # The memoryview owns a reference to the Pillow/OpenCV backing buffer,
      # keeping it valid until the USB sender finishes with this frame.
      return PreparedFrame(
        jpeg=jpg_data,
        size_kb=len(jpg_data) // 1024,
        prepare_elapsed=time.monotonic() - prepare_started,
      )
    except Exception as e:
      # Encoding failures are independent of the USB connection and must not
      # force a device reconnect.
      flog(f"[CLUSTER_USB_ERROR] Failed to encode display frame: {e}")
      return None

  def send_prepared(self, prepared):
    """Upload an already encoded frame and account for transport performance."""
    if not self.connected or self.device is None or prepared is None:
      return False

    try:
      t0 = time.monotonic()
      # The device returns an ACK for every image.  This must be read: leaving
      # ACKs in the IN endpoint fills the device queue after ~17 frames, then
      # the following OUT write blocks until its USB timeout.
      response = self._send_jpeg(
        self.device, prepared.jpeg, ep_out=self._ep_out, ep_in=self._ep_in,
        timeout=self.config.usb_image_timeout_ms,
      )
      elapsed = time.monotonic() - t0

      if not self._resp_ok(response):
        self.consecutive_upload_failures += 1
        flog(f"[CLUSTER_USB_WARN] JPEG upload was not acknowledged "
             f"({self.consecutive_upload_failures}/3); skipping frame.")
        # A single stale/late response is recoverable. Reconnecting on every
        # miss causes an endless reset loop and makes recovery impossible.
        if self.consecutive_upload_failures >= 3:
          raise usb.core.USBError("JPEG upload was not acknowledged 3 times")
        return False

      self.consecutive_upload_failures = 0

      self.frame_count += 1
      now = time.monotonic()
      if self._perf_started is None:
        self._perf_started = now
      self._perf_frames += 1
      self._perf_prepare_time += prepared.prepare_elapsed
      self._perf_usb_time += elapsed
      # Avoid 20 synchronous file opens/writes during startup. Periodic
      # telemetry remains sufficient for diagnosing throughput and stalls.
      if self.frame_count == 1 or self.frame_count % self.config.fps == 0:
        flog(f"[CLUSTER_USB_TX] frame#{self.frame_count} | Size: {prepared.size_kb} KB | "
             f"elapsed={elapsed:.3f}s | prep={prepared.prepare_elapsed * 1000:.1f}ms (ACK received)")

      if self._perf_frames >= self.config.fps * 10:
        perf_elapsed = max(now - self._perf_started, 1e-6)
        flog(f"[CLUSTER_USB_PERF] fps={self._perf_frames / perf_elapsed:.2f} | "
             f"prep_avg={self._perf_prepare_time * 1000 / self._perf_frames:.1f}ms | "
             f"usb_avg={self._perf_usb_time * 1000 / self._perf_frames:.1f}ms")
        self._perf_started = now
        self._perf_frames = 0
        self._perf_prepare_time = 0.0
        self._perf_usb_time = 0.0

      return True

    except usb.core.USBError as e:
      if e.errno == 110 or 'timed out' in str(e).lower():
        flog(f"[CLUSTER_USB_WARN] Write timeout (Errno 110). Clearing halt & skipping frame...")
        try:
          if self._ep_out and self.device:
            self.device.clear_halt(self._ep_out)
        except Exception as clear_err:
          flog(f"[CLUSTER_USB_WARN] Failed clear_halt: {clear_err}")
      else:
        err_msg = f"Critical USB Error: {e}"
        flog(f"[CLUSTER_USB_ERROR] {err_msg}")
        self._disconnect()
    except Exception as e:
      err_msg = f"Failed to send image to Turing display: {e}"
      flog(f"[CLUSTER_USB_ERROR] {err_msg}")
      self._disconnect()
    return False

  def send_image(self, frame_image):
    """Synchronous compatibility path used for display cleanup."""
    if not self.connected or self.device is None:
      return False
    return self.send_prepared(self.prepare_image(frame_image))

  def close(self):
    flog("Closing Turing connection.")
    self._disconnect()
