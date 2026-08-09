import numpy as np
import os
import time
from fractions import Fraction
import cv2
import usb.util
import usb.core
import av
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
    self._write_to_device = None
    self._h264_streaming = False
    self._h264_encoder = None
    self._h264_chunk_size = 202752
    self._h264_frame_count = 0
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
        build_command_packet_header, encrypt_command_packet, write_to_device,
        CMD_UPLOAD_JPEG, CMD_PLAY_H264_CHUNK, CMD_GET_H264_CHUNK_SIZE,
        CMD_STOP_STREAM,
      )

      self._find_usb_device = find_usb_device
      self._send_image = send_image
      self._send_jpeg = send_jpeg
      self._resp_ok = _resp_ok
      self._build_header = build_command_packet_header
      self._encrypt = encrypt_command_packet
      self._cmd_upload_jpeg = CMD_UPLOAD_JPEG
      self._write_to_device = write_to_device
      self._cmd_play_h264_chunk = CMD_PLAY_H264_CHUNK
      self._cmd_get_h264_chunk_size = CMD_GET_H264_CHUNK_SIZE
      self._cmd_stop_stream = CMD_STOP_STREAM

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

          if getattr(self.config, 'use_h264_stream', False):
            self._start_h264_stream()

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

  def _send_command(self, command, payload=b'', last_chunk=False):
    packet = self._build_header(command)
    payload_size = len(payload)
    packet[8] = (payload_size >> 24) & 0xFF
    packet[9] = (payload_size >> 16) & 0xFF
    packet[10] = (payload_size >> 8) & 0xFF
    packet[11] = payload_size & 0xFF
    if last_chunk:
      packet[12] = 1
    return self._write_to_device(self.device, self._encrypt(packet) + payload)

  def _start_h264_stream(self):
    """Enable the panel's video decoder for flicker-free live updates."""
    try:
      # Same playback-mode sequence used by the vendor library's send_video().
      for command in (111, 112, 13, 41):
        self._send_command(command)

      response = self._send_command(self._cmd_get_h264_chunk_size)
      if response and len(response) >= 12:
        negotiated = int.from_bytes(response[8:12], byteorder='big', signed=False)
        if 0 < negotiated <= 1024 * 1024:
          self._h264_chunk_size = negotiated

      width, height = self.config.height, self.config.width
      encoder = av.CodecContext.create('libx264', 'w')
      encoder.width = width
      encoder.height = height
      encoder.pix_fmt = 'yuv420p'
      encoder.time_base = Fraction(1, self.config.usb_fps)
      encoder.framerate = Fraction(self.config.usb_fps, 1)
      encoder.options = {
        'preset': 'ultrafast', 'tune': 'zerolatency', 'profile': 'baseline',
        'g': str(self.config.usb_fps), 'keyint_min': str(self.config.usb_fps),
        'sc_threshold': '0', 'annexb': '1',
      }
      encoder.open()
      self._h264_encoder = encoder
      self._h264_frame_count = 0
      self._h264_streaming = True
      flog(f"[CLUSTER_USB] H.264 stream enabled (chunk={self._h264_chunk_size}).")
    except Exception as e:
      self._h264_streaming = False
      self._h264_encoder = None
      flog(f"[CLUSTER_USB_WARN] H.264 stream unavailable; using JPEG fallback: {e}")

  def _send_h264_frame(self, frame_image):
    rotated = cv2.rotate(frame_image, cv2.ROTATE_90_CLOCKWISE)
    video_frame = av.VideoFrame.from_ndarray(rotated, format='rgb24')
    video_frame.pts = self._h264_frame_count
    self._h264_frame_count += 1

    for encoded_packet in self._h264_encoder.encode(video_frame):
      encoded = bytes(encoded_packet)
      for offset in range(0, len(encoded), self._h264_chunk_size):
        response = self._send_command(
          self._cmd_play_h264_chunk, encoded[offset:offset + self._h264_chunk_size])
        if response is None:
          raise RuntimeError('H.264 chunk was not acknowledged')

  def send_image(self, frame_image):
    if not self.connected or self.device is None:
      return

    try:
      if not isinstance(frame_image, np.ndarray):
        frame_image = np.array(frame_image)

      if self._h264_streaming:
        t0 = time.time()
        try:
          self._send_h264_frame(frame_image)
          elapsed = time.time() - t0
          self.frame_count += 1
          if self.frame_count <= 20 or self.frame_count % self.config.usb_fps == 0:
            flog(f"[CLUSTER_USB_TX] H264 frame#{self.frame_count} | elapsed={elapsed:.3f}s")
          return
        except Exception as e:
          # Preserve a working display if this unit's firmware does not accept
          # the live H.264 variant of the protocol.
          self._h264_streaming = False
          self._h264_encoder = None
          flog(f"[CLUSTER_USB_WARN] H.264 stream failed; switching to JPEG: {e}")

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
    if self._h264_streaming and self.device is not None:
      try:
        self._send_command(self._cmd_stop_stream)
      except Exception:
        pass
    self._h264_streaming = False
    self._h264_encoder = None
    self.connected = False
    self.device = None
    self.dev_pid = None
