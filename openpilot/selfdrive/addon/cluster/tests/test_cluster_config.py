import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("transport,size", [(None, (1920, 462)), ("usb", (1920, 462)), ("network", (1920, 480))])
def test_transport_selection_is_preserved_with_egpu(monkeypatch, transport, size):
  monkeypatch.delenv("FPS", raising=False)
  monkeypatch.delenv("CLUSTER_FPS", raising=False)
  params = Mock()
  params.get.side_effect = lambda key: transport if key == "ClusterDisplayTransport" else None
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
  assert config.fps == 20
  assert config.jpeg_quality == 68
  assert config.network_jpeg_quality == 68
  assert config.network_codec == "jpeg"
  assert Path(config.font_bold).name == "NotoSansKR-Bold.ttf"
  assert config.font_regular == config.font_bold
  assert config.network_max_in_flight == 2
  assert config.network_max_in_flight_bytes == 512 * 1024
  assert config.network_max_frame_age == config.network_max_ack_age == 0.25
  params.put.assert_not_called()


@pytest.mark.parametrize("cluster_fps,expected", [(None, 20), ("30", 30), ("120", 60), ("0", 1)])
def test_network_fps_is_independent_of_the_main_ui(monkeypatch, cluster_fps, expected):
  monkeypatch.setenv("FPS", "60")
  monkeypatch.delenv("CLUSTER_FPS", raising=False)
  if cluster_fps is not None:
    monkeypatch.setenv("CLUSTER_FPS", cluster_fps)
  params = Mock()
  params.get.side_effect = lambda key: "network" if key == "ClusterDisplayTransport" else None
  monkeypatch.setitem(sys.modules, "openpilot.common.params", SimpleNamespace(Params=lambda: params))
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=Mock()))
  spec = importlib.util.spec_from_file_location("cluster_config_under_test", Path(__file__).parents[1] / "cluster_config.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  assert module.ClusterConfig().fps == expected


@pytest.mark.parametrize("transport", ["network", "usb"])
@pytest.mark.parametrize("stored,expected,invalid", [
  (None, "jpeg", False), ("", "jpeg", False), ("jpeg", "jpeg", False),
  ("h264", "h264", False), (b"h264", "h264", False), (" H264 ", "h264", False),
  (" JPEG ", "jpeg", False), ("vp9", "jpeg", True), (b"\xff", "jpeg", True),
])
def test_network_codec_normalizes_saved_value_without_changing_usb(monkeypatch, transport, stored, expected, invalid):
  monkeypatch.delenv("CLUSTER_FPS", raising=False)
  params = Mock()
  params.get.side_effect = {"ClusterDisplayTransport": transport, "ClusterNetworkCodec": stored}.get
  params.get_bool.return_value = True
  log = Mock()
  monkeypatch.setitem(sys.modules, "openpilot.common.params", SimpleNamespace(Params=lambda: params))
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=log))
  spec = importlib.util.spec_from_file_location("cluster_config_codec_under_test", Path(__file__).parents[1] / "cluster_config.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  config = module.ClusterConfig()

  assert config.network_codec == expected
  assert config.display_transport == transport
  assert (config.width, config.height) == ((1920, 480) if transport == "network" else (1920, 462))
  assert config.fps == config.usb_fps == 20
  assert config.jpeg_quality == config.network_jpeg_quality == 68
  assert log.warning.call_count == int(transport == "network" and invalid)
  params.put.assert_not_called()
