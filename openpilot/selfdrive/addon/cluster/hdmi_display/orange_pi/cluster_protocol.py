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
  first_byte_at: float | None = None


class ReceiveTimeout(TimeoutError):
  def __init__(self, phase, received, expected, elapsed, progress_age):
    self.phase = phase
    self.received = received
    self.expected = expected
    super().__init__(f"Timed out waiting for cluster frame: phase={phase} bytes={received}/{expected} "
                     + f"elapsed={elapsed * 1000:.1f}ms progress_age={progress_age * 1000:.1f}ms")


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


def recv_exact(sock, size: int, *, deadline: float | None = None, poll_events=None, stats: ReceiveStats | None = None,
               timeout_from_first_byte: float | None = None, phase="frame") -> bytes:
  data = bytearray(size)
  view = memoryview(data)
  received = 0
  started = time.monotonic() if deadline is not None or timeout_from_first_byte is not None else 0.0
  first_byte_at = last_progress_at = None

  def timed_out(now):
    stage = "idle_header" if phase == "header" and received == 0 else f"partial_{phase}"
    return ReceiveTimeout(stage, received, size, now - started, now - (last_progress_at if last_progress_at is not None else started))

  while received < size:
    if poll_events is not None:
      poll_events()
    if deadline is not None:
      now = time.monotonic()
      remaining = deadline - now
      if remaining <= 0:
        raise timed_out(now)
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
    read_ended = time.monotonic() if stats is not None or deadline is not None or timeout_from_first_byte is not None else 0.0
    if stats is not None:
      stats.read_calls += 1
      stats.read_max = max(stats.read_max, read_ended - read_started)
    if count == 0:
      raise ConnectionError(f"C4 connection closed while receiving data: phase={phase} bytes={received}/{size}")
    received += count
    last_progress_at = read_ended
    if first_byte_at is None:
      first_byte_at = read_ended
      if stats is not None:
        stats.first_byte_at = first_byte_at
      if timeout_from_first_byte is not None:
        # Idle waiting is separate from the header+JPEG completion budget.
        # Reset exactly once; progress must never extend a partial frame forever.
        deadline = first_byte_at + timeout_from_first_byte
    if deadline is not None and read_ended > deadline:
      raise timed_out(read_ended)
  return bytes(data)
