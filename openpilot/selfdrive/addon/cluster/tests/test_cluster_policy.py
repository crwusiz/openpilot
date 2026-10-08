import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def policy(monkeypatch):
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
  params.state = values
  return params


@pytest.mark.parametrize("transport", [None, "usb"])
@pytest.mark.parametrize("states", [
  {"ChestnutLoading": True},
  {"ChestnutActive": True},
  {"ChestnutLoading": True, "ChestnutActive": True},
], ids=["loading", "active", "loading-and-active"])
def test_usb_disabled_before_and_during_gpu_use(policy, transport, states):
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


def test_hotplug_disables_usb_without_reenabling_on_disconnect(policy):
  params = make_params("usb")
  assert policy.enforce_cluster_transport(params)
  params.put_bool.assert_not_called()
  # The eGPU manager reports hotplug through loading/active Params; the policy
  # does not probe the USB bus from the cluster render loop.
  params.state["ChestnutLoading"] = True
  assert not policy.enforce_cluster_transport(params)
  params.state.update(ChestnutLoading=False, ChestnutActive=True)
  assert not policy.enforce_cluster_transport(params)
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)
  params.state["ChestnutActive"] = False
  assert policy.enforce_cluster_transport(params)
  assert not params.get_bool("ClusterEnable")


def test_running_usb_is_checked_even_if_network_was_just_selected(policy):
  params = make_params("network", ChestnutLoading=True)
  assert not policy.enforce_cluster_transport(params, "usb")
  params.get.assert_not_called()
  params.put_bool.assert_called_once_with("ClusterEnable", False, block=True)
