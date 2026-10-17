"""Low-priority full-frame encoding through the C4 Qualcomm V4L2 bridge."""

from collections import OrderedDict
import ctypes
from dataclasses import dataclass
from pathlib import Path
import time

import cv2
import numpy as np

from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.usb_display.h264_bitstream import H264AccessUnitFormatter


LIBRARY = Path(__file__).resolve().parents[4] / "system/loggerd/libcluster_h264_encoder_bridge.so"
CALLBACK = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_uint32,
                           ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
MAX_PACKET_BYTES = 1024 * 1024
MAX_PENDING_FRAMES = 8


@dataclass(frozen=True)
class PreparedH264Frame:
  data: bytes
  created_at: float
  prepare_elapsed: float
  convert_elapsed: float
  encoded_at: float
  timestamp_us: int


def portrait_nv12(frame, width, height, rotate_180=False):
  """Match JPEG panel orientation, padding only the encoder's right edge."""
  rgb = np.asarray(frame)
  if rgb.dtype != np.uint8 or rgb.shape != (height, width, 3):
    raise ValueError("USB H.264 input must be the configured RGB cluster image")
  oriented = cv2.rotate(rgb, cv2.ROTATE_90_COUNTERCLOCKWISE if rotate_180 else cv2.ROTATE_90_CLOCKWISE)
  encoder_width, encoder_height = (height + 15) // 16 * 16, (width + 15) // 16 * 16
  if oriented.shape[:2] != (encoder_height, encoder_width):
    oriented = cv2.copyMakeBorder(oriented, 0, encoder_height - width, 0, encoder_width - height, cv2.BORDER_REPLICATE)
  planar = cv2.cvtColor(oriented, cv2.COLOR_RGB2YUV_I420).reshape(-1)
  luma_bytes = encoder_width * encoder_height
  chroma_bytes = luma_bytes // 4
  packed = np.empty(luma_bytes * 3 // 2, dtype=np.uint8)
  packed[:luma_bytes] = planar[:luma_bytes]
  packed[luma_bytes::2] = planar[luma_bytes:luma_bytes + chroma_bytes]
  packed[luma_bytes + 1::2] = planar[luma_bytes + chroma_bytes:]
  return packed


class HardwareH264Encoder:
  """One encoder worker calls this object; callbacks never perform USB I/O.

  Output can arrive on a later submission. PTS maps every encoded frame back
  to its own render time, so queue/stale checks never make old frames fresh.
  """
  def __init__(self, config, *, library=None, clock=time.monotonic):
    self.config, self.clock = config, clock
    self.width, self.height = int(config.width), int(config.height)
    if not (2 <= self.width <= 4096 and 2 <= self.height <= 4096) or self.width % 2 or self.height % 2:
      raise ValueError("USB H.264 needs bounded, even frame dimensions")
    self.fps = int(getattr(config, "usb_h264_fps", 20))
    self.bitrate = int(getattr(config, "usb_h264_bitrate", 4_666_667))
    if not 1 <= self.fps <= 60 or not 100_000 <= self.bitrate <= 14_000_000:
      raise ValueError("USB H.264 rate is outside the supported range")
    if int(getattr(config, "usb_h264_gop", 1)) != 1:
      raise ValueError("USB H.264 requires GOP 1 for independent queued frames")
    self.encoder_width = (self.height + 15) // 16 * 16
    self.encoder_height = (self.width + 15) // 16 * 16
    self.formatter = H264AccessUnitFormatter(self.height, self.width, self.fps)
    self.handle = None
    self._pending = OrderedDict()
    self._packets = []
    self._callback_error = None
    self._last_timestamp = 0
    self._callback = CALLBACK(self._on_packet)
    try:
      self.lib = library if library is not None else ctypes.CDLL(str(LIBRARY))
      self._configure_abi()
    except (OSError, AttributeError) as error:
      raise RuntimeError("USB H.264 hardware bridge is unavailable; rebuild openpilot on C4, "
                         + "or select USB JPEG in Dashboard") from error

  def _fn(self, suffix):
    return getattr(self.lib, "cluster_h264_encoder_bridge_" + suffix)

  def _configure_abi(self):
    pointer = ctypes.c_void_p
    signatures = {
      "create": ([ctypes.c_int] * 5 + [ctypes.c_char_p] + [ctypes.c_int] * 3, pointer),
      "open": ([pointer], ctypes.c_int),
      "close": ([pointer], None),
      "destroy": ([pointer], None),
      "last_error": ([pointer], ctypes.c_char_p),
      "encode_nv12_packed": ([pointer, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_uint64, CALLBACK, pointer], ctypes.c_int),
      "drain": ([pointer, ctypes.c_int, CALLBACK, pointer], ctypes.c_int),
    }
    for name in ("slice_max_bytes", "rate_control", "realtime_priority"):
      signatures["set_" + name] = ([pointer, ctypes.c_int], ctypes.c_int)
    for name, (arguments, result) in signatures.items():
      function = self._fn(name)
      function.argtypes, function.restype = arguments, result

  def _check(self, status, action):
    if status != 0:
      error = self._fn("last_error")(self.handle)
      detail = error.decode("utf-8", errors="replace") if isinstance(error, bytes) else str(error or "unknown encoder error")
      raise RuntimeError(f"USB H.264 {action}: {detail}")

  def open(self):
    if self.handle is not None:
      return
    device = getattr(self.config, "usb_h264_device", "/dev/v4l/by-path/platform-aa00000.qcom_vidc-video-index1")
    flog(f"[CLUSTER_USB_H264_STAGE] Opening hardware encoder device={device} coded={self.encoder_width}x{self.encoder_height}...")
    self.handle = self._fn("create")(self.encoder_width, self.encoder_height, self.fps, self.bitrate, 1,
                                     str(device).encode("utf-8"), 2, 0, 0)
    if not self.handle:
      raise RuntimeError("USB H.264 hardware encoder allocation failed")
    try:
      self._check(self._fn("set_slice_max_bytes")(self.handle, int(getattr(self.config, "usb_h264_slice_max_bytes", 4096))), "slice setup")
      self._check(self._fn("set_rate_control")(self.handle, 2), "VBR-CFR setup")
      self._check(self._fn("set_realtime_priority")(self.handle, 0), "priority setup")
      self._check(self._fn("open")(self.handle), "encoder open")
    except Exception:
      self.close()
      raise
    flog(f"[CLUSTER_USB_H264_ENCODER] backend=qcom_v4l2 | display={self.height}x{self.width} | "
         + f"coded={self.encoder_width}x{self.encoder_height} | fps={self.fps} | bitrate={self.bitrate} | "
         + "gop=1 | rate_control=vbr-cfr | realtime_priority=0")

  def _on_packet(self, data, size, _flags, timestamp_us, _codec_config, _keyframe, _opaque):
    # Never raise through a ctypes callback: propagate on the worker instead.
    try:
      if not data or not 0 < size <= MAX_PACKET_BYTES or len(self._packets) >= MAX_PENDING_FRAMES:
        raise RuntimeError("USB H.264 hardware returned an invalid/oversized packet")
      self._packets.append((int(timestamp_us), ctypes.string_at(data, size)))
    except Exception as error:
      self._callback_error = error

  def encode(self, frame, created_at):
    self.open()
    started = self.clock()
    self._packets = []
    self._callback_error = None
    previous = None
    # USB/offline backpressure can intentionally pause submissions. Poll a
    # completed old frame before calling that idle time a hardware stall.
    if self._pending and (len(self._pending) >= MAX_PENDING_FRAMES or started - next(iter(self._pending.values()))[1] > 0.5):
      self._check(self._fn("drain")(self.handle, 0, self._callback, None), "drain after pause")
      previous = self._take_output()
    if len(self._pending) >= MAX_PENDING_FRAMES or (self._pending and started - next(iter(self._pending.values()))[1] > 0.5):
      raise RuntimeError("USB H.264 hardware output stalled; no software fallback is started")
    packed = portrait_nv12(frame, self.width, self.height, getattr(self.config, "rotate_180", False))
    converted = self.clock()
    timestamp = max(self._last_timestamp + 1, int(started * 1_000_000))
    self._last_timestamp = timestamp
    self._pending[timestamp] = (created_at, started, converted - started)
    self._check(self._fn("encode_nv12_packed")(self.handle, packed.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                                               packed.nbytes, timestamp, self._callback, None), "submit")
    # A short poll can complete this frame without blocking the render or USB
    # workers. A later call may return it instead; pending PTS remains bounded.
    self._check(self._fn("drain")(self.handle, 10, self._callback, None), "drain")
    return self._take_output() or previous

  def _take_output(self):
    if self._callback_error is not None:
      raise self._callback_error
    newest = None
    for packet_timestamp, data in self._packets:
      data = self.formatter.format(data)
      if data is None:
        continue
      metadata = self._pending.pop(packet_timestamp, None)
      if metadata is None:
        raise RuntimeError("USB H.264 hardware returned an unknown frame timestamp")
      rendered_at, submitted_at, convert_elapsed = metadata
      encoded_at = self.clock()
      newest = PreparedH264Frame(data, rendered_at, encoded_at - submitted_at, convert_elapsed, encoded_at, packet_timestamp)
    self._packets = []
    return newest

  def close(self):
    handle, self.handle = self.handle, None
    if handle is not None:
      try:
        self._fn("close")(handle)
      finally:
        self._fn("destroy")(handle)
    self._pending.clear()
    self._packets.clear()
