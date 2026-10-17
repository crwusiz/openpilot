import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def policy(monkeypatch):
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=Mock()))
  # Exercise the real sysfs/VID-PID helpers without importing device hardware
  # backends and compiled cereal bindings on the desktop test interpreter.
  usb_spec = importlib.util.spec_from_file_location("cluster_policy_usb_under_test",
                                                   Path(__file__).parents[4] / "common/hardware/usb.py")
  usb = importlib.util.module_from_spec(usb_spec)
  usb_spec.loader.exec_module(usb)
  monkeypatch.setitem(sys.modules, "openpilot.common.hardware.usb", usb)
  spec = importlib.util.spec_from_file_location("cluster_policy_under_test", Path(__file__).parents[1] / "cluster_policy.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module, "usb_devices", Mock(return_value=[]))
  return module


def make_params(transport, **states):
  values = {"ClusterEnable": True, **states}
  params = Mock()
  params.get.return_value = transport
  params.get_bool.side_effect = lambda key: values.get(key, False)
  params.put_bool.side_effect = lambda key, value, **kwargs: values.update({key: value})
  params.state = values
  return params


@pytest.mark.parametrize("transport", [None, "usb"])
@pytest.mark.parametrize("states", [
  {"ChestnutLoading": True},
  {"ChestnutActive": True},
  {"ChestnutLoading": True, "ChestnutActive": True},
], ids=["loading", "active", "loading-and-active"])
def test_usb_disabled_before_and_during_gpu_use(policy, monkeypatch, transport, states):
  monkeypatch.setattr(policy, "_chestnut_enumerated", Mock(return_value=True))
  params = make_params(transport, **states)
  assert not policy.enforce_cluster_transport(params)
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)
  assert not params.get_bool("ClusterEnable")
  policy.cloudlog.warning.assert_called_once()
  params.put.assert_not_called()


def test_network_keeps_running_with_egpu(policy):
  params = make_params("network", ChestnutLoading=True, ChestnutActive=True)
  assert policy.enforce_cluster_transport(params)
  params.get_bool.assert_not_called()
  assert params.get_bool("ClusterEnable")
  params.put_bool.assert_not_called()
  policy.usb_devices.assert_not_called()


def test_hotplug_disables_usb_without_reenabling_on_disconnect(policy, monkeypatch):
  enumerated = Mock(return_value=False)
  monkeypatch.setattr(policy, "_chestnut_enumerated", enumerated)
  params = make_params("usb")
  assert policy.enforce_cluster_transport(params)
  params.put_bool.assert_not_called()
  # Loading starts with cable detection; confirm enumerated identity before
  # changing the saved setting. An active GPU remains protected immediately.
  params.state["ChestnutLoading"] = True
  # A Type-C cable alone can start modeld's loading wait. It is not a GPU.
  assert policy.enforce_cluster_transport(params)
  assert params.get_bool("ClusterEnable")
  enumerated.return_value = True
  assert not policy.enforce_cluster_transport(params)
  params.state.update(ChestnutLoading=False, ChestnutActive=True)
  assert not policy.enforce_cluster_transport(params)
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)
  params.state["ChestnutActive"] = False
  assert policy.enforce_cluster_transport(params)
  assert not params.get_bool("ClusterEnable")


def test_running_usb_is_checked_even_if_network_was_just_selected(policy, monkeypatch):
  monkeypatch.setattr(policy, "_chestnut_enumerated", Mock(return_value=True))
  params = make_params("network", ChestnutLoading=True)
  assert not policy.enforce_cluster_transport(params, "usb")
  params.get.assert_not_called()
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)


@pytest.mark.parametrize("transport", [None, "usb"])
@pytest.mark.parametrize("enabled", [False, True])
def test_cable_only_loading_does_not_change_saved_usb_toggle_across_boot_checks(policy, transport, enabled):
  params = make_params(transport, ClusterEnable=enabled, ChestnutLoading=True)
  for _ in range(3):
    assert policy.enforce_cluster_transport(params)
  params.state["ChestnutLoading"] = False
  assert policy.enforce_cluster_transport(params)
  assert params.get_bool("ClusterEnable") is enabled
  params.put_bool.assert_not_called()
  policy.cloudlog.warning.assert_not_called()


def test_active_gpu_still_blocks_usb_during_temporary_enumeration_loss(policy):
  params = make_params("usb", ChestnutActive=True)
  assert not policy.enforce_cluster_transport(params)
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)
  policy.usb_devices.assert_not_called()


@pytest.mark.parametrize("vendor,product", [(0xADD1, 0x0001), (0x3801, 0x0001), (0x174C, 0x2464), (0x174C, 0x2463)])
def test_real_or_bootloader_chestnut_loading_disables_usb(policy, tmp_path, vendor, product):
  device = tmp_path / "1-1"
  device.mkdir()
  (device / "idVendor").write_text(f"{vendor:04x}\n")
  (device / "idProduct").write_text(f"{product:04x}\n")
  # Firmware product strings are deliberately not required during boot/flash.
  policy.usb_devices.return_value = [device]
  params = make_params("usb", ChestnutLoading=True)
  assert not policy.enforce_cluster_transport(params)
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)


def test_turzx_hub_and_unplugged_or_incomplete_devices_are_not_chestnut(policy, tmp_path):
  devices = []
  for index, (vendor, product) in enumerate((("1cbe", "0092"), ("2109", "0817"), ("add1", "0092"), ("invalid", "0001"))):
    device = tmp_path / str(index)
    device.mkdir()
    (device / "idVendor").write_text(vendor)
    (device / "idProduct").write_text(product)
    devices.append(device)
  missing = tmp_path / "disconnected"
  devices.append(missing)
  incomplete = tmp_path / "incomplete"
  incomplete.mkdir()
  (incomplete / "idVendor").write_text("add1")
  devices.append(incomplete)
  policy.usb_devices.return_value = devices
  params = make_params("usb", ChestnutLoading=True)
  assert policy.enforce_cluster_transport(params)
  assert params.get_bool("ClusterEnable")
  params.put_bool.assert_not_called()
