import faulthandler
import signal

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


def test_python_crash_is_separate_from_debug_and_survives_fresh_sessions(crash_logs, capsys):
  cluster_logging.initialize_crash_log()
  cluster_logging.initialize_log()
  cluster_logging.close_log()  # cluster_main closes before the launcher catches.
  try:
    raise ImportError("native module unavailable")
  except ImportError as error:
    cluster_logging.record_crash(error, "cluster imports")
  cluster_logging.close_crash_log()
  summary = (crash_logs / "cluster_debug.log").read_text()
  assert "stage=cluster imports" in summary and "pid=" in summary
  assert "ImportError: native module unavailable" in summary
  assert f"crash_log={cluster_logging.CRASH_LOG_FILE}" in summary
  assert "Traceback (most recent call last)" not in summary
  assert "Traceback (most recent call last)" in capsys.readouterr().err

  for _ in range(3):
    cluster_logging.initialize_crash_log()
    cluster_logging.initialize_log()
    cluster_logging.close_log()
    cluster_logging.close_crash_log()

  session = (crash_logs / "cluster_debug.log").read_text()
  assert "Most recent previous cluster failure" not in session
  assert "stage=cluster imports" not in session
  assert "Traceback (most recent call last)" not in session
  assert "ImportError: native module unavailable" not in session
  assert "CLUSTER_PROCESS_START" not in session
  persistent = (crash_logs / "cluster_crash.log").read_text()
  assert persistent.count("ImportError: native module unavailable") == 1
  assert "Traceback (most recent call last)" in persistent


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


def test_native_failure_is_preserved_without_copying_into_next_debug_session(crash_logs):
  trace = "Fatal Python error: Aborted\n" + "native frame\n" * 2000
  (crash_logs / "cluster_crash.log").write_text(trace)
  cluster_logging.initialize_crash_log()
  cluster_logging.initialize_log()
  cluster_logging.close_log()
  session = (crash_logs / "cluster_debug.log").read_text()
  assert "Most recent previous cluster failure" not in session
  assert "Fatal Python error: Aborted" not in session
  assert "native frame" not in session
  assert (crash_logs / "cluster_crash.log").read_text().startswith(trace)


def test_unwritable_crash_path_does_not_mask_original_failure(crash_logs, monkeypatch):
  monkeypatch.setattr(cluster_logging, "CRASH_LOG_FILE", str(crash_logs / "missing" / "cluster_crash.log"))
  cluster_logging.initialize_crash_log()
  cluster_logging.record_crash(RuntimeError("original failure"), "test")
  assert "original failure" in (crash_logs / "cluster_debug.log").read_text()


def test_crash_prints_original_chained_error_to_terminal_even_when_both_logs_unwritable(crash_logs, monkeypatch, capsys):
  monkeypatch.setattr(cluster_logging, "LOG_FILE", str(crash_logs / "missing/cluster_debug.log"))
  monkeypatch.setattr(cluster_logging, "CRASH_LOG_FILE", str(crash_logs / "missing/cluster_crash.log"))
  try:
    try:
      raise OSError("encoder device unavailable")
    except OSError as cause:
      raise RuntimeError("USB H.264 initialization failed") from cause
  except RuntimeError as error:
    cluster_logging.record_crash(error, "cluster startup or main loop")
  output = capsys.readouterr().err
  assert "[CLUSTER_CRASH] stage=cluster startup or main loop" in output
  assert "OSError: encoder device unavailable" in output
  assert "RuntimeError: USB H.264 initialization failed" in output


def test_important_events_flush_to_terminal_but_frame_performance_stays_in_file(crash_logs, capsys):
  cluster_logging.flog("[CLUSTER_START] transport=usb codec=h264")
  cluster_logging.flog("[CLUSTER_USB_H264_ERROR] hardware open failed")
  cluster_logging.flog("[CLUSTER_MAIN] Display mode changed; restarting cluster.")
  cluster_logging.flog("[CLUSTER_MAIN_PERF] fps=20")
  output = capsys.readouterr().err
  assert "transport=usb codec=h264" in output
  assert "hardware open failed" in output
  assert "restarting cluster" in output
  assert "CLUSTER_MAIN_PERF" not in output
  assert "CLUSTER_MAIN_PERF" in (crash_logs / "cluster_debug.log").read_text()


def test_parent_reports_native_signal_and_only_matching_pid_fatal_stack(crash_logs, capsys):
  (crash_logs / "cluster_crash.log").write_text(
    "[CLUSTER_PROCESS_START] pid=11 native_bootstrap=1\nFatal Python error: Aborted\nold native stack\n"
    + "[CLUSTER_PROCESS_START] pid=22 native_bootstrap=1\nFatal Python error: Aborted\ncurrent native stack\n")
  enabled = faulthandler.is_enabled()
  cluster_logging.record_process_exit(22, -signal.SIGABRT)
  output = capsys.readouterr().err
  assert "[CLUSTER_PROCESS_EXIT] pid=22" in output
  assert "reason=SIGABRT" in output
  assert "current native stack" in output
  assert "old native stack" not in output
  assert faulthandler.is_enabled() is enabled
  persistent = (crash_logs / "cluster_crash.log").read_text()
  assert "stage=process exit" in persistent
  assert persistent.count("current native stack") == 1
  collected = (crash_logs / "cluster_debug.log").read_text()
  assert "[CLUSTER_PROCESS_EXIT] pid=22" in collected and "reason=SIGABRT" in collected
  assert "current native stack" not in collected and "old native stack" not in collected
  assert "Fatal Python error" not in collected
  assert cluster_logging.CRASH_LOG_FILE in collected


def test_native_exit_summary_is_in_debug_without_a_cluster_restart(crash_logs):
  cluster_logging.initialize_log()
  cluster_logging.flog("last live cluster frame")
  cluster_logging.close_log()
  (crash_logs / "cluster_crash.log").write_text(
    "[CLUSTER_PROCESS_START] pid=321 native_bootstrap=1\n"
    + "Fatal Python error: Segmentation fault\nCurrent thread: native send_frame\n")
  cluster_logging.record_process_exit(321, -signal.SIGSEGV)
  collected = (crash_logs / "cluster_debug.log").read_text()
  assert collected.index("last live cluster frame") < collected.index("[CLUSTER_PROCESS_EXIT]")
  assert "pid=321" in collected and "reason=SIGSEGV" in collected
  assert "Fatal Python error: Segmentation fault" not in collected and "native send_frame" not in collected
  assert cluster_logging.CRASH_LOG_FILE in collected
  assert "native send_frame" in (crash_logs / "cluster_crash.log").read_text()


def test_full_native_trace_survives_exit_and_restart_but_terminal_output_stays_bounded(crash_logs, capsys):
  trace = ("Fatal Python error: Segmentation fault\nimportant early native frame\n"
           + "native worker frame\n" * 1100 + "last native frame\n")
  (crash_logs / "cluster_crash.log").write_text("[CLUSTER_PROCESS_START] pid=42 native_bootstrap=1\n" + trace)
  cluster_logging.record_process_exit(42, -signal.SIGSEGV)
  collected = (crash_logs / "cluster_debug.log").read_text()
  assert "important early native frame" not in collected and "last native frame" not in collected
  persistent = (crash_logs / "cluster_crash.log").read_text()
  assert "important early native frame" in persistent and "last native frame" in persistent
  assert persistent.count("Fatal Python error") == 1
  terminal = capsys.readouterr().err
  assert "Fatal Python error: Segmentation fault" in terminal
  assert len(terminal.encode()) < cluster_logging.MAX_CONSOLE_CRASH_BYTES + 1024
  cluster_logging.initialize_log()
  cluster_logging.close_log()
  next_session = (crash_logs / "cluster_debug.log").read_text()
  assert "important early native frame" not in next_session and "last native frame" not in next_session
  assert (crash_logs / "cluster_crash.log").read_text() == persistent


def test_parent_exit_metadata_starts_on_a_new_line_after_an_incomplete_native_stack(crash_logs):
  trace = "[CLUSTER_PROCESS_START] pid=4595 native_bootstrap=1\nFatal Python error: Segmentation fault\n  File "
  (crash_logs / "cluster_crash.log").write_text(trace)
  cluster_logging.record_process_exit(4595, -signal.SIGSEGV)
  persistent = (crash_logs / "cluster_crash.log").read_text()
  assert persistent.startswith(trace + "\n[")
  assert persistent.count("Fatal Python error") == 1
  assert "pid=4595 exit_code=-11 reason=SIGSEGV" in persistent


def test_python_summary_stays_on_one_line_without_large_or_chained_tracebacks(crash_logs, capsys):
  try:
    try:
      raise OSError("original native dependency error")
    except OSError as cause:
      raise RuntimeError("multiline failure\n" + "long detail " * 2000 + " end of error") from cause
  except RuntimeError as error:
    cluster_logging.record_crash(error, "test")
  summary = (crash_logs / "cluster_debug.log").read_text()
  assert len(summary.splitlines()) == 1
  assert len(summary.encode()) < 2048
  assert "RuntimeError: multiline failure" in summary
  assert "end of error" in summary
  assert "original native dependency error" not in summary
  assert "original native dependency error" in (crash_logs / "cluster_crash.log").read_text()
  assert "original native dependency error" in capsys.readouterr().err


@pytest.mark.parametrize("code,expected_stop,crash", [(0, False, False), (-signal.SIGINT, True, False),
                                                  (1, False, True), (-signal.SIGSEGV, False, True), (-9, True, True)])
def test_parent_reports_exit_without_needing_child_traceback(crash_logs, capsys, code, expected_stop, crash):
  cluster_logging.record_process_exit(123, code, expected_stop)
  output = capsys.readouterr().err
  assert "[CLUSTER_PROCESS_EXIT] pid=123" in output
  assert f"exit_code={code}" in output
  assert ("[CLUSTER_CRASH]" in (crash_logs / "cluster_crash.log").read_text()) is crash
  collected = (crash_logs / "cluster_debug.log").read_text()
  assert "[CLUSTER_PROCESS_EXIT] pid=123" in collected
  assert ("[CLUSTER_CRASH]" in collected) is crash


def test_parent_exit_is_visible_even_if_crash_log_cannot_be_written(crash_logs, monkeypatch, capsys):
  monkeypatch.setattr(cluster_logging, "CRASH_LOG_FILE", str(crash_logs / "missing/cluster_crash.log"))
  cluster_logging.record_process_exit(123, 1)
  assert "exit_code=1 reason=error_exit" in capsys.readouterr().err
  assert "exit_code=1 reason=error_exit" in (crash_logs / "cluster_debug.log").read_text()


def test_unwritable_upload_log_does_not_prevent_persistent_crash_record(crash_logs, monkeypatch, capsys):
  monkeypatch.setattr(cluster_logging, "LOG_FILE", str(crash_logs / "missing/cluster_debug.log"))
  cluster_logging.record_process_exit(123, -signal.SIGSEGV)
  assert "reason=SIGSEGV" in capsys.readouterr().err
  assert "reason=SIGSEGV" in (crash_logs / "cluster_crash.log").read_text()


def test_unwritable_debug_log_does_not_prevent_python_crash_trace(crash_logs, monkeypatch, capsys):
  monkeypatch.setattr(cluster_logging, "LOG_FILE", str(crash_logs / "missing/cluster_debug.log"))
  try:
    raise ValueError("original Python failure")
  except ValueError as error:
    cluster_logging.record_crash(error, "test")
  persistent = (crash_logs / "cluster_crash.log").read_text()
  assert "Traceback (most recent call last)" in persistent
  assert "ValueError: original Python failure" in persistent
  assert "ValueError: original Python failure" in capsys.readouterr().err
