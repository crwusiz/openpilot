"""Bounded signaling and scene state; H.264 video itself travels over WebRTC."""
import json
import math
import struct
import zlib

H264_MAGIC = b"OPCH"
CONTROL_HEADER = struct.Struct("!4sBB2xII")
PROTOCOL_VERSION = 1
MAX_CONTROL_SIZE = 64 * 1024
HELLO, OFFER, ANSWER, STATE, FEEDBACK, ERROR, STATE_ZLIB = range(1, 8)
KINDS = frozenset((HELLO, OFFER, ANSWER, STATE, FEEDBACK, ERROR, STATE_ZLIB))
PATH_KEYS = ("path_x", "path_y", "path_z", "lane_lines", "lane_line_probs", "road_edges", "road_edge_stds",
             "leads", "camera_height", "calib_transform")


def _check_value(value, depth=0):
  if depth > 7:
    raise ValueError("Cluster state nesting exceeds limit")
  if value is None or isinstance(value, bool):
    return
  if isinstance(value, (int, float)):
    if abs(value) > 1e30 or not math.isfinite(value):
      raise ValueError("Cluster state has invalid numeric data")
  elif isinstance(value, str):
    if len(value) > MAX_CONTROL_SIZE:
      raise ValueError("Cluster control string exceeds limit")
  elif isinstance(value, list):
    if len(value) > 128:
      raise ValueError("Cluster state array exceeds limit")
    for item in value:
      _check_value(item, depth + 1)
  elif isinstance(value, dict):
    if len(value) > 96:
      raise ValueError("Cluster state object exceeds limit")
    for key, item in value.items():
      if not isinstance(key, str) or len(key) > 96:
        raise ValueError("Invalid cluster state field")
      _check_value(item, depth + 1)
  else:
    raise ValueError("Unsupported cluster control value")


def decode_payload(data):
  if not 0 < len(data) <= MAX_CONTROL_SIZE:
    raise ValueError("Invalid cluster control size")
  try:
    payload = json.loads(data)
    if not isinstance(payload, dict):
      raise ValueError("Cluster control must be an object")
    _check_value(payload)
  except (UnicodeError, RecursionError, OverflowError) as e:
    raise ValueError("Invalid cluster control JSON") from e
  return payload


def pack_control(kind, payload, sequence=0):
  if kind not in KINDS:
    raise ValueError("Unknown cluster control kind")
  if kind == STATE_ZLIB:
    raise ValueError("Compressed cluster state must use pack_scene")
  _check_value(payload)
  if not isinstance(payload, dict):
    raise ValueError("Cluster control must be an object")
  return _pack_validated_control(kind, payload, sequence)


def _encode_validated_payload(payload):
  # Callers must validate the whole payload before entering this private
  # encoder. Keep the JSON and encoded-byte limits here for both send paths.
  data = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
  if not 0 < len(data) <= MAX_CONTROL_SIZE:
    raise ValueError("Cluster control exceeds byte limit")
  return data


def _pack_encoded_control(kind, data, sequence):
  return CONTROL_HEADER.pack(H264_MAGIC, PROTOCOL_VERSION, kind, sequence & 0xFFFFFFFF, len(data)) + data


def _pack_validated_control(kind, payload, sequence):
  return _pack_encoded_control(kind, _encode_validated_payload(payload), sequence)


def pack_scene(state, sequence=0, *, compression=None):
  # validate_scene includes the same recursive numeric/nesting checks as
  # pack_control, plus geometry limits. Avoid walking every coordinate twice
  # on the C4's 20 Hz path while retaining the full checks before encoding.
  if compression not in (None, "zlib"):
    raise ValueError("Unsupported cluster state compression")
  data = _encode_validated_payload(validate_scene(state))
  if compression == "zlib":
    compressed = zlib.compress(data, level=1)
    if len(compressed) < len(data):
      return _pack_encoded_control(STATE_ZLIB, compressed, sequence)
  return _pack_encoded_control(STATE, data, sequence)


def decode_state_payload(kind, data):
  """Decode one bounded state; compressed input never lifts the JSON limit."""
  if kind == STATE:
    return decode_payload(data)
  if kind != STATE_ZLIB:
    raise ValueError("Expected cluster scene state")
  if not 0 < len(data) <= MAX_CONTROL_SIZE:
    raise ValueError("Invalid compressed cluster state size")
  try:
    decoder = zlib.decompressobj()
    payload = decoder.decompress(data, MAX_CONTROL_SIZE + 1)
  except zlib.error as error:
    raise ValueError("Invalid compressed cluster state") from error
  # No flush(): even malicious input may produce at most one byte beyond the
  # uncompressed budget. Reject unfinished, concatenated or trailing streams.
  if (len(payload) > MAX_CONTROL_SIZE or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data):
    raise ValueError("Invalid or oversized compressed cluster state")
  return decode_payload(payload)


def unpack_control_header(data):
  magic, version, kind, sequence, size = CONTROL_HEADER.unpack(data)
  if magic != H264_MAGIC or version != PROTOCOL_VERSION or kind not in KINDS:
    raise ValueError("Unsupported H.264 cluster protocol")
  if not 0 < size <= MAX_CONTROL_SIZE:
    raise ValueError("Invalid cluster control size")
  return kind, sequence, size


def validate_scene(state):
  """Validate geometry before a remote state can allocate drawing buffers."""
  if (not isinstance(state, dict) or not isinstance(state.get("model_valid"), bool) or
      not isinstance(state.get("hud"), dict) or not isinstance(state.get("path"), dict)):
    raise ValueError("Invalid cluster scene")
  size = state.get("source_size")
  if size is not None and (not isinstance(size, list) or len(size) != 2 or
                           any(type(v) is not int or not 2 <= v <= 8192 for v in size)):
    raise ValueError("Invalid camera source dimensions")
  hud, path = state["hud"], state["path"]
  height = path.get("camera_height")
  if height is not None and (type(height) not in (int, float) or not 0.1 <= height <= 10.0):
    raise ValueError("Invalid cluster camera height")
  for key in ("is_metric", "rotate_180"):
    if key in state and type(state[key]) is not bool:
      raise ValueError("Invalid cluster display option")
  for key, value in hud.items():
    if key == "tpms":
      if not isinstance(value, list) or len(value) != 4 or any(type(v) not in (float, int) for v in value):
        raise ValueError("Invalid tire data")
    elif value is not None and type(value) not in (float, int, bool):
      raise ValueError("Invalid cluster HUD value")
  for key in ("path_x", "path_y", "path_z", "lane_line_probs", "road_edge_stds"):
    values = path.get(key, [])
    if not isinstance(values, list) or len(values) > 64 or any(type(v) not in (float, int) for v in values):
      raise ValueError("Invalid cluster path coordinates")
  for key, limit in (("lane_lines", 4), ("road_edges", 2)):
    lines = path.get(key, [])
    if not isinstance(lines, list) or len(lines) > limit:
      raise ValueError("Invalid cluster line count")
    for line in lines:
      if not isinstance(line, list) or len(line) != 3:
        raise ValueError("Invalid cluster line")
      for coordinates in line:
        if not isinstance(coordinates, list) or len(coordinates) > 64 or any(type(v) not in (float, int) for v in coordinates):
          raise ValueError("Invalid cluster line coordinates")
      if len({len(axis) for axis in line}) != 1:
        raise ValueError("Cluster line axes differ in length")
  matrix = path.get("calib_transform")
  if matrix is not None and (not isinstance(matrix, list) or len(matrix) != 3 or any(
      not isinstance(row, list) or len(row) != 3 or any(type(v) not in (float, int) for v in row) for row in matrix)):
    raise ValueError("Invalid cluster calibration")
  leads = path.get("leads", [])
  if not isinstance(leads, list) or len(leads) > 2 or any(not isinstance(lead, dict) for lead in leads):
    raise ValueError("Invalid cluster leads")
  for lead in leads:
    if any(type(value) not in (bool, int, float) for value in lead.values()):
      raise ValueError("Invalid cluster lead data")
  if len({len(path.get(axis, [])) for axis in ("path_x", "path_y", "path_z")}) != 1:
    raise ValueError("Cluster path axes differ in length")
  _check_value(state)
  return state
