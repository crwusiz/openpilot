import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def cluster(monkeypatch):
  config = SimpleNamespace(params=Mock(), display_transport="usb", fps=20, width=10, height=10)
  config.params.get.return_value = "usb"
  display = Mock(connected=True)
  pipeline = Mock()
  pipeline.close.return_value = True
  camera, models, renderer = Mock(), Mock(), Mock()
  policy = Mock(return_value=True)
  prefix = "openpilot.selfdrive.addon.cluster."
  modules = {
    "openpilot.common.swaglog": SimpleNamespace(cloudlog=Mock()),
    prefix + "cluster_config": SimpleNamespace(ClusterConfig=lambda: config),
    prefix + "cluster_logging": SimpleNamespace(close_log=Mock(), flog=Mock(), initialize_log=Mock()),
    prefix + "cluster_display_pipeline": SimpleNamespace(ClusterDisplayPipeline=lambda d: pipeline),
    prefix + "cluster_live_camera": SimpleNamespace(ClusterLiveCamera=lambda c: camera),
    prefix + "cluster_models": SimpleNamespace(ClusterModels=lambda: models),
    prefix + "cluster_renderer": SimpleNamespace(ClusterRenderer=lambda c: renderer),
    prefix + "cluster_policy": SimpleNamespace(enforce_cluster_transport=policy),
  }
  for name, module in modules.items():
    monkeypatch.setitem(sys.modules, name, module)
  spec = importlib.util.spec_from_file_location("cluster_main_under_test", Path(__file__).parents[1] / "main.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  module.create_cluster_display = Mock(return_value=display)
  monkeypatch.setattr(module.signal, "signal", Mock())
  return SimpleNamespace(module=module, config=config, display=display, pipeline=pipeline, policy=policy, camera=camera, models=models)


def test_existing_egpu_prevents_opening_usb_display(cluster):
  cluster.policy.return_value = False
  cluster.module.cluster_main()
  cluster.module.create_cluster_display.assert_not_called()
  cluster.module.close_log.assert_called_once()


def test_hotplug_stops_workers_without_sending_clear_frame(cluster):
  cluster.policy.side_effect = [True, False, False]
  cluster.module.cluster_main()
  cluster.pipeline.close.assert_called_once()
  cluster.pipeline.push.assert_not_called()
  cluster.display.send_image.assert_not_called()
  cluster.display.close.assert_called_once()
  cluster.camera.close.assert_called_once()
  cluster.models.close.assert_called_once()


def test_network_shutdown_still_clears_display(cluster):
  cluster.config.display_transport = "network"
  # A transport change exits the loop normally.
  cluster.module.cluster_main()
  cluster.display.send_image.assert_called_once()
  cluster.display.close.assert_called_once()
