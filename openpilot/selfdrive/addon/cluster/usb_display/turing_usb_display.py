import time

import usb.util
import usb.core

from openpilot.selfdrive.addon.cluster.cluster_jpeg import ClusterJpegEncoder
from openpilot.selfdrive.addon.cluster.cluster_logging import flog

TURZX_USB_VENDOR_ID = 0x1CBE
TURZX_USB_PRODUCT_IDS = {
  0x0092: "TURZX 9.2 inch",
}
TURZX_BRIGHTNESS_PERCENT = 100


class TuringUsbDisplay:
  def __init__(self, config):
    self.config = config
    self.device = None
    self.dev_pid = None
    self.connected = False
    self.product_id = None
    self.frame_count = 0
    self.consecutive_upload_failures = 0
    self.max_consecutive_upload_failures = max(
      1, int(getattr(config, "usb_max_consecutive_failures", 3)),
    )
    self.encoder = ClusterJpegEncoder(config, transport="usb")
    self.jpeg_quality = self.encoder.jpeg_quality
    self._perf_started = None
    self._perf_frames = 0
    self._perf_prepare_time = 0.0
    self._perf_usb_time = 0.0
    self._perf_size_kb = 0

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
          flog(f"[CLUSTER_USB] Brightness set to {TURZX_BRIGHTNESS_PERCENT}%: ok={self._resp_ok(resp)}")
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
    return self.encoder.prepare_image(frame_image)

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
        flog(
          f"[CLUSTER_USB_WARN] JPEG upload was not acknowledged ({self.consecutive_upload_failures}/"
          + f"{self.max_consecutive_upload_failures}); skipping frame.",
        )
        # A single stale/late response is recoverable. Reconnecting on every
        # miss causes an endless reset loop and makes recovery impossible.
        if self.consecutive_upload_failures >= self.max_consecutive_upload_failures:
          raise usb.core.USBError(
            f"JPEG upload was not acknowledged {self.max_consecutive_upload_failures} times",
          )
        return False

      self.consecutive_upload_failures = 0

      self.frame_count += 1
      now = time.monotonic()
      if self._perf_started is None:
        self._perf_started = now
      self._perf_frames += 1
      self._perf_prepare_time += prepared.prepare_elapsed
      self._perf_usb_time += elapsed
      self._perf_size_kb += prepared.size_kb
      if self.frame_count == 1:
        flog(
          f"[CLUSTER_USB_TX] frame#{self.frame_count} | Size: {prepared.size_kb} KB | elapsed={elapsed:.3f}s | "
          + f"prep={prepared.prepare_elapsed * 1000:.1f}ms (ACK received)",
        )

      perf_interval_frames = max(1, int(getattr(self.config, "status_interval_frames", self.config.fps * 10)))
      if self._perf_frames >= perf_interval_frames:
        perf_elapsed = max(now - self._perf_started, 1e-6)
        flog(
          f"[CLUSTER_USB_PERF] fps={self._perf_frames / perf_elapsed:.2f} | "
          + f"size_avg={self._perf_size_kb / self._perf_frames:.1f}KB | "
          + f"prep_avg={self._perf_prepare_time * 1000 / self._perf_frames:.1f}ms | "
          + f"usb_avg={self._perf_usb_time * 1000 / self._perf_frames:.1f}ms",
        )
        self._perf_started = now
        self._perf_frames = 0
        self._perf_prepare_time = 0.0
        self._perf_usb_time = 0.0
        self._perf_size_kb = 0

      return True

    except usb.core.USBError as e:
      if e.errno == 110 or 'timed out' in str(e).lower():
        flog("[CLUSTER_USB_WARN] Write timeout (Errno 110). Clearing halt & skipping frame...")
        if getattr(self.config, "usb_clear_halt_on_timeout", True):
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
