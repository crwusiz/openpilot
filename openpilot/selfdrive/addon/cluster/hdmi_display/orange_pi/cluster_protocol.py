import select
import struct
import time
from dataclasses import dataclass


PROTOCOL_VERSION = 1
FRAME_MAGIC = b"OPCF"
ACK_MAGIC = b"OPCA"
MAX_FRAME_SIZE = 4 * 1024 * 1024

ACK_OK = 0
ACK_DISPLAY_ERROR = 1
ACK_PROTOCOL_ERROR = 2
FRAME_QUERY_STREAM = 1
FRAME_STREAM = 2
ACK_STREAM_SUPPORTED = 1
ACK_SCREEN_OFF = 2

FRAME_HEADER = struct.Struct("!4sBB2xII")
ACK_PACKET = struct.Struct("!4sBB2xIB3x")


@dataclass
class ReceiveStats:
  # Wall durations include thread scheduling/GIL reacquisition, not just I/O.
  wait_time: float = 0.0
  wait_max: float = 0.0
  read_calls: int = 0
  read_max: float = 0.0
  select_timeouts: int = 0


def unpack_frame_header(data: bytes) -> tuple[int, int]:
  sequence, frame_size, _flags = unpack_frame_header_info(data)
  return sequence, frame_size


def unpack_frame_header_info(data: bytes) -> tuple[int, int, int]:
  magic, version, flags, sequence, frame_size = FRAME_HEADER.unpack(data)
  if magic != FRAME_MAGIC or version != PROTOCOL_VERSION:
    raise ValueError("Unsupported cluster frame protocol")
  if not 0 < frame_size <= MAX_FRAME_SIZE:
    raise ValueError(f"Invalid cluster frame size: {frame_size}")
  return sequence, frame_size, flags


def pack_ack(sequence: int, status: int = ACK_OK, flags: int = 0) -> bytes:
  return ACK_PACKET.pack(ACK_MAGIC, PROTOCOL_VERSION, flags, sequence, status)


def recv_exact(sock, size: int, *, deadline: float | None = None, poll_events=None, stats: ReceiveStats | None = None) -> bytes:
  data = bytearray(size)
  view = memoryview(data)
  received = 0
  while received < size:
    if poll_events is not None:
      poll_events()
    if deadline is not None:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise TimeoutError("Timed out waiting for a complete cluster frame")
      # Preserve partial packets while keeping SDL responsive during a stall.
      wait_started = time.monotonic() if stats is not None else 0.0
      readable, _, _ = select.select([sock], [], [], min(remaining, 0.05))
      if stats is not None:
        wait_elapsed = time.monotonic() - wait_started
        stats.wait_time += wait_elapsed
        stats.wait_max = max(stats.wait_max, wait_elapsed)
        stats.select_timeouts += not readable
      if not readable:
        continue
    read_started = time.monotonic() if stats is not None else 0.0
    count = sock.recv_into(view[received:])
    if stats is not None:
      stats.read_calls += 1
      stats.read_max = max(stats.read_max, time.monotonic() - read_started)
    if count == 0:
      raise ConnectionError("C4 connection closed while receiving data")
    received += count
  return bytes(data)
