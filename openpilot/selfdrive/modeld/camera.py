from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from msgq.visionipc import VisionBuf, VisionIpcClient


MAX_CAMERA_SYNC_NS = 10_000_000
CAMERA_RECV_TIMEOUT_MS = 100


@dataclass(frozen=True)
class CameraFrame:
  buf: VisionBuf
  frame_id: int
  timestamp_sof: int
  timestamp_eof: int


class CameraFrameReader:
  """Receive fresh camera pairs, advancing only the older stream to synchronize."""

  def __init__(self, main: VisionIpcClient, extra: VisionIpcClient | None, timeout_ms: int = CAMERA_RECV_TIMEOUT_MS):
    self.main = main
    self.extra = extra
    self.timeout_ms = timeout_ms
    self.main_frame: CameraFrame | None = None
    self.extra_frame: CameraFrame | None = None
    self.failed_stream: str | None = None
    self.stats: dict[str, float | int] = {}

  def _recv(self, client: VisionIpcClient, stream: str, deadline: float) -> CameraFrame | None:
    start = time.monotonic()
    remaining_ms = math.ceil((deadline - start) * 1e3)
    if remaining_ms <= 0:
      self.failed_stream = stream
      return None

    self.stats[f'camera_{stream}_recv_count'] += 1
    buf = client.recv(timeout_ms=remaining_ms)
    self.stats[f'camera_{stream}_receive_ms'] += (time.monotonic() - start) * 1e3
    if buf is None:
      self.failed_stream = stream
      return None

    # VisionIPC metadata is unchanged on a timeout. Snapshot it only after a successful receive.
    return CameraFrame(buf, client.frame_id, client.timestamp_sof, client.timestamp_eof)

  def recv(self) -> tuple[CameraFrame, CameraFrame] | None:
    self.main_frame = self.extra_frame = None
    self.failed_stream = None
    self.stats = {f'camera_{stream}_{metric}': 0 for stream in ('main', 'extra')
                  for metric in ('receive_ms', 'recv_count', 'sync_skips')}
    self.stats['camera_sync_max_ms'] = 0.0
    deadline = time.monotonic() + self.timeout_ms * 1e-3

    # Each attempt starts with a new main frame; a previous extra timestamp must not select it.
    self.main_frame = self._recv(self.main, 'main', deadline)
    if self.main_frame is None:
      return None
    if self.extra is None:
      self.extra_frame = self.main_frame
      return self.main_frame, self.extra_frame

    self.extra_frame = self._recv(self.extra, 'extra', deadline)
    if self.extra_frame is None:
      return None

    while True:
      sync_ns = self.main_frame.timestamp_sof - self.extra_frame.timestamp_sof
      self.stats['camera_sync_max_ms'] = max(self.stats['camera_sync_max_ms'], abs(sync_ns) * 1e-6)
      if abs(sync_ns) <= MAX_CAMERA_SYNC_NS:
        return self.main_frame, self.extra_frame

      # Use the current pair, rather than advancing main past the previous extra frame.
      # The shared deadline bounds recovery when cameras remain out of sync.
      if sync_ns < 0:
        self.stats['camera_main_sync_skips'] += 1
        frame = self._recv(self.main, 'main', deadline)
        if frame is None:
          return None
        self.main_frame = frame
      else:
        self.stats['camera_extra_sync_skips'] += 1
        frame = self._recv(self.extra, 'extra', deadline)
        if frame is None:
          return None
        self.extra_frame = frame
