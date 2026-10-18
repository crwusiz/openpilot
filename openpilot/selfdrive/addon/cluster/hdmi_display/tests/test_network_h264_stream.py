import asyncio
from fractions import Fraction
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SPS, PPS, IDR, PFRAME = b"\x67\x42\x11", b"\x68\xce\x12", b"\x65\x88\x13", b"\x41\x99\x14"
START = b"\x00\x00\x00\x01"


@pytest.fixture
def sender(monkeypatch):
  class Base:
    def __init__(self, *args, **kwargs):
      self.options = kwargs
      self.connection_stopped_event = asyncio.Event()
      self.logger = Mock()

    def _set_event(self, event):
      event.set()

    async def stop(self):
      self.stopped = True

  monkeypatch.setitem(sys.modules, "teleoprtc.stream", SimpleNamespace(
    RTCSessionDescription=SimpleNamespace, WebRTCAnswerStream=Base,
  ))
  spec = importlib.util.spec_from_file_location("cluster_h264_sender_under_test",
                                               Path(__file__).parents[1] / "network_h264_stream.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module, "flog", Mock())
  return module


@pytest.mark.parametrize("separator", [b"\x00\x00\x01", START])
def test_annexb_mixed_start_codes_are_separate_checked_nal_units(sender, separator):
  data = START + SPS + separator + PPS + b"\x00\x00\x01" + IDR
  normalized, keyframe = sender.normalize_access_unit(data)
  assert normalized == START + SPS + START + PPS + START + IDR
  assert keyframe
  assert sender.normalize_access_unit(START + PFRAME) == (START + PFRAME, False)


def test_emulation_prevention_payload_is_preserved_and_annexb_padding_is_removed(sender):
  unit = IDR + b"\x00\x00\x03\x01\x22"
  assert sender.normalize_access_unit(b"\x00" + START + unit + b"\x00\x00") == (START + unit, True)


def test_end_of_sequence_header_is_valid_elementary_h264(sender):
  assert sender.normalize_access_unit(START + b"\x0a") == (START + b"\x0a", False)


def test_real_h264_idr_and_dependent_frames_decode_identically_after_normalization(sender):
  av = pytest.importorskip("av")
  np = pytest.importorskip("numpy")
  encoder = av.CodecContext.create("libx264", "w")
  encoder.width, encoder.height, encoder.pix_fmt = 160, 96, "yuv420p"
  encoder.time_base, encoder.framerate = Fraction(1, 20), Fraction(20)
  encoder.options = {"preset": "ultrafast", "tune": "zerolatency", "x264-params": "keyint=20:scenecut=0"}
  packets = []
  for index in range(12):
    pixels = np.random.default_rng(index).integers(0, 256, (96, 160, 3), dtype=np.uint8)
    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
    frame.pts = index
    packets.extend(encoder.encode(frame))
  packets.extend(encoder.encode(None))
  original, normalized = av.CodecContext.create("h264", "r"), av.CodecContext.create("h264", "r")
  expected, actual, keyframes = [], [], []
  for packet in packets:
    data, keyframe = sender.normalize_access_unit(bytes(packet))
    keyframes.append(keyframe)
    expected.extend(original.decode(av.Packet(bytes(packet))))
    actual.extend(normalized.decode(av.Packet(data)))
  expected.extend(original.decode(None))
  actual.extend(normalized.decode(None))
  assert len(expected) == len(actual) == 12
  assert any(keyframes) and not all(keyframes)
  for before, after in zip(expected, actual, strict=True):
    np.testing.assert_array_equal(before.to_ndarray(format="rgb24"), after.to_ndarray(format="rgb24"))


@pytest.mark.parametrize("data", [b"", b"raw data", b"garbage" + START + IDR, START, START + START + IDR,
                                  START + b"\x65", START + b"\x80\x10", START + b"\x00\x10", START + b"\x7c\x10"])
def test_invalid_access_units_are_rejected_before_native_packetization(sender, data):
  with pytest.raises(ValueError):
    sender.normalize_access_unit(data)


def test_access_unit_byte_and_nal_budgets_are_bounded(sender, monkeypatch):
  monkeypatch.setattr(sender, "MAX_ACCESS_UNIT_BYTES", 80)
  with pytest.raises(ValueError, match="byte budget"):
    sender.normalize_access_unit(START + IDR + b"\x11" * 80)
  monkeypatch.setattr(sender, "MAX_NAL_UNITS", 2)
  with pytest.raises(ValueError, match="too many"):
    sender.normalize_access_unit(START + SPS + START + PPS + START + IDR)


def test_rtp_time_follows_capture_intervals_and_rebases_a_camera_clock_restart(sender):
  clock = sender.CaptureClock()
  assert [clock.pts(eof, fallback) for eof, fallback in [
    (1_000_000_000, 90000), (1_050_000_000, 90001), (1_100_000_000, 180000),
  ]] == [0, 4500, 9000]
  assert clock.pts(100_000_000, 0) == 13500
  assert clock.pts(150_000_000, 0) == 18000


def test_absent_capture_time_uses_packet_pts(sender):
  clock = sender.CaptureClock()
  assert clock.pts(None, 1234) == 1234
  assert clock.pts(None, 5734) == 5734


class Packet(bytes):
  pts = 0


def make_tracks(packets, send_count):
  sent = []
  config = SimpleNamespace(start_timestamp=0xFFFFFFF0, timestamp=0)
  native = SimpleNamespace(closed=False, send_frame=Mock(side_effect=AssertionError("FrameInfo path must not be used")))
  native.is_open = lambda: not native.closed
  native.is_closed = lambda: native.closed

  def send(data):
    sent.append((bytes(data), config.timestamp))
    native.closed = len(sent) >= send_count
    return True

  native.send = Mock(side_effect=send)
  producer = SimpleNamespace(readyState="live", request_keyframe=Mock(), video_timestamp_eof=None)
  pending = iter(packets)

  async def receive():
    data, eof = next(pending)
    producer.video_timestamp_eof = eof
    return Packet(data)

  producer.recv = receive
  return producer, native, config, sent


def test_sender_uses_explicit_wrapping_capture_timestamp_and_checked_track_send(sender):
  producer, native, config, sent = make_tracks([(START + IDR, 1_000_000_000),
                                               (b"\x00\x00\x01" + PFRAME, 1_050_000_000)], 2)
  stream = sender.ClusterH264Stream("v=0", producer)
  asyncio.run(stream._send_track_loop(native, producer, config))
  assert sent == [(START + IDR, 0xFFFFFFF0), (START + PFRAME, (0xFFFFFFF0 + 4500) & 0xFFFFFFFF)]
  native.send_frame.assert_not_called()
  assert stream.connection_stopped_event.is_set()


def test_invalid_input_requests_idr_and_does_not_forward_dependent_pictures(sender):
  producer, native, config, sent = make_tracks([(START, 1_000_000_000), (START + PFRAME, 1_050_000_000),
                                               (START + SPS + START + PPS + START + IDR, 1_100_000_000)], 1)
  stream = sender.ClusterH264Stream("v=0", producer)
  asyncio.run(stream._send_track_loop(native, producer, config))
  assert [data for data, _pts in sent] == [START + SPS + START + PPS + START + IDR]
  producer.request_keyframe.assert_called_once()
  assert any("Invalid access unit" in call.args[0] for call in sender.flog.call_args_list)


def test_connection_closed_during_camera_wait_cannot_enter_native_send(sender):
  producer, native, config, sent = make_tracks([], 1)

  async def receive():
    native.closed = True
    return Packet(START + IDR)

  producer.recv = receive
  stream = sender.ClusterH264Stream("v=0", producer)
  asyncio.run(stream._send_track_loop(native, producer, config))
  assert not sent
  native.send.assert_not_called()
  assert stream.connection_stopped_event.is_set()


def test_native_send_failure_disconnects_the_session_and_surfaces_an_error(sender):
  producer, native, config, _sent = make_tracks([(START + IDR, 1_000_000_000)], 1)
  native.send.return_value = False
  native.send.side_effect = None
  stream = sender.ClusterH264Stream("v=0", producer)
  asyncio.run(stream._send_track_loop(native, producer, config))
  assert stream.connection_stopped_event.is_set()
  assert any("[CLUSTER_H264_SEND_ERROR]" in call.args[0] for call in sender.flog.call_args_list)


def test_native_chain_wrappers_are_released_only_after_stream_stop(sender):
  stream = sender.ClusterH264Stream("v=0", object())
  stream._native_video_handlers.append(object())
  asyncio.run(stream.stop())
  assert stream.stopped and stream._native_video_handlers == []
