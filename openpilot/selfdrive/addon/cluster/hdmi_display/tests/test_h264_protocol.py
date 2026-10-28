import json
import math
import zlib

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_protocol as wire


def scene():
  return {"model_valid": True, "hud": {"v_ego": 12.0, "tpms": [34, 34, 35, 35]},
          "path": {"path_x": [1, 2], "path_y": [0, 0], "path_z": [0, 0],
                   "calib_transform": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}, "source_size": [1344, 760]}


def realistic_scene():
  state = scene()
  xs = [round(index * index / 10, 3) for index in range(33)]
  ys = [round(0.0008 * x * x, 3) for x in xs]
  zs = [round(-0.001 * x, 3) for x in xs]
  def line(offset):
    return [xs, [round(y + offset, 3) for y in ys], zs]
  state["hud"].update(enabled=True, lat_active=True, steering_angle=12.345, cruise_speed=100.0,
                      distance_level=3, wifi_strength=4, gps_satellites=12)
  state["path"].update(path_x=xs, path_y=ys, path_z=zs,
                       lane_lines=[line(-3.6), line(-1.8), line(1.8), line(3.6)],
                       lane_line_probs=[0.51, 0.99, 0.98, 0.49], road_edges=[line(-4.2), line(4.2)],
                       road_edge_stds=[0.1, 0.2], camera_height=1.22,
                       leads=[{"present": True, "d_rel": 22.123, "v_rel": -1.456, "y_rel": 0.234}])
  state.update(is_metric=True, rotate_180=False, state_seq=123, video_frame_id=456,
                car_state_age_ms=3.21, model_age_ms=24.567)
  return state


def test_scene_wire_is_bounded_json_with_explicit_codec_magic():
  state = scene()
  packet = wire.pack_control(wire.STATE, state, 2**32 + 3)
  assert wire.unpack_control_header(packet[:16]) == (wire.STATE, 3, len(packet) - 16)
  assert packet[:4] == b"OPCH"
  assert wire.validate_scene(wire.decode_payload(packet[16:])) == state


def test_scene_packing_preserves_wire_bytes_and_checks_numeric_tree_once(monkeypatch):
  state = scene()
  expected = wire.pack_control(wire.STATE, state, 2**32 + 3)
  check_value = wire._check_value
  roots = []

  def checked(value, depth=0):
    if depth == 0:
      roots.append(value)
    return check_value(value, depth)

  monkeypatch.setattr(wire, "_check_value", checked)
  assert wire.pack_scene(state, 2**32 + 3) == expected
  assert roots == [state]


def test_negotiated_compression_is_lossless_and_smaller_for_real_lane_geometry():
  state = realistic_scene()
  legacy = wire.pack_scene(state, 2**32 + 7)
  assert legacy == wire.pack_control(wire.STATE, state, 2**32 + 7)
  compressed = wire.pack_scene(state, 2**32 + 7, compression="zlib")
  kind, sequence, size = wire.unpack_control_header(compressed[:wire.CONTROL_HEADER.size])
  assert (kind, sequence, size) == (wire.STATE_ZLIB, 7, len(compressed) - wire.CONTROL_HEADER.size)
  assert len(compressed) < len(legacy) / 2
  restored = wire.decode_state_payload(kind, compressed[wire.CONTROL_HEADER.size:])
  assert restored == state
  assert wire.validate_scene(restored) == state
  assert wire.decode_state_payload(wire.STATE, legacy[wire.CONTROL_HEADER.size:]) == restored


def test_compression_checks_numeric_tree_once_and_encodes_json_once(monkeypatch):
  state = realistic_scene()
  check_value, dumps = wire._check_value, wire.json.dumps
  roots, encoded = [], []
  def checked(value, depth=0):
    if depth == 0:
      roots.append(value)
    return check_value(value, depth)
  def encode(*args, **kwargs):
    encoded.append(args[0])
    return dumps(*args, **kwargs)
  monkeypatch.setattr(wire, "_check_value", checked)
  monkeypatch.setattr(wire.json, "dumps", encode)
  wire.pack_scene(state, compression="zlib")
  assert roots == encoded == [state]


@pytest.mark.parametrize("compression", ["gzip", "ZLIB", True, False, 1])
def test_unknown_compression_is_rejected(compression):
  with pytest.raises(ValueError, match="compression"):
    wire.pack_scene(scene(), compression=compression)


def test_generic_json_control_packer_cannot_emit_an_uncompressed_zlib_state():
  with pytest.raises(ValueError, match="pack_scene"):
    wire.pack_control(wire.STATE_ZLIB, scene())


def test_compression_falls_back_to_plain_state_when_not_smaller(monkeypatch):
  state = scene()
  legacy = wire.pack_scene(state, 123)
  monkeypatch.setattr(wire.zlib, "compress", lambda data, *, level: data + b"overhead")
  assert wire.pack_scene(state, 123, compression="zlib") == legacy


def test_small_scene_uses_real_plain_fallback_when_zlib_adds_overhead():
  state = {"model_valid": False, "hud": {}, "path": {}}
  assert wire.pack_scene(state, 11, compression="zlib") == wire.pack_scene(state, 11)


@pytest.mark.parametrize("compression", [None, "zlib"])
def test_encoded_json_budget_is_checked_before_compression(compression):
  state = scene()
  state["extra"] = "a" * wire.MAX_CONTROL_SIZE
  with pytest.raises(ValueError, match="byte limit"):
    wire.pack_scene(state, compression=compression)


def test_compressed_state_accepts_exact_uncompressed_boundary():
  state = scene()
  state["extra"] = ""
  overhead = len(wire.pack_scene(state)) - wire.CONTROL_HEADER.size
  state["extra"] = "x" * (wire.MAX_CONTROL_SIZE - overhead)
  plain = wire.pack_scene(state)
  assert len(plain) - wire.CONTROL_HEADER.size == wire.MAX_CONTROL_SIZE
  compressed = wire.pack_scene(state, compression="zlib")
  kind, _, size = wire.unpack_control_header(compressed[:wire.CONTROL_HEADER.size])
  assert kind == wire.STATE_ZLIB and size < wire.MAX_CONTROL_SIZE
  assert wire.decode_state_payload(kind, compressed[wire.CONTROL_HEADER.size:]) == state


@pytest.mark.parametrize("payload", [
  b"", b"plain JSON", zlib.compress(b"{}")[:-1], zlib.compress(b"{}")[:3],
  zlib.compress(b"{}") + b"\x00", zlib.compress(b"{}") + zlib.compress(b"{}"),
  zlib.compress(b""), zlib.compress(b"[]"), zlib.compress(b'{"x":NaN}'),
  zlib.compress(b" " * (wire.MAX_CONTROL_SIZE + 1)),
  zlib.compress(b" " * (wire.MAX_CONTROL_SIZE * 32)), b"x" * (wire.MAX_CONTROL_SIZE + 1),
], ids=["empty", "plain", "truncated-tail", "truncated-header", "trailing-byte", "concatenated", "empty-json",
        "non-object-json", "nonfinite-json", "overflow-one", "bomb", "compressed-input-overflow"])
def test_bad_or_unbounded_compressed_state_is_rejected(payload):
  with pytest.raises(ValueError):
    wire.decode_state_payload(wire.STATE_ZLIB, payload)


@pytest.mark.parametrize("kind", [wire.HELLO, wire.OFFER, wire.ANSWER, wire.FEEDBACK, wire.ERROR, 8])
def test_state_decoder_rejects_other_control_kinds(kind):
  with pytest.raises(ValueError, match="scene state"):
    wire.decode_state_payload(kind, b"{}")


@pytest.mark.parametrize("state", [None, [], {"model_valid": True, "hud": {}, "path": None}])
def test_local_scene_packer_rejects_non_scene_objects(state):
  with pytest.raises(ValueError):
    wire.pack_scene(state)


@pytest.mark.parametrize("mutation", [
  lambda s: s["hud"].update(v_ego=math.nan),
  lambda s: s["path"].update(path_x=[math.inf, 2]),
  lambda s: s["path"].update(calib_transform=[[math.inf, 0, 0]] * 3),
  lambda s: s.update(counter=10**400),
  lambda s: s.update(extra=[0] * 129),
  lambda s: s.update(extra={str(i): 0 for i in range(97)}),
  lambda s: s.update(extra=[[[[[[[[0]]]]]]]]),
  lambda s: s.update({"x" * 97: 0}),
  lambda s: s.update(extra="x" * wire.MAX_CONTROL_SIZE),
  lambda s: s.update(extra="한" * 11000),
])
def test_local_scene_packer_retains_recursive_and_encoded_byte_budgets(mutation):
  state = scene()
  mutation(state)
  with pytest.raises(ValueError):
    wire.pack_scene(state)


@pytest.mark.parametrize("payload", [
  b"[]", b"null", b"{}\xff", b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":' + b'[' * 140 + b'0' + b']' * 140 + b'}',
])
def test_malformed_or_nonfinite_control_is_rejected(payload):
  with pytest.raises((ValueError, json.JSONDecodeError)):
    wire.decode_payload(payload)


@pytest.mark.parametrize("kind,size", [(0, 1), (8, 1), (wire.STATE, 0), (wire.STATE, 65537), (wire.OFFER, 2**32 - 1)])
def test_invalid_header_is_rejected_before_allocating_payload(kind, size):
  with pytest.raises(ValueError):
    wire.unpack_control_header(wire.CONTROL_HEADER.pack(wire.H264_MAGIC, 1, kind, 0, size))


@pytest.mark.parametrize("payload", [{"sdp": "x" * 65536}, {"x": [0] * 129}, {"x": math.nan}, {"x": 10**400}])
def test_oversize_or_bad_local_control_is_not_encoded(payload):
  with pytest.raises(ValueError):
    wire.pack_control(wire.OFFER, payload)


@pytest.mark.parametrize("mutation", [
  lambda s: s.update(source_size=[8193, 760]), lambda s: s.update(source_size=[1344.0, 760]),
  lambda s: s["path"].update(path_x=[0] * 65), lambda s: s["path"].update(lane_lines=[[[0], [0], [0]]] * 5),
  lambda s: s["path"].update(lane_lines=[[[0], [0, 1], [0]]]),
  lambda s: s["path"].update(calib_transform=[[1, 0], [0, 1]]),
  lambda s: s["path"].update(leads=[{"present": "yes"}]), lambda s: s["hud"].update(tpms=[1] * 100),
  lambda s: s["path"].update(camera_height=[1]), lambda s: s["path"].update(camera_height="1"),
])
def test_remote_geometry_cannot_expand_drawing_allocations(mutation):
  state = scene()
  mutation(state)
  with pytest.raises(ValueError):
    wire.validate_scene(state)
  with pytest.raises(ValueError):
    wire.pack_scene(state)


def test_unknown_source_geometry_is_explicit_and_valid_for_bootstrap():
  state = scene()
  state.update(source_size=None, model_valid=False)
  assert wire.validate_scene(state) is state
