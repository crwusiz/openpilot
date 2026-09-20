from openpilot.selfdrive.addon.cluster import cluster_logging


def test_logger_reuses_session_handle(tmp_path, monkeypatch):
  log_path = tmp_path / "cluster_debug.log"
  monkeypatch.setattr(cluster_logging, "LOG_FILE", str(log_path))

  cluster_logging.initialize_log()
  handle = cluster_logging._log_handle
  try:
    cluster_logging.flog("first")
    cluster_logging.flog("second")
    assert cluster_logging._log_handle is handle
  finally:
    cluster_logging.close_log()

  lines = log_path.read_text().splitlines()
  assert "Cluster Session Started" in lines[0]
  assert lines[1].endswith("first")
  assert lines[2].endswith("second")
