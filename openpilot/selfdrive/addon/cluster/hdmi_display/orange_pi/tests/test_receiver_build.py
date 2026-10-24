import hashlib
from pathlib import Path

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import cluster_receiver, hdmi_display
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.receiver_build import BUILD_FILES, ReceiverVersion, package_digest, source_fingerprint


def _populate_sources(directory):
  for name in BUILD_FILES:
    (directory / name).write_bytes(f"# {name}\n".encode())
  assets = ["assets/fonts/NotoSansKR-Bold.ttf", "assets/icons/steer.png"]
  (directory / "receiver_assets.txt").write_text("\n".join(assets) + "\n", encoding="utf-8")
  for name in assets:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"asset\x00\r\n")


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

  (tmp_path / "control_socket.py").write_bytes(b"# updated socket receiver\n")
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
      (tmp_path / "control_socket.py").write_bytes(b"# changed after process startup\n")
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
  (tmp_path / "h264_protocol.py").unlink()
  original_read = Path.read_bytes

  def read_bytes(path):
    if path.name == "texture_presenter.py":
      raise PermissionError("source unavailable")
    return original_read(path)

  monkeypatch.setattr(Path, "read_bytes", read_bytes)
  snapshot = source_fingerprint(tmp_path)
  assert "h264_protocol.py:missing" in snapshot
  assert "texture_presenter.py:unreadable" in snapshot
  assert "missing=h264_protocol.py | unreadable=texture_presenter.py" in snapshot


def test_asset_fingerprint_changes_and_reports_missing_font(tmp_path):
  _populate_sources(tmp_path)
  startup = source_fingerprint(tmp_path)
  assert "asset_files=2 | missing_assets=none" in startup
  (tmp_path / "assets/icons/steer.png").write_bytes(b"updated icon")
  changed = source_fingerprint(tmp_path)
  assert changed != startup
  # Runtime source hashes stay stable while the asset aggregate changes.
  assert changed.split(" | assets_sha256=")[0] == startup.split(" | assets_sha256=")[0]
  (tmp_path / "assets/fonts/NotoSansKR-Bold.ttf").unlink()
  assert "missing_assets=assets/fonts/NotoSansKR-Bold.ttf" in source_fingerprint(tmp_path)


def test_asset_manifest_cannot_escape_the_receiver_directory(tmp_path):
  _populate_sources(tmp_path)
  (tmp_path / "receiver_assets.txt").write_text("../../private\n", encoding="utf-8")
  assert "assets_sha256=unavailable | missing_assets=invalid_manifest" in source_fingerprint(tmp_path)


def _populate_package(directory):
  _populate_sources(directory)
  for name in ("README.md", "requirements.txt", "cluster-hdmi.service"):
    (directory / name).write_bytes(b"package text\r\n")
  (directory / "scripts").mkdir()
  (directory / "scripts/common.sh").write_bytes(b"#!/bin/bash\r\n")


def test_package_version_normalizes_text_but_includes_binary_assets_and_scripts(tmp_path):
  _populate_package(tmp_path)
  initial = package_digest(tmp_path)
  (tmp_path / "README.md").write_bytes(b"package text\n")
  assert package_digest(tmp_path) == initial
  (tmp_path / "scripts/common.sh").write_bytes(b"# new setup\n")
  assert package_digest(tmp_path) != initial
  changed = package_digest(tmp_path)
  (tmp_path / "assets/icons/steer.png").write_bytes(b"updated icon")
  assert package_digest(tmp_path) != changed


def test_pi_version_reads_the_public_flag_and_tracks_a_successful_update(tmp_path):
  reader = ReceiverVersion(tmp_path)
  (tmp_path / "receiver-version").write_text("20261024123456 " + "a" * 64 + "\n")
  assert reader.sample() == {"pi_version": "20261024123456", "pi_package_sha256": "a" * 64}
  (tmp_path / "receiver-version").write_text("20261025120000 " + "b" * 64 + "\n")
  assert reader.sample() == {"pi_version": "20261025120000", "pi_package_sha256": "b" * 64}


def test_pi_version_uses_a_source_hash_without_a_public_flag(tmp_path):
  _populate_package(tmp_path)
  digest = package_digest(tmp_path)
  reader = ReceiverVersion(tmp_path)
  assert reader.sample() == {"pi_version": digest[:12], "pi_package_sha256": digest}


def test_unreadable_pi_version_is_unknown_and_does_not_break_sensor_sampling(tmp_path):
  assert ReceiverVersion(tmp_path).sample() == {}
