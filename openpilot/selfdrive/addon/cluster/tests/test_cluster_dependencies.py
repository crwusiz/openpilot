import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def bootstrap(monkeypatch):
  path = Path(__file__).parents[1] / "dependencies.py"
  spec = importlib.util.spec_from_file_location("cluster_dependencies_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  values = {"ClusterEnable": True, "ClusterDisplayTransport": "network", "ClusterNetworkCodec": "jpeg", "IsOffroad": False}
  config = SimpleNamespace(display_transport="network", network_codec="jpeg",
                           params=SimpleNamespace(get=values.get, get_bool=lambda key: bool(values.get(key))))
  clock = [0.0]
  def sleep(seconds):
    clock[0] += seconds
    values["ClusterEnable"] = False
  monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
  for name in ("groups_ready", "prepare_groups", "activate_groups", "flog"):
    monkeypatch.setattr(module, name, Mock())
  module.groups_ready.return_value = False
  return SimpleNamespace(module=module, config=config, values=values, clock=clock)


def test_ready_mode_activates_without_installing_even_while_onroad(bootstrap):
  item = bootstrap
  item.module.groups_ready.return_value = True
  assert item.module.ensure_cluster_dependencies(item.config)
  item.module.prepare_groups.assert_not_called()
  item.module.activate_groups.assert_called_once_with(("cluster-jpeg",))


def test_missing_mode_waits_onroad_and_stops_when_disabled(bootstrap):
  item = bootstrap
  assert not item.module.ensure_cluster_dependencies(item.config)
  item.module.prepare_groups.assert_not_called()
  item.module.activate_groups.assert_not_called()
  assert item.clock[0] == 1.0


def test_first_use_prepares_only_selected_groups_while_offroad(bootstrap):
  item = bootstrap
  item.values["IsOffroad"] = True
  def prepare(groups, *, cancel_check):
    assert groups == ("cluster-jpeg",) and not cancel_check()
  item.module.prepare_groups.side_effect = prepare
  assert item.module.ensure_cluster_dependencies(item.config)
  item.module.activate_groups.assert_called_once_with(("cluster-jpeg",))


@pytest.mark.parametrize("codec", ["jpeg", "h264"])
def test_usb_codec_prepares_composition_and_usb_without_network_runtime(bootstrap, codec):
  item = bootstrap
  item.config.display_transport = "usb"
  item.config.usb_codec = codec
  item.values.update(ClusterDisplayTransport="usb", ClusterUsbCodec=codec, ClusterNetworkCodec="h264", IsOffroad=True)

  assert item.module.ensure_cluster_dependencies(item.config)

  assert item.module.prepare_groups.call_args.args == (("cluster-jpeg", "cluster-usb"),)
  item.module.activate_groups.assert_called_once_with(("cluster-jpeg", "cluster-usb"))


def test_usb_codec_change_cancels_preparation_without_activation(bootstrap):
  item = bootstrap
  item.config.display_transport = "usb"
  item.config.usb_codec = "jpeg"
  item.values.update(ClusterDisplayTransport="usb", ClusterUsbCodec="jpeg", IsOffroad=True)

  def prepare(_groups, *, cancel_check):
    item.values["ClusterUsbCodec"] = "h264"
    assert cancel_check()
    raise item.module.DependencyInstallCancelled("USB selection changed")

  item.module.prepare_groups.side_effect = prepare
  assert not item.module.ensure_cluster_dependencies(item.config)
  item.module.prepare_groups.assert_called_once()
  item.module.activate_groups.assert_not_called()


@pytest.mark.parametrize("transport", ["usb", "network"])
def test_inactive_transport_codec_change_does_not_cancel_preparation(bootstrap, transport):
  item = bootstrap
  item.config.display_transport = transport
  item.config.usb_codec = "jpeg"
  item.values.update(ClusterDisplayTransport=transport, ClusterUsbCodec="jpeg", IsOffroad=True)

  def prepare(_groups, *, cancel_check):
    item.values["ClusterNetworkCodec" if transport == "usb" else "ClusterUsbCodec"] = "h264"
    assert not cancel_check()

  item.module.prepare_groups.side_effect = prepare
  assert item.module.ensure_cluster_dependencies(item.config)
  item.module.activate_groups.assert_called_once()


@pytest.mark.parametrize("changed", ["IsOffroad", "ClusterNetworkCodec"])
def test_driving_or_mode_change_cancels_preparation_without_activation(bootstrap, changed):
  item = bootstrap
  item.values["IsOffroad"] = True
  def prepare(_groups, *, cancel_check):
    item.values[changed] = False if changed == "IsOffroad" else "h264"
    assert cancel_check()
    raise item.module.DependencyInstallCancelled("selection changed")
  item.module.prepare_groups.side_effect = prepare
  assert not item.module.ensure_cluster_dependencies(item.config)
  item.module.prepare_groups.assert_called_once()
  item.module.activate_groups.assert_not_called()


def test_failed_install_is_not_repeated_on_every_poll(bootstrap):
  item = bootstrap
  item.values["IsOffroad"] = True
  item.module.prepare_groups.side_effect = item.module.DependencyError("offline")
  def sleep(seconds):
    item.clock[0] += seconds
    if item.clock[0] >= 5:
      item.values["ClusterEnable"] = False
  item.module.time.sleep = sleep
  assert not item.module.ensure_cluster_dependencies(item.config)
  item.module.prepare_groups.assert_called_once()
  item.module.activate_groups.assert_not_called()


def test_failed_activation_is_an_error_instead_of_a_successful_exit(bootstrap):
  item = bootstrap
  item.module.groups_ready.return_value = True
  item.module.activate_groups.side_effect = item.module.DependencyError("native import failed")
  with pytest.raises(item.module.DependencyError, match="native import failed"):
    item.module.ensure_cluster_dependencies(item.config)
  item.module.prepare_groups.assert_not_called()


def test_onroad_wait_emits_periodic_liveness_without_installing(bootstrap):
  item = bootstrap
  def sleep(seconds):
    item.clock[0] += seconds
    if item.clock[0] > 65:
      item.values["ClusterEnable"] = False
  item.module.time.sleep = sleep
  assert not item.module.ensure_cluster_dependencies(item.config)
  item.module.prepare_groups.assert_not_called()
  messages = [call.args[0] for call in item.module.flog.call_args_list if "Still waiting" in call.args[0]]
  assert len(messages) == 2
  assert all("offroad=False" in message and "cluster-jpeg" in message for message in messages)
