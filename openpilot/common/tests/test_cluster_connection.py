import json
from types import SimpleNamespace

import pytest

from openpilot.common import cluster_connection


def test_cluster_readiness_tracks_transport_and_expires(monkeypatch, tmp_path):
  path = tmp_path / "cluster-status.json"
  now = [20.0]
  monkeypatch.setattr(cluster_connection, "STATUS_FILE", path)
  monkeypatch.setattr(cluster_connection, "time", SimpleNamespace(monotonic=lambda: now[0]))

  assert not cluster_connection.is_cluster_connected("usb")
  cluster_connection.publish_cluster_connection("usb", True)
  assert cluster_connection.is_cluster_connected("usb")
  assert not cluster_connection.is_cluster_connected("network")
  now[0] += cluster_connection.STATUS_MAX_AGE + 0.01
  assert not cluster_connection.is_cluster_connected("usb")

  cluster_connection.publish_cluster_connection("network", True)
  assert cluster_connection.is_cluster_connected("network")
  cluster_connection.publish_cluster_connection("network", False)
  assert not cluster_connection.is_cluster_connected("network")


@pytest.mark.parametrize("timestamp", [None, True, '20.0', -1.0, 21.0, float('nan'), float('inf')])
def test_invalid_heartbeat_cannot_hide_hud(monkeypatch, tmp_path, timestamp):
  path = tmp_path / "cluster-status.json"
  monkeypatch.setattr(cluster_connection, "STATUS_FILE", path)
  monkeypatch.setattr(cluster_connection, "time", SimpleNamespace(monotonic=lambda: 20.0))
  path.write_text(json.dumps({"transport": "network", "connected": True, "updated_at": timestamp}), encoding="utf-8")
  assert not cluster_connection.is_cluster_connected("network")
