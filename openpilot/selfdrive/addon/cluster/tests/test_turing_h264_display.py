import struct
import sys
import zlib
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import usb.core

from openpilot.selfdrive.addon.cluster.usb_display import turing_h264_display as display_module


def response(*, queue=0, ok=True):
  payload = bytearray(512)
  payload[1] = 0xC8 if ok else 0
  payload[8] = queue
  return bytes(payload)


@pytest.fixture
def panel(monkeypatch):
  clock = SimpleNamespace(now=10.0)
  logs = []
  sleeps = []
  monkeypatch.setattr(display_module, "flog", logs.append)
  monkeypatch.setattr(display_module, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleeps.append))
  pending = deque()
  state = SimpleNamespace(status=response(), ack=True, writes=[], pending=pending, clock=clock, logs=logs, sleeps=sleeps)

  def read(_size, *, timeout):
    if pending:
      return pending.popleft()
    raise usb.core.USBTimeoutError("timed out", errno=110)

  def write(payload, *, timeout):
    state.writes.append((bytes(payload), timeout))
    if payload[0] == 122:
      if state.status:
        pending.append(state.status)
    elif state.ack:
      pending.append(response())
    return len(payload)

  state.ep_out = SimpleNamespace(bEndpointAddress=0x01, write=Mock(side_effect=write))
  state.ep_in = SimpleNamespace(bEndpointAddress=0x81, read=Mock(side_effect=read))
  state.device = Mock(idProduct=0x0092)
  state.device.is_kernel_driver_active.return_value = True
  state.find = Mock(return_value=state.device)
  state.claim = Mock()
  state.dispose = Mock()
  state.descriptors = Mock(side_effect=[Mock(), state.ep_out, state.ep_in])
  monkeypatch.setattr(display_module.usb.core, "find", state.find)
  monkeypatch.setattr(display_module.usb.util, "claim_interface", state.claim)
  monkeypatch.setattr(display_module.usb.util, "dispose_resources", state.dispose)
  monkeypatch.setattr(display_module.usb.util, "find_descriptor", state.descriptors)

  def header(command):
    packet = bytearray(500)
    packet[0] = command
    return packet

  state.vendor = SimpleNamespace(
    build_command_packet_header=header,
    encrypt_command_packet=lambda packet: packet + bytes(12),
    response_ok=lambda payload: bool(payload) and (payload[1] == 0xC8 or payload[8] == 0xC8),
  )
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.usb_display.turing_protocol", state.vendor)
  state.config = SimpleNamespace(usb_fps=20, usb_h264_fps=20, usb_write_timeout_ms=1000, usb_max_frame_age=0.25)
  state.display = display_module.TuringH264Display(state.config)
  return state


def test_open_uses_captured_h264_setup_and_clears_overlay(panel, monkeypatch):
  monkeypatch.setattr(display_module.sys, "platform", "linux")
  operations = Mock()
  operations.attach_mock(panel.device.detach_kernel_driver, "detach")
  operations.attach_mock(panel.device.set_configuration, "configure")
  operations.attach_mock(panel.claim, "claim")

  assert panel.display.open()

  assert [call[0] for call in operations.mock_calls] == ["detach", "configure", "claim"]
  panel.find.assert_called_once_with(idVendor=0x1CBE, idProduct=0x0092)
  packets = [packet for packet, _timeout in panel.writes]
  assert [packet[0] for packet in packets] == [123, 10, 111, 112, 13, 14, 52, 102, 15]
  assert packets[4][8] == 0
  assert packets[5][8] == 102
  assert packets[-1][8] == 20
  assert packets[7][512:] == display_module.transparent_overlay_png()
  assert panel.sleeps == [*display_module.SETUP_GAPS, 0.070, 0.015]
  assert panel.display.connected
  panel.device.reset.assert_not_called()


def test_overlay_has_aligned_dimensions_and_all_pixels_transparent():
  png = display_module.transparent_overlay_png()
  assert png[:8] == b"\x89PNG\r\n\x1a\n"
  assert struct.unpack(">II", png[16:24]) == (464, 1920)
  at = 8
  compressed = bytearray()
  while at < len(png):
    size = int.from_bytes(png[at:at + 4], "big")
    if png[at + 4:at + 8] == b"IDAT":
      compressed.extend(png[at + 8:at + 8 + size])
    at += size + 12
  rows = zlib.decompress(compressed)
  assert len(rows) == (464 * 4 + 1) * 1920
  assert not any(rows)


def test_shutdown_overlay_is_opaque_black_and_covers_held_video():
  png = display_module.black_overlay_png()
  assert struct.unpack(">II", png[16:24]) == (464, 1920)
  compressed_size = int.from_bytes(png[33:37], "big")
  assert png[37:41] == b"IDAT"
  rows = zlib.decompress(png[41:41 + compressed_size])
  assert rows == (b"\x00" + b"\x00\x00\x00\xff" * 464) * 1920


@pytest.mark.parametrize("failure", ["configuration", "claim", "interface", "endpoint", "write", "pid"])
def test_open_failure_releases_display_without_reset(panel, failure):
  if failure == "configuration":
    panel.device.set_configuration.side_effect = usb.core.USBError("busy", errno=16)
  elif failure == "claim":
    panel.claim.side_effect = usb.core.USBError("busy", errno=16)
  elif failure == "interface":
    panel.descriptors.side_effect = [None]
  elif failure == "endpoint":
    panel.descriptors.side_effect = [Mock(), None, panel.ep_in]
  elif failure == "pid":
    panel.device.idProduct = 0x0088
  else:
    panel.ep_out.write.side_effect = usb.core.USBTimeoutError("timed out", errno=110)

  assert not panel.display.open()
  assert not panel.display.connected
  assert panel.display.device is None
  panel.dispose.assert_called_once_with(panel.device)
  panel.device.reset.assert_not_called()


def test_open_is_idempotent_and_not_found_is_nonfatal(panel):
  panel.find.return_value = None
  assert not panel.display.open()
  panel.dispose.assert_not_called()
  panel.find.return_value = panel.device
  assert panel.display.open()
  assert panel.display.open()
  assert panel.find.call_count == 2


def test_chunking_preserves_access_unit_bytes_and_does_not_end_stream(panel):
  assert panel.display.open()
  panel.writes.clear()
  # A NAL crosses the chunk boundary; USB's byte stream must remain exact.
  data = b"\x00\x00\x00\x01\x67" + bytes(range(256)) * 300 + b"\x00\x00\x01\x65tail"

  assert panel.display.send_access_unit(data, created_at=panel.clock.now)

  chunks = [packet for packet, _timeout in panel.writes if packet[0] == 121]
  assert len(chunks) == 3
  assert b"".join(packet[512:] for packet in chunks) == data
  assert all(int.from_bytes(packet[8:12], "big") == len(packet) - 512 for packet in chunks)
  assert all(packet[12] == 0 for packet in chunks)
  assert all(len(packet) - 512 <= 32768 for packet in chunks)
  assert all(timeout <= 250 for packet, timeout in panel.writes if packet[0] == 121)
  assert panel.sleeps[-2:] == [0.002, 0.002]
  assert panel.display.frame_count == 1
  assert any("[CLUSTER_USB_H264_TX]" in message for message in panel.logs)


def test_live_firmware_without_chunk_or_status_ack_does_not_reconnect(panel):
  panel.ack = False
  panel.status = None
  assert panel.display.open()
  for _ in range(30):
    assert panel.display.send_access_unit(b"\x00\x00\x01\x65frame")
    panel.clock.now += 0.05
  assert panel.display.frame_count == 30
  assert panel.display.connected
  assert panel.display._status_misses == 2
  panel.dispose.assert_not_called()


def test_no_ack_mode_consumes_unsolicited_ack_queue(panel):
  assert panel.display.open()
  for _ in range(30):
    assert panel.display.send_access_unit(b"\x00\x00\x01\x65frame")
  assert not panel.pending
  assert panel.display._drained_acks >= 30
  assert all(call.kwargs["timeout"] <= 20 for call in panel.ep_in.read.call_args_list)


def test_drain_input_is_bounded_even_when_device_never_stops_acknowledging(panel):
  assert panel.display.open()
  panel.ep_in.read.side_effect = None
  panel.ep_in.read.return_value = response()
  panel.ep_in.read.reset_mock()

  panel.display._drain_input()

  assert panel.ep_in.read.call_count == 8


def test_stale_access_unit_is_skipped_without_usb_io(panel):
  assert panel.display.open()
  panel.ep_out.write.reset_mock()
  assert panel.display.send_access_unit(b"frame", created_at=panel.clock.now - 1) is None
  panel.ep_out.write.assert_not_called()
  assert panel.display.frame_count == 0
  assert panel.display.connected


def test_status_poll_cannot_make_an_old_frame_start_uploading(panel):
  assert panel.display.open()
  panel.writes.clear()
  original_read = panel.ep_in.read.side_effect

  def delayed_read(size, *, timeout):
    if timeout == 20:
      panel.clock.now += 0.060
    return original_read(size, timeout=timeout)

  panel.ep_in.read.side_effect = delayed_read
  assert panel.display.send_access_unit(b"frame", created_at=panel.clock.now - 0.2) is None
  assert [packet[0] for packet, _timeout in panel.writes] == [122]
  assert panel.display.connected


@pytest.mark.parametrize("data,keyframe", [(b"", True), (b"frame", False), (bytes(1024 * 1024 + 1), True)], ids=["empty", "dependent", "oversize"])
def test_rejects_empty_dependent_or_oversized_frames_before_write(panel, data, keyframe):
  assert panel.display.open()
  panel.ep_out.write.reset_mock()
  assert panel.display.send_access_unit(data, keyframe=keyframe) is False
  panel.ep_out.write.assert_not_called()


def test_queue_backpressure_drops_whole_frames_without_sleep_and_recovers(panel):
  assert panel.display.open()
  panel.status = response(queue=5)
  panel.writes.clear()
  panel.sleeps.clear()

  assert panel.display.send_access_unit(b"frame") is None
  panel.clock.now += 0.040
  assert panel.display.send_access_unit(b"frame") is None
  assert [packet[0] for packet, _timeout in panel.writes] == [122]
  assert not panel.sleeps

  panel.clock.now += 0.020
  panel.status = response(queue=1)
  assert panel.display.send_access_unit(b"fresh frame")
  assert [packet[0] for packet, _timeout in panel.writes] == [122, 122, 121]
  assert panel.display._skipped == 2


@pytest.mark.parametrize("status", [b"short", response(queue=0xC8), response(queue=99, ok=False)])
def test_invalid_or_image_ack_response_is_not_treated_as_queue_depth(panel, status):
  assert panel.display.open()
  panel.status = status
  assert panel.display.send_access_unit(b"frame")
  assert panel.display._queue_depth is None
  assert panel.display._status_misses == 1


@pytest.mark.parametrize("failure", ["timeout", "short_write", "disconnected", "ack"])
def test_partial_upload_failure_disconnects_without_reset(panel, failure):
  assert panel.display.open()
  # Do not include the periodic optional status command in this failure.
  panel.display._next_status_at = panel.clock.now + 1
  panel.display.wait_ack = failure == "ack"
  if failure == "timeout":
    panel.ep_out.write.side_effect = usb.core.USBTimeoutError("timed out", errno=110)
  elif failure == "short_write":
    panel.ep_out.write.side_effect = lambda _payload, **_kwargs: 20
  elif failure == "disconnected":
    panel.ep_out.write.side_effect = usb.core.USBError("No such device", errno=19)
  else:
    panel.ack = False

  assert not panel.display.send_access_unit(b"frame")
  assert not panel.display.connected
  panel.dispose.assert_called_once_with(panel.device)
  panel.device.reset.assert_not_called()


def test_access_unit_deadline_does_not_restart_for_each_chunk(panel):
  assert panel.display.open()
  panel.display._next_status_at = panel.clock.now + 5
  panel.writes.clear()
  original_write = panel.ep_out.write.side_effect

  def delayed_write(payload, *, timeout):
    result = original_write(payload, timeout=timeout)
    panel.clock.now += 0.6
    return result

  panel.ep_out.write.side_effect = delayed_write
  assert not panel.display.send_access_unit(bytes(32768 * 3))
  assert len([packet for packet, _timeout in panel.writes if packet[0] == 121]) == 2
  assert not panel.display.connected


def test_reopen_after_partial_upload_stops_old_stream_without_usb_reset(panel):
  assert panel.display.open()
  original_write = panel.ep_out.write.side_effect
  panel.ep_out.write.side_effect = usb.core.USBError("No such device", errno=19)
  assert not panel.display.send_access_unit(b"frame")
  replacement = Mock(idProduct=0x0092)
  panel.find.return_value = replacement
  panel.descriptors.side_effect = [Mock(), panel.ep_out, panel.ep_in]
  panel.ep_out.write.side_effect = original_write
  panel.writes.clear()

  assert panel.display.open()

  assert panel.display.device is replacement
  assert panel.writes[0][0][0] == 123
  assert panel.display.send_access_unit(b"fresh SPS/PPS/IDR frame")
  replacement.reset.assert_not_called()


@pytest.mark.parametrize("stop_fails", [False, True])
def test_close_stops_stream_and_releases_handle_even_on_stop_failure(panel, stop_fails):
  assert panel.display.open()
  panel.writes.clear()
  if stop_fails:
    original_write = panel.ep_out.write.side_effect

    def failed_write(payload, *, timeout):
      original_write(payload, timeout=timeout)
      raise usb.core.USBError("No such device", errno=19)

    panel.ep_out.write.side_effect = failed_write
  panel.display.close()
  assert [packet[0] for packet, _timeout in panel.writes] == [123, 102]
  assert panel.writes[-1][0][512:] == display_module.black_overlay_png()
  assert all(timeout <= 100 for _packet, timeout in panel.writes)
  assert not panel.display.connected
  panel.dispose.assert_called_once_with(panel.device)
  panel.display.close()
  assert panel.dispose.call_count == 1
  panel.device.reset.assert_not_called()


def test_shutdown_does_not_restart_timeout_budget_for_black_overlay(panel):
  assert panel.display.open()
  panel.writes.clear()
  original_write = panel.ep_out.write.side_effect

  def delayed_stop(payload, *, timeout):
    result = original_write(payload, timeout=timeout)
    panel.clock.now += 0.100
    return result

  panel.ep_out.write.side_effect = delayed_stop
  panel.display.close()
  assert [packet[0] for packet, _timeout in panel.writes] == [123]
  assert not panel.display.connected
  panel.dispose.assert_called_once_with(panel.device)
