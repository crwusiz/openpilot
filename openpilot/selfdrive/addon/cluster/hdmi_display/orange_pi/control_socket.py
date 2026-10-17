"""Deadline-bounded signaling reads that keep SDL touch events responsive."""

import select
import time
from dataclasses import dataclass


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
      # select may release the GIL until after the deadline. A late header
      # must not turn an expired idle wait into a fresh completion budget.
      now = time.monotonic()
      if now >= deadline:
        raise timed_out(now)
    read_started = time.monotonic() if stats is not None else 0.0
    count = sock.recv_into(view[received:])
    read_ended = time.monotonic() if stats is not None or deadline is not None or timeout_from_first_byte is not None else 0.0
    if stats is not None:
      stats.read_calls += 1
      stats.read_max = max(stats.read_max, read_ended - read_started)
    if count == 0:
      raise ConnectionError(f"C4 connection closed while receiving data: phase={phase} bytes={received}/{size}")
    # recv_into can also return after its idle budget due thread scheduling.
    # Check the original deadline before the first byte can reset that budget.
    if first_byte_at is None and timeout_from_first_byte is not None and deadline is not None and read_ended >= deadline:
      raise timed_out(read_ended)
    received += count
    last_progress_at = read_ended
    if first_byte_at is None:
      first_byte_at = read_ended
      if stats is not None:
        stats.first_byte_at = first_byte_at
      if timeout_from_first_byte is not None:
        # Idle waiting is separate from the signaling message completion budget.
        # Reset exactly once; progress must never extend a partial frame forever.
        deadline = first_byte_at + timeout_from_first_byte
    if deadline is not None and read_ended > deadline:
      raise timed_out(read_ended)
  return bytes(data)
