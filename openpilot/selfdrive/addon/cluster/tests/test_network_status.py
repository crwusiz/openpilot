import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display import network_status


@pytest.fixture
def status_file(tmp_path, monkeypatch):
  path = tmp_path / "network-status.json"
  clock = SimpleNamespace(monotonic=Mock(return_value=100.0))
  monkeypatch.setattr(network_status, "STATUS_FILE", path)
  monkeypatch.setattr(network_status, "time", clock)
  return path, clock


def test_connection_state_expires_and_clears_on_disconnect(status_file):
  path, clock = status_file
  assert network_status.get_connected_pi_ip() is None
  network_status.write_network_status("192.168.0.84")
  assert network_status.get_connected_pi_ip() == "192.168.0.84"
  clock.monotonic.return_value = 111.0
  assert network_status.get_connected_pi_ip() is None
  network_status.write_network_status("192.168.0.90")
  assert network_status.get_connected_pi_ip() == "192.168.0.90"
  network_status.write_network_status(None)
  assert network_status.get_connected_pi_ip() is None
  assert json.loads(path.read_text())["connected"] is False


@pytest.mark.parametrize("contents", [
  "{", "null", "[]",
  '{"connected":true,"ip":"192.168.0.84"}',
  '{"connected":true,"ip":"192.168.0.84","updated_at":101}',
  '{"connected":true,"ip":"192.168.0.84","updated_at":true}',
  '{"connected":true,"ip":"192.168.0.84","updated_at":NaN}',
  '{"connected":true,"ip":"192.168.0.84","updated_at":Infinity}',
  '{"connected":1,"ip":"192.168.0.84","updated_at":100}',
  '{"connected":true,"ip":"hostname;command","updated_at":100}',
  '{"connected":true,"ip":1234,"updated_at":100}',
])
def test_bad_state_is_not_displayed_or_used_for_ssh(status_file, contents):
  path, _ = status_file
  path.write_text(contents, encoding="utf-8")
  assert network_status.get_connected_pi_ip() is None


def test_failed_atomic_write_preserves_previous_state_and_removes_temporary_file(status_file, monkeypatch):
  path, _ = status_file
  network_status.write_network_status("192.168.0.84")
  monkeypatch.setattr(network_status.os, "replace", Mock(side_effect=OSError("disk error")))
  with pytest.raises(OSError, match="disk error"):
    network_status.write_network_status("192.168.0.90")
  assert network_status.get_connected_pi_ip() == "192.168.0.84"
  assert list(path.parent.glob(f".{path.name}.*")) == []


def test_status_io_error_does_not_prevent_peer_connection(status_file, monkeypatch):
  from openpilot.selfdrive.addon.cluster.hdmi_display import network_h264

  log = Mock()
  monkeypatch.setattr(network_h264, "write_network_status", Mock(side_effect=OSError("disk error")))
  monkeypatch.setattr(network_h264, "flog", log)
  network_h264.publish_status("192.168.0.84")
  assert "Cannot publish H.264 connection status" in log.call_args.args[0]
