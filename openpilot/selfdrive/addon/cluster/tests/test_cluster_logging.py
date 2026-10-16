import faulthandler

import pytest

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


@pytest.fixture
def crash_logs(tmp_path, monkeypatch):
  cluster_logging.close_log()
  cluster_logging.close_crash_log()
  monkeypatch.setattr(cluster_logging, "LOG_FILE", str(tmp_path / "cluster_debug.log"))
  monkeypatch.setattr(cluster_logging, "CRASH_LOG_FILE", str(tmp_path / "cluster_crash.log"))
  try:
    yield tmp_path
  finally:
    cluster_logging.close_log()
    cluster_logging.close_crash_log()


def test_crash_survives_fresh_session_and_restores_traceback_to_collected_log(crash_logs):
  cluster_logging.initialize_crash_log()
  cluster_logging.initialize_log()
  cluster_logging.close_log()  # cluster_main closes before the launcher catches.
  try:
    raise ImportError("native module unavailable")
  except ImportError as error:
    cluster_logging.record_crash(error, "cluster imports")
  cluster_logging.close_crash_log()

  for _ in range(3):
    cluster_logging.initialize_crash_log()
    cluster_logging.initialize_log()
    cluster_logging.close_log()
    cluster_logging.close_crash_log()

  session = (crash_logs / "cluster_debug.log").read_text()
  assert session.count("Most recent previous cluster failure") == 1
  assert "stage=cluster imports" in session
  assert "Traceback (most recent call last)" in session
  assert "ImportError: native module unavailable" in session
  assert "CLUSTER_PROCESS_START" not in session


def test_crash_file_rotation_keeps_only_one_previous_bounded_file(crash_logs, monkeypatch):
  monkeypatch.setattr(cluster_logging, "MAX_CRASH_LOG_BYTES", 4096)
  monkeypatch.setattr(cluster_logging, "MAX_CRASH_RECORD_BYTES", 1024)
  cluster_logging.initialize_crash_log()
  for index in range(20):
    cluster_logging.record_crash(RuntimeError("x" * 2000 + f" final failure {index}"), "test")
  cluster_logging.close_crash_log()
  files = list(crash_logs.glob("cluster_crash.log*"))
  assert len(files) == 2
  assert all(path.stat().st_size <= 4096 for path in files)
  assert "final failure 19" in (crash_logs / "cluster_crash.log").read_text()


def test_fault_handler_has_live_persistent_descriptor(crash_logs):
  cluster_logging.initialize_crash_log()
  assert cluster_logging._fault_handler_enabled
  faulthandler.dump_traceback(file=cluster_logging._crash_handle)
  assert "test_cluster_logging.py" in (crash_logs / "cluster_crash.log").read_text()


def test_native_failure_excerpt_is_bounded_and_carried_into_next_session(crash_logs):
  (crash_logs / "cluster_crash.log").write_text("Fatal Python error: Aborted\n" + "native frame\n" * 2000)
  cluster_logging.initialize_crash_log()
  cluster_logging.initialize_log()
  cluster_logging.close_log()
  session = (crash_logs / "cluster_debug.log").read_text()
  assert "Most recent previous cluster failure" in session
  assert "Fatal Python error: Aborted" in session
  assert len(session.encode()) < cluster_logging.MAX_PREVIOUS_CRASH_BYTES + 300


def test_unwritable_crash_path_does_not_mask_original_failure(crash_logs, monkeypatch):
  monkeypatch.setattr(cluster_logging, "CRASH_LOG_FILE", str(crash_logs / "missing" / "cluster_crash.log"))
  cluster_logging.initialize_crash_log()
  cluster_logging.record_crash(RuntimeError("original failure"), "test")
  assert "original failure" in (crash_logs / "cluster_debug.log").read_text()
