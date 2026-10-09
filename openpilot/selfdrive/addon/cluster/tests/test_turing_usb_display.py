import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import usb.core

from openpilot.selfdrive.addon.cluster.cluster_jpeg import PreparedFrame
from openpilot.selfdrive.addon.cluster.usb_display import turing_usb_display as display_module


@pytest.fixture
def usb_display(monkeypatch):
  monkeypatch.setattr(display_module, "flog", lambda _message: None)
  monkeypatch.setattr(display_module.time, "sleep", lambda _seconds: None)
  device = Mock(idProduct=0x0092)
  device.is_kernel_driver_active.return_value = True
  ep_out = SimpleNamespace(bEndpointAddress=0x01)
  ep_in = SimpleNamespace(bEndpointAddress=0x81)
  interface = Mock()
  descriptors = Mock(side_effect=[interface, ep_out, ep_in])
  find = Mock(return_value=device)
  claim = Mock()
  dispose = Mock()
  monkeypatch.setattr(display_module.usb.core, "find", find)
  monkeypatch.setattr(display_module.usb.util, "find_descriptor", descriptors)
  monkeypatch.setattr(display_module.usb.util, "claim_interface", claim)
  monkeypatch.setattr(display_module.usb.util, "dispose_resources", dispose)
  vendor = SimpleNamespace(
    send_jpeg=Mock(return_value=b"ok"),
    send_sync_command=Mock(return_value=b"ok"),
    send_brightness_command=Mock(return_value=b"ok"),
    send_frame_rate_command=Mock(return_value=b"ok"),
    _resp_ok=lambda response: response == b"ok",
  )
  monkeypatch.setitem(sys.modules, "library.lcd.lcd_comm_turing_usb", vendor)
  config = SimpleNamespace(usb_fps=20, fps=20, usb_image_timeout_ms=1000, usb_clear_halt_on_timeout=True)
  display = display_module.TuringUsbDisplay(config)
  return SimpleNamespace(display=display, device=device, find=find, claim=claim, dispose=dispose,
                         vendor=vendor, descriptors=descriptors, ep_out=ep_out, ep_in=ep_in)


def test_open_targets_panel_and_detaches_before_configuration(usb_display, monkeypatch):
  state = usb_display
  monkeypatch.setattr(display_module.sys, "platform", "linux")
  operations = Mock()
  operations.attach_mock(state.device.detach_kernel_driver, "detach")
  operations.attach_mock(state.device.set_configuration, "configure")
  operations.attach_mock(state.claim, "claim")

  assert state.display.open()

  state.find.assert_called_once_with(idVendor=0x1CBE, idProduct=0x0092)
  assert [call[0] for call in operations.mock_calls] == ["detach", "configure", "claim"]
  state.claim.assert_called_once_with(state.device, 0)
  state.device.reset.assert_not_called()


@pytest.mark.parametrize("failure", ["configuration", "claim", "interface", "endpoint", "sync"])
def test_failed_initialization_releases_only_display_handle(usb_display, failure):
  state = usb_display
  if failure == "configuration":
    state.device.set_configuration.side_effect = usb.core.USBError("busy", errno=16)
  elif failure == "claim":
    state.claim.side_effect = usb.core.USBError("busy", errno=16)
  elif failure == "interface":
    state.descriptors.side_effect = [None]
  elif failure == "endpoint":
    state.descriptors.side_effect = [Mock(), None, state.ep_in]
  else:
    state.vendor.send_sync_command.return_value = None

  assert not state.display.open()
  assert not state.display.connected
  assert state.display.device is None
  state.dispose.assert_called_once_with(state.device)
  state.device.reset.assert_not_called()


@pytest.mark.parametrize("timeout_exception", [False, True])
def test_repeated_frame_failures_disconnect_without_usb_reset(usb_display, timeout_exception):
  state = usb_display
  assert state.display.open()
  if timeout_exception:
    state.vendor.send_jpeg.side_effect = usb.core.USBTimeoutError("timed out", errno=110)
  else:
    state.vendor.send_jpeg.return_value = None
  frame = PreparedFrame(memoryview(b"jpeg"), 1, 0.001)

  for attempt in range(3):
    assert not state.display.send_prepared(frame)
    assert state.display.connected == (attempt < 2)

  state.dispose.assert_called_once_with(state.device)
  state.device.reset.assert_not_called()
  if timeout_exception:
    assert [call.args[0] for call in state.device.clear_halt.call_args_list] == [0x01, 0x81] * 3


def test_success_after_timeout_resets_failure_count(usb_display):
  state = usb_display
  assert state.display.open()
  state.vendor.send_jpeg.side_effect = [usb.core.USBTimeoutError("timed out", errno=110), b"ok"]
  frame = PreparedFrame(memoryview(b"jpeg"), 1, 0.001)

  assert not state.display.send_prepared(frame)
  assert state.display.send_prepared(frame)
  assert state.display.consecutive_upload_failures == 0
  assert state.display.connected
  state.dispose.assert_not_called()


def test_reconnect_rediscovers_display_after_hub_reenumeration(usb_display):
  state = usb_display
  assert state.display.open()
  state.vendor.send_jpeg.return_value = None
  frame = PreparedFrame(memoryview(b"jpeg"), 1, 0.001)
  for _ in range(3):
    assert not state.display.send_prepared(frame)

  replacement = Mock(idProduct=0x0092)
  state.find.return_value = replacement
  state.descriptors.side_effect = [Mock(), state.ep_out, state.ep_in]

  assert state.display.open()
  assert state.display.device is replacement
  assert state.display.consecutive_upload_failures == 0
  state.dispose.assert_called_once_with(state.device)
  replacement.reset.assert_not_called()
