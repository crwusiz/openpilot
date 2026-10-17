from fractions import Fraction
from unittest.mock import patch

import pytest

from openpilot.selfdrive.addon.cluster.usb_display import h264_bitstream as stream


class Bitstream:
  """Independent fixture writer, including RBSP escaping and trailing bits."""
  def __init__(self):
    self.bits = ""

  def uint(self, value, count=1):
    self.bits += format(value, f"0{count}b")
    return self

  def ue(self, value):
    code = format(value + 1, "b")
    self.bits += "0" * (len(code) - 1) + code
    return self

  def se(self, value):
    return self.ue(value * 2 - 1 if value > 0 else -value * 2)

  def finish(self):
    bits = self.bits + "1"
    bits += "0" * (-len(bits) % 8)
    raw = bytes(int(bits[index:index + 8], 2) for index in range(0, len(bits), 8))
    output = bytearray()
    zeros = 0
    for byte in raw:
      if zeros >= 2 and byte <= 3:
        output.append(3)
        zeros = 0
      output.append(byte)
      zeros = zeros + 1 if byte == 0 else 0
    return bytes(output)


def sps(*, profile=66, width=464, height=1920, sps_id=0, interlaced=False, vui=False, refs=0, poc_type=2, level=40):
  bits = Bitstream().uint(profile, 8).uint(0, 8).uint(level, 8).ue(sps_id).ue(0).ue(poc_type)
  if poc_type == 0:
    bits.ue(0)
  elif poc_type == 1:
    bits.uint(0).se(0).se(0).ue(1).se(0)
  bits.ue(refs).uint(0).ue(width // 16 - 1).ue(height // 16 - 1).uint(int(not interlaced))
  if interlaced:
    bits.uint(0)
  bits.uint(1).uint(0).uint(int(vui))
  if vui:
    # Existing timing is deliberately different; output must advertise 20 Hz.
    bits.uint(0, 4).uint(1).uint(1, 32).uint(60, 32).uint(1).uint(0, 3).uint(0)
  return b"\x67" + bits.finish()


def pps(*, pps_id=0, sps_id=0, cabac=False):
  bits = Bitstream().ue(pps_id).ue(sps_id).uint(int(cabac)).uint(0).ue(0).ue(0).ue(0).uint(0, 3)
  bits.se(0).se(0).se(0).uint(1).uint(0, 2)
  return b"\x68" + bits.finish()


def idr(*, pps_id=0, first_mb=0):
  return b"\x65" + Bitstream().ue(first_mb).ue(7).ue(pps_id).finish()


def annex(*nals, start_code=stream.START_CODE):
  return b"".join(start_code + nal for nal in nals)


def nals(data):
  return data.split(stream.START_CODE)[1:]


def parse_patched_sps(nal):
  raw = stream._unescape(nal[1:])
  reader = stream._Bits(raw)
  profile, flags, level = reader.read(8), reader.read(8), reader.read(8)
  sps_id = reader.ue()
  reader.ue()
  poc_type = reader.ue()
  if poc_type == 0:
    reader.ue()
  elif poc_type == 1:
    reader.read()
    reader.se()
    reader.se()
    for _ in range(reader.ue()):
      reader.se()
  refs = reader.ue()
  reader.read()
  coded_width, coded_height = (reader.ue() + 1) * 16, (reader.ue() + 1) * 16
  assert reader.read() == 1
  reader.read()
  crop = [reader.ue() for _ in range(4)] if reader.read() else [0] * 4
  assert reader.read() == 1
  assert reader.read(4) == 0
  assert reader.read() == 1
  tick, scale = reader.read(32), reader.read(32)
  assert reader.read() == 1
  assert reader.read(3) == 0
  assert reader.read() == reader.read() == 1
  restriction = [reader.ue() for _ in range(6)]
  assert reader.read() == 1  # RBSP stop bit
  return {
    "profile": profile, "constraints": flags, "level": level, "id": sps_id, "refs": refs,
    "coded": (coded_width, coded_height), "crop": crop, "timing": (tick, scale), "restriction": restriction,
  }


@pytest.mark.parametrize("vui", [False, True])
@pytest.mark.parametrize("poc_type", [0, 1, 2])
def test_portrait_sps_is_constrained_baseline_cropped_and_low_delay(vui, poc_type):
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  result = formatter.format(annex(sps(vui=vui, poc_type=poc_type), pps(), idr()))
  units = nals(result)
  assert [nal[0] & 31 for nal in units] == [7, 8, 5]
  info = parse_patched_sps(units[0])
  assert info["profile"] == 66 and info["constraints"] & 0x40
  assert info["coded"] == (464, 1920)
  assert info["crop"] == [0, 1, 0, 0]
  assert info["timing"] == (1, 40)
  assert info["restriction"][-2:] == [0, 1]
  assert units[1:] == [pps(), idr()]


@pytest.mark.parametrize("width,height,fps,crop", [(464, 1920, 30, [0, 0, 0, 0]), (462, 1918, 20, [0, 1, 0, 1])])
def test_dimensions_and_timing_are_configurable(width, height, fps, crop):
  formatter = stream.H264AccessUnitFormatter(width, height, fps)
  result = formatter.format(annex(sps(), pps(), idr()))
  info = parse_patched_sps(nals(result)[0])
  assert info["crop"] == crop
  assert info["timing"] == (1, fps * 2)


def test_config_only_callbacks_are_cached_and_every_idr_repeats_parameter_sets():
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  assert formatter.format(b"") is None
  assert formatter.format(annex(sps())) is None
  assert formatter.format(annex(pps())) is None
  first = formatter.format(annex(idr()))
  assert formatter.format(annex(idr())) == first
  assert formatter.format(annex(sps(), pps(), idr())) == first


def test_identical_inline_sps_and_pps_skip_repeated_parsing():
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  with patch.object(stream, "_patch_sps", wraps=stream._patch_sps) as patch_sps, \
       patch.object(stream, "_pps_ids", wraps=stream._pps_ids) as parse_pps:
    for _ in range(10):
      assert formatter.format(annex(sps(), pps(), idr()))
    patch_sps.assert_called_once()
    parse_pps.assert_called_once()
    assert formatter.format(annex(sps(level=41), pps(), idr()))
    assert patch_sps.call_count == 2


def test_aud_sei_and_multiple_idr_slices_preserve_image_order():
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  aud, sei = b"\x09\xf0", b"\x06\x05\x01\x80"
  result = formatter.format(annex(aud, sps(), pps(), sei, idr(), idr(first_mb=300), start_code=b"\x00\x00\x01"))
  units = nals(result)
  assert [nal[0] & 31 for nal in units] == [9, 7, 8, 6, 5, 5]
  assert units[3:] == [sei, idr(), idr(first_mb=300)]


@pytest.mark.parametrize("unit", [b"\x41\x80", b"\x01\x80", b"\x42\x80", b"\x74\x80"])
def test_non_idr_coded_frames_are_rejected_instead_of_entering_the_drop_queue(unit):
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  with pytest.raises(RuntimeError, match="non-IDR"):
    formatter.format(annex(sps(), pps(), unit))


@pytest.mark.parametrize("units", [
  (idr(),), (sps(), idr()), (pps(), idr()), (sps(), pps(sps_id=1), idr()),
  (sps(), pps(), idr(pps_id=1)), (sps(), pps(), idr(), idr(pps_id=1)),
])
def test_idr_requires_matching_parameter_sets(units):
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  with pytest.raises(RuntimeError, match="matching SPS/PPS"):
    formatter.format(annex(*units))


@pytest.mark.parametrize("broken", [
  b"not annex b", b"\x00\x00\x00\x02\x65\x80", annex(b""), annex(b"\xe7\x80"),
  annex(b"\x67"), annex(sps(profile=100)), annex(sps(interlaced=True)),
  annex(sps(width=480)), annex(sps(height=1936)), annex(sps()[:-1]),
  annex(b"\x67" + b"\x00" * 50 + b"\x80"), annex(b"\x68\x80"),
  annex(pps(cabac=True)), annex(b"\x65\x00"), annex(b"\x00\x80"),
  annex(b"\x67\x42\x00\x00\x03\xff\x80"),
  annex(b"\x05\x80"), annex(b"\x07\x80"), annex(b"\x08\x80"),
])
def test_malformed_or_unsupported_headers_fail_with_a_bounded_error(broken):
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  with pytest.raises(RuntimeError, match="Invalid USB H.264"):
    formatter.format(broken)


def test_invalid_callback_does_not_poison_last_complete_configuration():
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  expected = formatter.format(annex(sps(), pps(), idr()))
  with pytest.raises(RuntimeError):
    formatter.format(annex(sps(level=41), pps(sps_id=1), idr()))
  assert formatter.format(annex(idr())) == expected


@pytest.mark.parametrize("data,error", [
  (b"x" * (stream.MAX_ACCESS_UNIT_BYTES + 1), "2 MiB"),
  (annex(b"\x67" + b"x" * stream.MAX_PARAMETER_SET_BYTES), "8 KiB"),
  (annex(b"\x68" + b"x" * stream.MAX_PARAMETER_SET_BYTES), "8 KiB"),
  (annex(*([b"\x0c\x80"] * (stream.MAX_NALS + 1))), "too many"),
], ids=["oversized-au", "oversized-sps", "oversized-pps", "too-many-nals"])
def test_access_unit_and_parameter_set_limits(data, error):
  with pytest.raises(RuntimeError, match=error):
    stream.H264AccessUnitFormatter(462, 1920, 20).format(data)


def test_repeated_headers_cannot_push_output_past_the_access_unit_limit():
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  formatter.format(annex(sps(), pps()))
  data = annex(b"\x06" + b"\xff" * (stream.MAX_ACCESS_UNIT_BYTES - len(annex(idr())) - 5), idr())
  assert len(data) == stream.MAX_ACCESS_UNIT_BYTES
  with pytest.raises(RuntimeError, match="formatted access unit"):
    formatter.format(data)


@pytest.mark.parametrize("width,height,fps", [(461, 1920, 20), (462, 1919, 20), (0, 1920, 20),
                                               (462, 8194, 20), (462, 1920, 0), (462, 1920, 61),
                                               (462.0, 1920, 20), (462, 1920, True)])
def test_invalid_formatter_configuration_is_rejected(width, height, fps):
  with pytest.raises(ValueError):
    stream.H264AccessUnitFormatter(width, height, fps)


def test_real_baseline_frame_decodes_to_cropped_portrait_dimensions():
  av = pytest.importorskip("av")
  np = pytest.importorskip("numpy")
  try:
    encoder = av.CodecContext.create("libx264", "w")
  except av.codec.codec.UnknownCodecError:
    pytest.skip("PyAV was built without libx264")
  encoder.width, encoder.height = 464, 1920
  encoder.pix_fmt = "yuv420p"
  encoder.time_base, encoder.framerate = Fraction(1, 20), Fraction(20, 1)
  encoder.options = {"profile": "baseline", "preset": "ultrafast", "tune": "zerolatency",
                     "x264-params": "keyint=1:min-keyint=1:scenecut=0:bframes=0:repeat-headers=1"}
  frame = av.VideoFrame.from_ndarray(np.zeros((1920, 464, 3), dtype=np.uint8), format="rgb24")
  data = b"".join(bytes(packet) for packet in (*encoder.encode(frame), *encoder.encode(None)))
  formatter = stream.H264AccessUnitFormatter(462, 1920, 20)
  formatted = formatter.format(data)
  decoder = av.CodecContext.create("h264", "r")
  decoded = [*decoder.decode(av.Packet(formatted)), *decoder.decode(None)]
  assert len(decoded) == 1
  assert (decoded[0].width, decoded[0].height) == (462, 1920)
  assert int(decoded[0].to_ndarray(format="rgb24").max()) <= 3
  assert decoder.profile == "Constrained Baseline"
  assert decoder.framerate == Fraction(20, 1)
  without_headers = annex(*(nal for nal in stream._nals(data) if nal[0] & 31 not in (7, 8)))
  reconnected = av.CodecContext.create("h264", "r")
  repeated = formatter.format(without_headers)
  frames = [*reconnected.decode(av.Packet(repeated)), *reconnected.decode(None)]
  assert len(frames) == 1 and (frames[0].width, frames[0].height) == (462, 1920)
