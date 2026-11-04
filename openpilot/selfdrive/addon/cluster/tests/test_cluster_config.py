import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

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
  assert Path(config.font_bold).name == "NotoSansKR-Bold.ttf"
  assert config.font_regular == config.font_bold
  assert config.usb_h264_fps == 20
  assert config.usb_h264_bitrate == 4_666_667
  assert config.usb_h264_gop == 1
  assert config.usb_h264_slice_max_bytes == 4096
  assert config.usb_h264_device.endswith("qcom_vidc-video-index1")
  expected_reads = [call("ClusterDisplayTransport")]
  if transport == "network":
    expected_reads.append(call("ClusterHdmiProfile"))
    assert config.hdmi_profile == "1920x480"
  assert params.get.call_args_list == expected_reads
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


@pytest.mark.parametrize("profile,height", [(None, 480), ("1920x480", 480), ("1920x720", 720),
                                           (b"1920x720", 720), ("unknown", 480)])
@pytest.mark.parametrize("transport", ["network", "usb"])
def test_hdmi_profile_selects_native_scene_dimensions_only_for_network(monkeypatch, profile, height, transport):
  values = {"ClusterDisplayTransport": transport, "ClusterHdmiProfile": profile}
  params = Mock(get=Mock(side_effect=values.get), get_bool=Mock(return_value=True))
  monkeypatch.setitem(sys.modules, "openpilot.common.params", SimpleNamespace(Params=lambda: params))
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=Mock()))
  spec = importlib.util.spec_from_file_location("cluster_config_under_test", Path(__file__).parents[1] / "cluster_config.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  config = module.ClusterConfig()

  assert (config.width, config.height) == (1920, height if transport == "network" else 462)
  assert config.camera_panel_width > 0
  assert config.camera_panel_height == config.height - 20
  if transport == "network":
    assert config.hdmi_profile == f"1920x{height}"
