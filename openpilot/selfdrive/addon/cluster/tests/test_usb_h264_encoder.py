import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image
import pytest

from openpilot.selfdrive.addon.cluster.usb_display import h264_encoder as module
from openpilot.selfdrive.addon.cluster.usb_display.h264_encoder import HardwareH264Encoder, portrait_nv12


@pytest.mark.parametrize("rotate", [False, True])
@pytest.mark.parametrize("width,height", [(16, 8), (1920, 462)])
def test_portrait_nv12_matches_panel_orientation_and_pads_only_outside_visible_crop(rotate, width, height):
  rgb = np.zeros((height, width, 3), dtype=np.uint8)
  rgb[:height // 2, :width // 2] = (255, 0, 0)
  rgb[height // 2:, :width // 2] = (0, 255, 0)
  rgb[:height // 2, width // 2:] = (0, 0, 255)
  rgb[height // 2:, width // 2:] = (255, 255, 255)
  original = rgb.copy()
  result = portrait_nv12(Image.fromarray(rgb), width, height, rotate)
  encoded_width, encoded_height = (height + 15) // 16 * 16, (width + 15) // 16 * 16
  decoded = cv2.cvtColor(result.reshape(encoded_height * 3 // 2, encoded_width), cv2.COLOR_YUV2RGB_NV12)
  expected = np.rot90(rgb, 1 if rotate else 3)
  # Sample solid quadrants away from subsampled boundaries.
  for y in (width // 4, width * 3 // 4):
    for x in (height // 4, height * 3 // 4):
      np.testing.assert_allclose(decoded[y, x].astype(int), expected[y, x].astype(int), atol=2)
  np.testing.assert_array_equal(original, rgb)
  assert result.nbytes == encoded_width * encoded_height * 3 // 2


@pytest.fixture
def native(monkeypatch):
  config = SimpleNamespace(width=16, height=8, usb_h264_fps=20, usb_h264_bitrate=4_666_667, usb_h264_gop=1)
  names = ("create", "open", "close", "destroy", "last_error", "encode_nv12_packed", "drain",
           "set_slice_max_bytes", "set_rate_control", "set_realtime_priority")
  functions = {name: Mock(return_value=0) for name in names}
  functions["create"].return_value = 42
  functions["last_error"].return_value = b"driver failure"
  library = SimpleNamespace(**{"cluster_h264_encoder_bridge_" + name: function for name, function in functions.items()})
  now = [100.0]
  formatter = SimpleNamespace(format=lambda data: data)
  monkeypatch.setattr(module, "H264AccessUnitFormatter", lambda *_args: formatter)
  monkeypatch.setattr(module, "flog", Mock())
  encoder = HardwareH264Encoder(config, library=library, clock=lambda: now[0])
  return SimpleNamespace(config=config, functions=functions, encoder=encoder, now=now, formatter=formatter)


def _packet(callback, timestamp, payload=b"independent frame"):
  storage = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
  callback(storage, len(payload), 8, timestamp, 0, 1, None)
  # Callback must have copied before the native capture buffer is requeued.
  storage[0] = 0


def test_delayed_native_output_retains_original_render_timestamp_and_owned_bytes(native):
  waiting = []

  def submit(_handle, _data, size, timestamp, callback, _opaque):
    assert size == 16 * 16 * 3 // 2
    if waiting:
      _packet(callback, waiting.pop(0))
    waiting.append(timestamp)
    return 0

  native.functions["encode_nv12_packed"].side_effect = submit
  image = np.zeros((8, 16, 3), dtype=np.uint8)
  assert native.encoder.encode(image, 99.98) is None
  native.now[0] = 100.05
  result = native.encoder.encode(image, 100.04)
  assert result.created_at == 99.98
  assert result.timestamp_us == 100_000_000
  assert result.data == b"independent frame"
  assert result.prepare_elapsed == pytest.approx(0.05)
  native.functions["set_rate_control"].assert_called_once_with(42, 2)
  native.functions["set_realtime_priority"].assert_called_once_with(42, 0)
  native.encoder.close()
  native.encoder.close()
  native.functions["destroy"].assert_called_once_with(42)


def test_async_output_keeps_conversion_metrics_for_its_own_input(native, monkeypatch):
  conversions = iter([
    module.ConversionTimings(0.030, 0.010, 0.005, 0.025, "native_tiled"),
    module.ConversionTimings(0.020, 0.015, 0.004, 0.016, "native_tiled"),
  ])
  monkeypatch.setattr(native.encoder, "_to_nv12", lambda _frame: (np.zeros(384, dtype=np.uint8), next(conversions)))
  waiting = []

  def submit(_handle, _data, _size, timestamp, callback, _opaque):
    if waiting:
      _packet(callback, waiting.pop(0))
    waiting.append(timestamp)
    return 0

  native.functions["encode_nv12_packed"].side_effect = submit
  assert native.encoder.encode(None, 99.98) is None
  native.now[0] = 100.05
  result = native.encoder.encode(None, 100.04)
  assert result.created_at == 99.98
  assert result.convert_elapsed == 0.030
  assert result.convert_cpu_elapsed == 0.010
  assert result.rgb_export_elapsed == 0.005
  assert result.pixels_elapsed == 0.025
  assert result.converter == "native_tiled"


def test_older_bridge_uses_the_existing_color_conversion_and_reports_rebuild(native, monkeypatch):
  assert native.encoder._convert_rgb is None
  assert any("Native RGB converter unavailable" in call.args[0] for call in module.flog.call_args_list)
  expected = np.zeros(384, dtype=np.uint8)
  convert = Mock(return_value=expected)
  monkeypatch.setattr(module, "portrait_nv12", convert)
  result, metrics = native.encoder._to_nv12(Image.new("RGB", (16, 8)))
  assert result is expected
  assert metrics.backend == "opencv"
  assert convert.call_count == 1


def test_drain_can_finish_current_frame_without_waiting_for_next_input(native):
  submitted = []
  native.functions["encode_nv12_packed"].side_effect = lambda _h, _d, _s, timestamp, _c, _o: submitted.append(timestamp) or 0

  def drain(_handle, timeout_ms, callback, _opaque):
    assert timeout_ms == 10
    _packet(callback, submitted.pop())
    return 0

  native.functions["drain"].side_effect = drain
  result = native.encoder.encode(np.zeros((8, 16, 3), dtype=np.uint8), 99.9)
  assert result.created_at == 99.9
  assert not native.encoder._pending


@pytest.mark.parametrize("failure", ["open", "set_slice_max_bytes", "set_rate_control", "set_realtime_priority"])
def test_failed_hardware_setup_releases_handle_and_reports_native_error(native, failure):
  native.functions[failure].return_value = -1
  with pytest.raises(RuntimeError, match="driver failure"):
    native.encoder.open()
  assert native.encoder.handle is None
  native.functions["close"].assert_called_once_with(42)
  native.functions["destroy"].assert_called_once_with(42)


def test_native_output_stall_is_bounded_instead_of_accumulating_frames(native):
  image = np.zeros((8, 16, 3), dtype=np.uint8)
  assert native.encoder.encode(image, 99.9) is None
  native.now[0] = 100.6
  with pytest.raises(RuntimeError, match="output stalled"):
    native.encoder.encode(image, 100.6)
  assert native.functions["encode_nv12_packed"].call_count == 1


def test_completed_output_after_usb_pause_is_drained_before_declaring_hardware_stalled(native):
  image = np.zeros((8, 16, 3), dtype=np.uint8)
  assert native.encoder.encode(image, 99.9) is None
  native.now[0] = 101.0

  def drain(_handle, timeout_ms, callback, _opaque):
    if timeout_ms == 0:
      _packet(callback, 100_000_000)
    return 0

  native.functions["drain"].side_effect = drain
  result = native.encoder.encode(image, 100.9)
  assert result.created_at == 99.9
  assert native.functions["encode_nv12_packed"].call_count == 2


def test_oversized_native_callback_error_is_propagated_outside_ctypes(native):
  def submit(_handle, data, _size, timestamp, callback, _opaque):
    callback(data, module.MAX_PACKET_BYTES + 1, 0, timestamp, 0, 1, None)
    return 0

  native.functions["encode_nv12_packed"].side_effect = submit
  with pytest.raises(RuntimeError, match="oversized packet"):
    native.encoder.encode(np.zeros((8, 16, 3), dtype=np.uint8), 99.9)


def test_unknown_output_pts_does_not_silently_freshen_old_frame(native):
  native.functions["drain"].side_effect = lambda _h, _t, callback, _o: _packet(callback, 123) or 0
  with pytest.raises(RuntimeError, match="unknown frame timestamp"):
    native.encoder.encode(np.zeros((8, 16, 3), dtype=np.uint8), 99.9)


def test_missing_hardware_library_does_not_start_software_encoder(monkeypatch):
  monkeypatch.setattr(module.ctypes, "CDLL", Mock(side_effect=OSError("no shared library")))
  with pytest.raises(RuntimeError, match="rebuild openpilot on C4"):
    HardwareH264Encoder(SimpleNamespace(width=1920, height=462))


def test_predictive_gop_is_rejected_before_loading_hardware(monkeypatch):
  loader = Mock()
  monkeypatch.setattr(module.ctypes, "CDLL", loader)
  with pytest.raises(ValueError, match="GOP 1"):
    HardwareH264Encoder(SimpleNamespace(width=1920, height=462, usb_h264_gop=2))
  loader.assert_not_called()
