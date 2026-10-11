import select
import struct
import time


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

# Use one formerly reserved byte for capability flags. Packet sizes, sequence
# offsets and statuses remain compatible with version-1 receivers/senders.
FRAME_HEADER = struct.Struct("!4sBB2xII")
ACK_PACKET = struct.Struct("!4sBB2xIB3x")


def pack_frame_header(sequence: int, frame_size: int, flags: int = 0) -> bytes:
  if not 0 < frame_size <= MAX_FRAME_SIZE:
    raise ValueError(f"Invalid cluster frame size: {frame_size}")
  return FRAME_HEADER.pack(FRAME_MAGIC, PROTOCOL_VERSION, flags, sequence, frame_size)


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


def unpack_ack(data: bytes) -> tuple[int, int]:
  sequence, status, _flags = unpack_ack_info(data)
  return sequence, status


def unpack_ack_info(data: bytes) -> tuple[int, int, int]:
  magic, version, flags, sequence, status = ACK_PACKET.unpack(data)
  if magic != ACK_MAGIC or version != PROTOCOL_VERSION:
    raise ValueError("Unsupported cluster ACK protocol")
  return sequence, status, flags


def recv_exact(sock, size: int, *, timeout: float | None = None) -> bytes:
  """Read one packet within a fixed budget, without changing socket timeouts."""
  data = bytearray(size)
  view = memoryview(data)
  received = 0
  deadline = None if timeout is None else time.monotonic() + timeout

  def timed_out():
    return TimeoutError(f"Cluster packet timeout: bytes={received}/{size}")

  while received < size:
    if deadline is not None:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise timed_out()
      # The stream ACK reader shares its socket with sendall. Shrinking the
      # socket timeout here would also shorten the concurrent JPEG write.
      readable, _, _ = select.select([sock], [], [], remaining)
      if not readable or time.monotonic() >= deadline:
        raise timed_out()
    count = sock.recv_into(view[received:])
    if count == 0:
      raise ConnectionError(f"Cluster connection closed while receiving data: bytes={received}/{size}")
    received += count
    if deadline is not None and time.monotonic() >= deadline:
      raise timed_out()
  return bytes(data)
