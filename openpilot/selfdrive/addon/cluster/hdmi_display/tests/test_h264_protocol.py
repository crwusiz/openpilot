import json
import math

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_protocol as wire


def scene():
  return {"model_valid": True, "hud": {"v_ego": 12.0, "tpms": [34, 34, 35, 35]},
          "path": {"path_x": [1, 2], "path_y": [0, 0], "path_z": [0, 0],
                   "calib_transform": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}, "source_size": [1344, 760]}


def test_scene_wire_is_bounded_json_with_explicit_codec_magic():
  state = scene()
  packet = wire.pack_control(wire.STATE, state, 2**32 + 3)
  assert wire.unpack_control_header(packet[:16]) == (wire.STATE, 3, len(packet) - 16)
  assert packet[:4] == b"OPCH"
  assert wire.validate_scene(wire.decode_payload(packet[16:])) == state


@pytest.mark.parametrize("payload", [
  b"[]", b"null", b"{}\xff", b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":' + b'[' * 140 + b'0' + b']' * 140 + b'}',
])
def test_malformed_or_nonfinite_control_is_rejected(payload):
  with pytest.raises((ValueError, json.JSONDecodeError)):
    wire.decode_payload(payload)


@pytest.mark.parametrize("kind,size", [(0, 1), (7, 1), (wire.STATE, 0), (wire.STATE, 65537), (wire.OFFER, 2**32 - 1)])
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


def test_unknown_source_geometry_is_explicit_and_valid_for_bootstrap():
  state = scene()
  state.update(source_size=None, model_valid=False)
  assert wire.validate_scene(state) is state
