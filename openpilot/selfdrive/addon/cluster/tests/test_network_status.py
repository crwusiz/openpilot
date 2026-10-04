import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display import network_display, network_status
from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import pack_ack


@pytest.fixture
def status_file(tmp_path, monkeypatch):
  path = tmp_path / "network-status.json"
  clock = SimpleNamespace(monotonic=Mock(return_value=100.0))
  monkeypatch.setattr(network_status, "STATUS_FILE", path)
  monkeypatch.setattr(network_status, "time", clock)
  monkeypatch.setattr(network_display, "time", clock)
  monkeypatch.setattr(network_display, "flog", Mock())
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


def test_sender_publishes_peer_refreshes_on_ack_and_clears_on_send_error(status_file):
  _, clock = status_file
  config = SimpleNamespace(network_bind_host="0.0.0.0", network_port=9200,
                           network_accept_timeout=0.25, network_ack_timeout=2.5, fps=20)
  display = network_display.ClusterNetworkDisplay(config)
  peer = Mock()
  display.listener = Mock()
  display.listener.accept.return_value = (peer, ("192.168.0.84", 59123))
  assert display.open()
  assert network_status.get_connected_pi_ip() == "192.168.0.84"

  def receive_ack(buffer):
    ack = pack_ack(display.sequence + 1)
    buffer[:] = ack
    return len(ack)

  peer.recv_into.side_effect = receive_ack
  clock.monotonic.return_value = 111.0
  assert network_status.get_connected_pi_ip() is None
  frame = SimpleNamespace(jpeg=b"JPEG", prepare_elapsed=0.0, size_kb=1)
  assert display.send_prepared(frame)
  assert network_status.get_connected_pi_ip() == "192.168.0.84"
  peer.sendall.side_effect = ConnectionError("disconnected")
  assert not display.send_prepared(frame)
  assert network_status.get_connected_pi_ip() is None

  display.listener.accept.return_value = (Mock(), ("192.168.0.90", 50000))
  assert display.open()
  assert network_status.get_connected_pi_ip() == "192.168.0.90"
  display.close()
  assert network_status.get_connected_pi_ip() is None


def test_status_io_error_does_not_prevent_peer_connection(status_file, monkeypatch):
  config = SimpleNamespace(network_bind_host="0.0.0.0", network_port=9200,
                           network_accept_timeout=0.25, network_ack_timeout=2.5, fps=20)
  monkeypatch.setattr(network_display, "write_network_status", Mock(side_effect=OSError("read-only filesystem")))
  display = network_display.ClusterNetworkDisplay(config)
  display.listener = Mock(accept=Mock(return_value=(Mock(), ("192.168.0.84", 50000))))
  assert display.open()
  assert display.connected
  assert len([call for call in network_display.flog.call_args_list if "Cannot publish" in call.args[0]]) == 1
