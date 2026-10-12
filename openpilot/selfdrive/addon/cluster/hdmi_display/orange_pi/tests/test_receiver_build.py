import hashlib
from pathlib import Path

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import cluster_receiver, hdmi_display
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.receiver_build import BUILD_FILES, source_fingerprint


def _populate_sources(directory):
  for name in BUILD_FILES:
    (directory / name).write_bytes(f"# {name}\n".encode())


def test_source_snapshot_matches_disk_until_a_dependency_changes(tmp_path):
  _populate_sources(tmp_path)
  startup = source_fingerprint(tmp_path)
  expected = hashlib.sha256()
  for name in BUILD_FILES:
    digest = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    expected.update(f"{name}\0{digest}\n".encode())
  assert startup.startswith(f"source_sha256={expected.hexdigest()}")
  assert "missing=none | unreadable=none" in startup
  assert source_fingerprint(tmp_path) == startup

  (tmp_path / "frame_stream.py").write_bytes(b"# updated socket receiver\n")
  assert source_fingerprint(tmp_path) != startup


def test_receiver_logs_a_single_startup_snapshot_before_display_opens(tmp_path, monkeypatch, caplog):
  _populate_sources(tmp_path)
  startup = source_fingerprint(tmp_path)
  snapshots = []

  def snapshot():
    result = source_fingerprint(tmp_path)
    snapshots.append(result)
    return result

  class Display:
    def open(self):
      (tmp_path / "frame_stream.py").write_bytes(b"# changed after process startup\n")
      return True

    def show_waiting(self):
      return True

    def pump_events(self):
      return False

    def close(self):
      pass

  args = cluster_receiver.parse_args([])
  monkeypatch.setattr(cluster_receiver, "parse_args", lambda: args)
  monkeypatch.setattr(cluster_receiver, "source_fingerprint", snapshot)
  monkeypatch.setattr(cluster_receiver.signal, "signal", lambda *_args: None)
  monkeypatch.setattr(hdmi_display, "HdmiDisplay", lambda **_kwargs: Display())
  with caplog.at_level("INFO", logger="cluster_receiver"):
    cluster_receiver.main()
  assert snapshots == [startup]
  assert f"[CLUSTER_RX_BUILD] startup_{startup}" in caplog.text
  assert source_fingerprint(tmp_path) not in caplog.text


def test_missing_and_unreadable_sources_are_reported_without_preventing_startup(tmp_path, monkeypatch):
  _populate_sources(tmp_path)
  (tmp_path / "tcp_diagnostics.py").unlink()
  original_read = Path.read_bytes

  def read_bytes(path):
    if path.name == "texture_presenter.py":
      raise PermissionError("source unavailable")
    return original_read(path)

  monkeypatch.setattr(Path, "read_bytes", read_bytes)
  snapshot = source_fingerprint(tmp_path)
  assert "tcp_diagnostics.py:missing" in snapshot
  assert "texture_presenter.py:unreadable" in snapshot
  assert "missing=tcp_diagnostics.py | unreadable=texture_presenter.py" in snapshot
