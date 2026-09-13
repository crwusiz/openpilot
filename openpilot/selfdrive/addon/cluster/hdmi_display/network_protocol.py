import struct


PROTOCOL_VERSION = 1
FRAME_MAGIC = b"OPCF"
ACK_MAGIC = b"OPCA"
MAX_FRAME_SIZE = 4 * 1024 * 1024

ACK_OK = 0
ACK_DISPLAY_ERROR = 1
ACK_PROTOCOL_ERROR = 2

# magic, version, padding, sequence, JPEG byte length
FRAME_HEADER = struct.Struct("!4sB3xII")
# magic, version, padding, sequence, status, padding
ACK_PACKET = struct.Struct("!4sB3xIB3x")


def pack_frame_header(sequence: int, frame_size: int) -> bytes:
  if not 0 < frame_size <= MAX_FRAME_SIZE:
    raise ValueError(f"Invalid cluster frame size: {frame_size}")
  return FRAME_HEADER.pack(FRAME_MAGIC, PROTOCOL_VERSION, sequence, frame_size)


def unpack_frame_header(data: bytes) -> tuple[int, int]:
  magic, version, sequence, frame_size = FRAME_HEADER.unpack(data)
  if magic != FRAME_MAGIC or version != PROTOCOL_VERSION:
    raise ValueError("Unsupported cluster frame protocol")
  if not 0 < frame_size <= MAX_FRAME_SIZE:
    raise ValueError(f"Invalid cluster frame size: {frame_size}")
  return sequence, frame_size


def pack_ack(sequence: int, status: int = ACK_OK) -> bytes:
  return ACK_PACKET.pack(ACK_MAGIC, PROTOCOL_VERSION, sequence, status)


def unpack_ack(data: bytes) -> tuple[int, int]:
  magic, version, sequence, status = ACK_PACKET.unpack(data)
  if magic != ACK_MAGIC or version != PROTOCOL_VERSION:
    raise ValueError("Unsupported cluster ACK protocol")
  return sequence, status


def recv_exact(sock, size: int) -> bytes:
  data = bytearray(size)
  view = memoryview(data)
  received = 0
  while received < size:
    count = sock.recv_into(view[received:])
    if count == 0:
      raise ConnectionError("Cluster connection closed while receiving data")
    received += count
  return bytes(data)
