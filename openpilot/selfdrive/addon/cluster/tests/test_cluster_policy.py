import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def policy(monkeypatch):
  connected = Mock(return_value=False)
  monkeypatch.setitem(sys.modules, "openpilot.common.hardware.usb", SimpleNamespace(is_chestnut_connected=connected))
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=Mock()))
  spec = importlib.util.spec_from_file_location("cluster_policy_under_test", Path(__file__).parents[1] / "cluster_policy.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def make_params(transport, **states):
  values = {"ClusterEnable": True, **states}
  params = Mock()
  params.get.return_value = transport
  params.get_bool.side_effect = lambda key: values.get(key, False)
  params.put_bool.side_effect = lambda key, value, **kwargs: values.update({key: value})
  return params


@pytest.mark.parametrize("transport", [None, "usb"])
@pytest.mark.parametrize("state", ["connected", "ChestnutLoading", "ChestnutActive"])
def test_usb_disabled_before_and_during_gpu_use(policy, transport, state):
  params = make_params(transport, **{state: True})
  policy.is_chestnut_connected.return_value = state == "connected"
  assert not policy.enforce_cluster_transport(params)
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)
  assert not params.get_bool("ClusterEnable")
  if state == "connected":
    policy.is_chestnut_connected.assert_called_once_with(include_bootloader=True)
  params.put.assert_not_called()


def test_network_keeps_running_with_egpu(policy):
  params = make_params("network", ChestnutLoading=True, ChestnutActive=True)
  policy.is_chestnut_connected.return_value = True
  assert policy.enforce_cluster_transport(params)
  assert params.get_bool("ClusterEnable")
  params.put_bool.assert_not_called()
  policy.is_chestnut_connected.assert_not_called()


def test_hotplug_disables_usb_without_reenabling_on_disconnect(policy):
  params = make_params("usb")
  assert policy.enforce_cluster_transport(params)
  params.put_bool.assert_not_called()
  policy.is_chestnut_connected.return_value = True
  assert not policy.enforce_cluster_transport(params)
  assert not policy.enforce_cluster_transport(params)
  params.put_bool.assert_called_once()
  policy.is_chestnut_connected.return_value = False
  assert policy.enforce_cluster_transport(params)
  assert not params.get_bool("ClusterEnable")


def test_running_usb_is_checked_even_if_network_was_just_selected(policy):
  params = make_params("network")
  policy.is_chestnut_connected.return_value = True
  assert not policy.enforce_cluster_transport(params, "usb")
