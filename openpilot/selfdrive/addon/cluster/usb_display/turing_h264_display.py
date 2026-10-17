"""Bounded, live Annex-B H.264 transport for the TURZX 9.2 inch panel."""

import binascii
import struct
import sys
import threading
import time
import zlib

import usb.core
import usb.util

from openpilot.selfdrive.addon.cluster.cluster_logging import flog

VENDOR_ID = 0x1CBE
PRODUCT_ID = 0x0092
CHUNK_SIZE = 32 * 1024
MAX_ACCESS_UNIT_BYTES = 1024 * 1024
SETUP_GAPS = (0.020, 0.020, 0.080, 0.125, 0.360, 0.025)


def _overlay_png(alpha):
  # Match the controller's macroblock-aligned image layer. The encoded SPS
  # retains the actual 462x1920 display crop; its coded width is 464 pixels.
  def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)

  return b"".join((
    b"\x89PNG\r\n\x1a\n",
    chunk(b"IHDR", struct.pack(">IIBBBBB", 464, 1920, 8, 6, 0, 0, 0)),
    chunk(b"IDAT", zlib.compress((b"\x00" + bytes((0, 0, 0, alpha)) * 464) * 1920, 4)),
    chunk(b"IEND", b""),
  ))


def transparent_overlay_png():
  return _overlay_png(0)


def black_overlay_png():
  return _overlay_png(255)


class TuringH264Display:
  def __init__(self, config):
    self.config = config
    self.connected = False
    self.device = None
    self.product_id = None
    self._ep_out = self._ep_in = None
    self._build_header = self._encrypt = self._resp_ok = None
    self._lock = threading.RLock()
    self.chunk_size = CHUNK_SIZE
    self.frame_count = 0
    self.max_frame_age = max(0.05, float(getattr(config, "usb_max_frame_age", 0.25)))
    # No-ACK matches carrot-wip's tested live H264 path. Some firmware only
    # acknowledges status commands; waiting for every 121 chunk would stall it.
    self.wait_ack = bool(getattr(config, "usb_h264_wait_ack", False))
    self.timeout_ms = min(1000, max(1, int(getattr(config, "usb_write_timeout_ms", 1000))))
    self._queue_depth = None
    self._next_status_at = 0.0
    self._status_misses = 0
    self._drained_acks = 0
    self._skipped = 0
    self._perf_started = None
    self._perf_frames = self._perf_chunks = self._perf_bytes = 0
    self._perf_usb_time = 0.0

  @staticmethod
  def _is_timeout(error):
    return isinstance(error, usb.core.USBTimeoutError) or getattr(error, "errno", None) == 110 or "timed out" in str(error).lower()

  def _disconnect(self):
    device, self.device = self.device, None
    self.connected = False
    self.product_id = None
    self._ep_out = self._ep_in = None
    self._queue_depth = None
    if device is not None:
      try:
        usb.util.dispose_resources(device)
      except Exception:
        pass

  def _drain_input(self, attempts=8):
    # Consume unsolicited/late ACKs even in no-ACK mode: the panel's IN queue
    # must never fill. This is bounded; do not use the vendor's 100ms flush.
    for _ in range(attempts):
      try:
        response = bytes(self._ep_in.read(512, timeout=1))
      except usb.core.USBError as error:
        if self._is_timeout(error):
          return
        raise
      if not response:
        return
      self._drained_acks += 1

  def _payload(self, command, data=b"", fields=None):
    header = self._build_header(command)
    if data:
      header[8:12] = len(data).to_bytes(4, "big")
    if fields:
      for index, value in fields.items():
        header[index] = value
    # In a live stream byte 12 stays zero, including the final chunk of each
    # access unit. Setting it means end-of-stream, rather than end-of-frame.
    return bytes(self._encrypt(header)) + data

  def _write(self, payload, timeout_ms):
    written = self._ep_out.write(payload, timeout=timeout_ms)
    if written != len(payload):
      raise usb.core.USBError(f"Incomplete H264 USB write: {written}/{len(payload)} bytes")

  def _setup(self):
    # Stop any prior stream without resetting the device or hub.
    self._write(self._payload(123), self.timeout_ms)
    self._drain_input()
    setup = ((10, None), (111, None), (112, None), (13, {8: 0}), (14, {8: 102}), (52, None))
    for (command, fields), gap in zip(setup, SETUP_GAPS, strict=True):
      self._write(self._payload(command, fields=fields), self.timeout_ms)
      time.sleep(gap)
      self._drain_input()
    self._write(self._payload(102, transparent_overlay_png()), self.timeout_ms)
    time.sleep(0.070)
    self._drain_input()
    fps = min(60, max(1, int(getattr(self.config, "usb_h264_fps", getattr(self.config, "usb_fps", 20)))))
    self._write(self._payload(15, fields={8: fps}), self.timeout_ms)
    time.sleep(0.015)
    self._drain_input()

  def open(self):
    with self._lock:
      if self.connected:
        return True
      self._disconnect()
      try:
        from openpilot.selfdrive.addon.cluster.usb_display.turing_protocol import build_command_packet_header, encrypt_command_packet, response_ok

        self._build_header, self._encrypt, self._resp_ok = build_command_packet_header, encrypt_command_packet, response_ok
        self.device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.device is None:
          flog("[CLUSTER_USB_H264_ERROR] TURZX 9.2 inch display not found.")
          return False
        self.product_id = getattr(self.device, "idProduct", PRODUCT_ID)
        if self.product_id != PRODUCT_ID:
          raise usb.core.USBError("Unsupported H264 USB display product ID")
        # Detach before configure/claim. Never issue device/hub resets: C4's
        # driving model can share the same xHCI controller through an eGPU.
        if sys.platform.startswith("linux"):
          try:
            if self.device.is_kernel_driver_active(0):
              self.device.detach_kernel_driver(0)
          except usb.core.USBError as error:
            if error.errno != 2:
              raise
        self.device.set_configuration()
        usb.util.claim_interface(self.device, 0)
        interface = usb.util.find_descriptor(self.device.get_active_configuration(), bInterfaceNumber=0)
        if interface is None:
          raise usb.core.USBError("TURZX interface 0 not found")
        self._ep_out = usb.util.find_descriptor(
          interface, custom_match=lambda endpoint: usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_OUT,
        )
        self._ep_in = usb.util.find_descriptor(
          interface, custom_match=lambda endpoint: usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN,
        )
        if self._ep_out is None or self._ep_in is None:
          raise usb.core.USBError("TURZX H264 endpoints not found")
        self._setup()
        self._queue_depth = None
        self._next_status_at = 0.0
        self.connected = True
        flog(f"[CLUSTER_USB_H264_SUCCESS] Connected | portrait=462x1920 | chunk={self.chunk_size}B | ack={self.wait_ack}")
        return True
      except Exception as error:
        flog(f"[CLUSTER_USB_H264_ERROR] Opening display failed: {error}")
        self._disconnect()
        return False

  def _poll_status(self, now):
    if now < self._next_status_at:
      return
    self._drain_input()
    self._write(self._payload(122), min(20, self.timeout_ms))
    try:
      response = bytes(self._ep_in.read(512, timeout=min(20, self.timeout_ms)))
    except usb.core.USBError as error:
      if not self._is_timeout(error):
        raise
      response = b""
    # Queue depth is defined for command 122 only; a late image ACK with 0xC8
    # at byte 8 is not a queue measurement. Missing status is optional on the
    # live firmware and must not trigger an endless reconnect cycle.
    previous_depth = self._queue_depth
    if len(response) > 8 and response[1] == 0xC8 and response[8] != 0xC8:
      self._queue_depth = response[8]
    else:
      self._queue_depth = None
      self._status_misses += 1
    if self._queue_depth is not None and self._queue_depth > 3 and self._queue_depth != previous_depth:
      flog(f"[CLUSTER_USB_H264_BACKPRESSURE] queue={self._queue_depth}; skipping independent frames until the queue drains.")
    self._next_status_at = now + (0.05 if self._queue_depth is not None and self._queue_depth > 3 else 1.0)

  def send_access_unit(self, data, *, created_at=None, keyframe=True):
    """Submit one independent Annex-B frame; None means skipped before write."""
    with self._lock:
      if not self.connected or self.device is None or not data:
        return False
      if not keyframe or len(data) > MAX_ACCESS_UNIT_BYTES:
        flog("[CLUSTER_USB_H264_ERROR] Expected an independent H264 frame no larger than 1 MiB.")
        return False
      started = time.monotonic()
      if created_at is not None and started - created_at > self.max_frame_age:
        self._skipped += 1
        return None
      try:
        self._poll_status(started)
        if self._queue_depth is not None and self._queue_depth > 3:
          self._skipped += 1
          return None
        # Polling can consume the freshness budget; never start a stale AU.
        if created_at is not None and time.monotonic() - created_at > self.max_frame_age:
          self._skipped += 1
          return None
        deadline = started + self.timeout_ms / 1000.0
        chunks = 0
        for offset in range(0, len(data), self.chunk_size):
          remaining_ms = int((deadline - time.monotonic()) * 1000)
          if remaining_ms <= 0:
            raise usb.core.USBTimeoutError("H264 access-unit upload timed out", errno=110)
          self._drain_input()
          self._write(self._payload(121, data[offset:offset + self.chunk_size]), min(250, remaining_ms))
          if self.wait_ack:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            response = bytes(self._ep_in.read(512, timeout=min(250, remaining_ms)))
            if len(response) <= 8 or not self._resp_ok(response):
              raise usb.core.USBError("H264 chunk was not acknowledged")
          else:
            self._drain_input()
          chunks += 1
          # Keep individual OUT bursts small for the shared eGPU bus. No sleep
          # after the final chunk, and no sleeping on queue-depth backpressure.
          if offset + self.chunk_size < len(data):
            time.sleep(0.002)
        self.frame_count += 1
        self._record_perf(started, len(data), chunks)
        return True
      except Exception as error:
        # A partially sent AU cannot be retried as if it were a whole frame.
        # Reopen performs a stop/setup and starts with a new independent frame.
        flog(f"[CLUSTER_USB_H264_ERROR] Upload failed; reopening without USB reset: {error}")
        self._disconnect()
        return False

  def _record_perf(self, started, size, chunks):
    now = time.monotonic()
    if self._perf_started is None:
      self._perf_started = started
    self._perf_frames += 1
    self._perf_chunks += chunks
    self._perf_bytes += size
    self._perf_usb_time += now - started
    interval = max(1.0, float(getattr(self.config, "status_interval_s", 10.0)))
    if self.frame_count == 1:
      flog(f"[CLUSTER_USB_H264_TX] frame#1 | Size: {size / 1024:.1f}KB | chunks={chunks} | usb={now - started:.3f}s")
    if now - self._perf_started >= interval:
      elapsed = max(now - self._perf_started, 1e-6)
      flog(
        f"[CLUSTER_USB_H264_PERF] submitted_fps={self._perf_frames / elapsed:.2f} | "
        + f"size_avg={self._perf_bytes / self._perf_frames / 1024:.1f}KB | "
        + f"usb_avg={self._perf_usb_time * 1000 / self._perf_frames:.1f}ms | chunks={self._perf_chunks} | "
        + f"queue={self._queue_depth if self._queue_depth is not None else 'unknown'} | skipped={self._skipped} | "
        + f"ack_drained={self._drained_acks} | status_misses={self._status_misses}",
      )
      self._perf_started = now
      self._perf_frames = self._perf_chunks = self._perf_bytes = 0
      self._perf_usb_time = 0.0

  def close(self):
    with self._lock:
      if self.connected and self.device is not None:
        # The controller may retain its final video frame after stop. Cover
        # the image layer with opaque black; a transparent overlay would expose
        # that old frame. This does not need a running encoder at shutdown.
        black = black_overlay_png()
        deadline = time.monotonic() + min(100, self.timeout_ms) / 1000.0
        for command, data in ((123, b""), (102, black)):
          remaining_ms = int((deadline - time.monotonic()) * 1000)
          if remaining_ms <= 0:
            flog("[CLUSTER_USB_H264_WARN] Display shutdown deadline reached.")
            break
          try:
            self._write(self._payload(command, data), remaining_ms)
            self._drain_input()
          except Exception as error:
            flog(f"[CLUSTER_USB_H264_WARN] Shutdown command {command} failed: {error}")
      self._disconnect()
