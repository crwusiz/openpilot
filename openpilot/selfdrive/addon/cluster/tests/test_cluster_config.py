import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("transport,size", [(None, (1920, 462)), ("usb", (1920, 462)), ("network", (1920, 480))])
def test_transport_selection_is_preserved_with_egpu(monkeypatch, transport, size):
  params = Mock()
  params.get.return_value = transport
  params.get_bool.return_value = True
  chestnut = Mock(return_value=True)
  monkeypatch.setitem(sys.modules, "openpilot.common.params", SimpleNamespace(Params=lambda: params))
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=Mock()))
  monkeypatch.setitem(sys.modules, "openpilot.common.hardware.usb", SimpleNamespace(is_chestnut_connected=chestnut))
  spec = importlib.util.spec_from_file_location("cluster_config_under_test", Path(__file__).parents[1] / "cluster_config.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  config = module.ClusterConfig()

  assert config.display_transport == (transport or "usb")
  assert (config.width, config.height) == size
  params.put.assert_not_called()

