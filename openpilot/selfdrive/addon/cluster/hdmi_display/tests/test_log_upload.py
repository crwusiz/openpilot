import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[6]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is needed to check cluster log uploads")


def _bash_path(path):
  path = Path(path).as_posix()
  return "/" + path[0].lower() + path[2:] if os.name == "nt" else path


@pytest.fixture
def sandbox(tmp_path):
  root = tmp_path / "checkout with spaces"
  scripts = root / "scripts"
  scripts.mkdir(parents=True)
  # Use only mocked FTP, so running this script cannot contact any server.
  (scripts / "log_upload.sh").write_text((ROOT / "scripts" / "log_upload.sh").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
  (scripts / "ftp_upload_utils.sh").write_text('''FTP_HOST=mock
FTP_DEFAULT_DIR=logs
YELLOW='' NC=''
log() { printf '%s: %s\\n' "$1" "$2"; }
get_param() { if [[ $1 == CarName ]]; then printf 'TEST_CAR'; else printf 'test-dongle'; fi; }
ftp_upload_file() {
  printf '%s\\n' "$2" >> "$MOCK_UPLOAD_NAMES"
  printf '%s\\n' "${*:3}" >> "$MOCK_UPLOAD_OPTIONS"
  cp -- "$1" "$MOCK_UPLOADED_DIR/$(basename "$1")"
  chmod u+w "$MOCK_UPLOADED_DIR/$(basename "$1")"
  local archive_name
  archive_name=$(basename "$2")
  cp -- "$1" "$MOCK_UPLOADED_DIR/${archive_name//:/_}"
  chmod u+w "$MOCK_UPLOADED_DIR/${archive_name//:/_}"
  if [[ $(basename "$1") == cluster_debug.log && ${MOCK_INTERRUPT_C4:-} == 1 ]]; then exit 143; fi
  if [[ $(basename "$1") == cluster_pi_debug.log && ${MOCK_INTERRUPT_PI:-} == 1 ]]; then exit 143; fi
  if [[ $(basename "$1") == cluster_pi_debug.log && ${MOCK_FAIL_RETRY_ONLY:-} == 1 && ${*:3} == *'--max-time 10'* ]]; then
    printf 'mock FTP retry error\\n' >&2
    return 1
  fi
  if [[ $(basename "$1") == cluster_debug.log && ${MOCK_C4_UPLOAD_FAIL:-} == 1 ]]; then return 1; fi
  if [[ $(basename "$1") == cluster_pi_debug.log && ${MOCK_PI_UPLOAD_FAIL:-} == 1 ]]; then return 1; fi
}
''', encoding="utf-8", newline="\n")
  collector = root / "openpilot/selfdrive/addon/cluster/hdmi_display/pi_log_collector.py"
  collector.parent.mkdir(parents=True)
  collector.write_text('''import argparse
import os
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--c4-log')
parser.add_argument('--output')
args = parser.parse_args()
if os.name == 'nt' and args.output.startswith('/'):
  args.output = args.output[1] + ':' + args.output[2:]
with Path(os.environ['MOCK_COLLECT_CALLS']).open('a') as calls:
  calls.write(args.c4_log + '\\n')
failed = os.environ.get('MOCK_COLLECT_FAIL') == '1'
content = 'Collection status: failed\\nFailure: SSH unavailable\\n' if failed else 'Collection status: ok\\n[CLUSTER_RX_PERF] display_fps=20\\n'
if os.environ.get('MOCK_COLLECT_SKIP') == '1':
  content = 'Collection status: skipped\\nSkip reason: USB transport only\\n'
Path(args.output).write_text(content)
raise SystemExit(1 if failed else 0)
''', encoding="utf-8", newline="\n")
  (tmp_path / "uploaded").mkdir()
  (tmp_path / "temporary files").mkdir()
  (tmp_path / "cluster_debug.log").write_text("C4 driving log\n", encoding="utf-8")
  return root


def _upload(root, filename="cluster_debug.log", **extra_env):
  parent = root.parent
  env = {**os.environ, "CLUSTER_PYTHON": Path(sys.executable).as_posix(),
         "MOCK_UPLOAD_NAMES": _bash_path(parent / "upload-names.txt"), "MOCK_UPLOADED_DIR": _bash_path(parent / "uploaded"),
         "MOCK_UPLOAD_OPTIONS": _bash_path(parent / "upload-options.txt"),
         "MOCK_COLLECT_CALLS": (parent / "collector-call.txt").as_posix(), "TMPDIR": _bash_path(parent / "temporary files"), **extra_env}
  return subprocess.run([BASH, _bash_path(root / "scripts/log_upload.sh"), _bash_path(parent / filename)],
                        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)


def test_cluster_uploads_two_files_with_identical_prefix_and_cleans_private_temp(sandbox):
  result = _upload(sandbox)
  assert result.returncode == 0, result.stderr
  names = (sandbox.parent / "upload-names.txt").read_text().splitlines()
  assert len(names) == 2
  assert names[0].endswith("_TEST_CAR_test-dongle_cluster_debug.log")
  assert names[1] == names[0].removesuffix("cluster_debug.log") + "cluster_pi_debug.log"
  assert (sandbox.parent / "uploaded/cluster_debug.log").read_text() == "C4 driving log\n"
  assert "display_fps=20" in (sandbox.parent / "uploaded/cluster_pi_debug.log").read_text()
  assert "CLUSTER_PI_LOG_WARNING:" not in result.stdout
  assert not list((sandbox.parent / "temporary files").iterdir())


def test_pi_collection_failure_uploads_failure_report_and_preserves_c4_success(sandbox):
  result = _upload(sandbox, MOCK_COLLECT_FAIL="1")
  assert result.returncode == 0, result.stderr
  assert "CLUSTER_PI_LOG_WARNING:" in result.stdout
  assert "Collection status: failed" in (sandbox.parent / "uploaded/cluster_pi_debug.log").read_text()
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 2
  assert not list((sandbox.parent / "temporary files").iterdir())


def test_usb_collection_skip_uploads_explanation_without_warning(sandbox):
  result = _upload(sandbox, MOCK_COLLECT_SKIP="1")
  assert result.returncode == 0
  assert "CLUSTER_PI_LOG_WARNING:" not in result.stdout
  assert "Collection status: skipped" in (sandbox.parent / "uploaded/cluster_pi_debug.log").read_text()


def test_pi_sidecar_upload_failure_warns_but_c4_upload_remains_success(sandbox):
  result = _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1")
  assert result.returncode == 0
  assert "CLUSTER_PI_LOG_WARNING:" in result.stdout
  assert not list((sandbox.parent / "temporary files").iterdir())


def test_failed_pi_upload_keeps_original_report_and_prefix_for_explicit_retry(sandbox):
  first = _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1")
  assert first.returncode == 0
  pending = list((sandbox.parent / ".cluster-pi-pending").glob("pending.*/cluster_pi_debug.log"))
  assert len(pending) == 1
  original = pending[0].read_text()
  prefix = pending[0].parent.joinpath("prefix").read_text().strip()
  second = _upload(sandbox, MOCK_COLLECT_FAIL="1")
  assert second.returncode == 0
  names = (sandbox.parent / "upload-names.txt").read_text().splitlines()
  assert names[2] == f"/logs/{prefix}_cluster_pi_debug.log"
  assert names[3] != names[0]  # A quick retry cannot overwrite the previous NAS pair.
  assert (sandbox.parent / "uploaded" / f"{prefix}_cluster_pi_debug.log".replace(":", "_")).read_text() == original
  assert len((sandbox.parent / "collector-call.txt").read_text().splitlines()) == 2
  assert "display_fps=20" in original
  assert not list((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))


@pytest.mark.parametrize("interrupt", ["MOCK_INTERRUPT_C4", "MOCK_INTERRUPT_PI"])
def test_interrupted_upload_retains_stable_pair_and_retries_with_bounded_ftp(sandbox, interrupt):
  result = _upload(sandbox, **{interrupt: "1"})
  assert result.returncode != 0
  pending = list((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  assert len(pending) == 1
  original_c4 = pending[0].joinpath("cluster_debug.log").read_text()
  prefix = pending[0].joinpath("prefix").read_text().strip()
  assert pending[0].joinpath("cluster_pi_debug.log").is_file()
  (sandbox.parent / "cluster_debug.log").write_text("new drive\n")
  retry = _upload(sandbox)
  assert retry.returncode == 0, retry.stderr
  assert (sandbox.parent / "uploaded" / f"{prefix}_cluster_debug.log".replace(":", "_")).read_text() == original_c4
  assert (sandbox.parent / "uploaded" / f"{prefix}_cluster_pi_debug.log".replace(":", "_")).is_file()
  options = (sandbox.parent / "upload-options.txt").read_text().splitlines()
  assert any("--retry 0" in option and "--max-time 10" in option for option in options)
  assert not list((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  history = (sandbox.parent / ".cluster-pi-pending/history.log").read_text()
  assert "retry_pi_uploaded" in history
  assert "new drive" not in history


def test_pending_retry_failure_keeps_report_without_preventing_new_c4_upload(sandbox):
  assert _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1").returncode == 0
  second = _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1")
  assert second.returncode == 0
  assert len(list((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))) == 2
  assert "CLUSTER_PI_LOG_WARNING:" in second.stdout


def test_retry_preserves_completed_report_if_interrupted_before_shell_completion_marker(sandbox):
  assert _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1").returncode == 0
  job = next((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  original = job.joinpath("cluster_pi_debug.log").read_text()
  prefix = job.joinpath("prefix").read_text().strip()
  job.joinpath("collection_done").unlink()
  job.joinpath("collection_status").chmod(0o600)
  job.joinpath("collection_status").unlink()
  retry = _upload(sandbox)
  assert retry.returncode == 0
  assert (sandbox.parent / "uploaded" / f"{prefix}_cluster_pi_debug.log".replace(":", "_")).read_text() == original
  assert "CLUSTER_PI_LOG_WARNING:" not in retry.stdout


def test_interrupted_atomic_report_temp_does_not_block_retry_or_consume_outbox_slot(sandbox):
  assert _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1").returncode == 0
  job = next((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  # SIGKILL between NamedTemporaryFile.write and os.replace skips its finally.
  job.joinpath(".cluster_pi_debug.log.aborted").write_text("unfinished atomic report\n")
  retry = _upload(sandbox)
  assert retry.returncode == 0, retry.stderr
  assert not list((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 5


def test_retry_lock_open_does_not_hide_ftp_stderr(sandbox):
  assert _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1").returncode == 0
  retry = _upload(sandbox, MOCK_FAIL_RETRY_ONLY="1")
  assert retry.returncode == 0, retry.stderr
  assert "mock FTP retry error" in retry.stderr
  assert "CLUSTER_PI_LOG_WARNING:" in retry.stdout


def test_retry_does_not_recreate_lock_removed_during_other_uploader_cleanup(sandbox):
  assert _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1").returncode == 0
  job = next((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  report = job.joinpath("cluster_pi_debug.log").read_text()
  job.joinpath("lock").unlink()
  retry = _upload(sandbox)
  assert retry.returncode == 0, retry.stderr
  assert not job.joinpath("lock").exists()
  assert job.joinpath("cluster_pi_debug.log").read_text() == report
  # Retry must skip the disappearing job, then upload only the new C4/Pi pair.
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 4


def test_retry_rechecks_job_removed_by_other_uploader_after_lock_open(sandbox):
  assert _upload(sandbox, MOCK_PI_UPLOAD_FAIL="1").returncode == 0
  job = next((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  mocks = sandbox.parent / "mock native commands"
  mocks.mkdir()
  flock = mocks / "flock"
  flock.write_text('''#!/usr/bin/env bash
if [[ ${1:-} == -n && -d "$MOCK_REMOVE_JOB" ]]; then
  rm -f -- "$MOCK_REMOVE_JOB"/*
  rmdir -- "$MOCK_REMOVE_JOB"
fi
exit 0
''', encoding="utf-8", newline="\n")
  flock.chmod(0o700)
  retry = _upload(sandbox, PATH=f"{_bash_path(mocks)}:/usr/bin:/bin", MOCK_REMOVE_JOB=_bash_path(job))
  assert retry.returncode == 0, retry.stderr
  assert not list((sandbox.parent / ".cluster-pi-pending").glob("pending.*"))
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 4


def test_outbox_job_limit_keeps_previous_reports_and_uploads_new_c4_with_warning(sandbox):
  outbox = sandbox.parent / ".cluster-pi-pending"
  for number in range(16):
    job = outbox / f"pending.saved{number}"
    job.mkdir(parents=True)
    job.joinpath("cluster_pi_debug.log").write_text(f"saved report {number}\n")
  result = _upload(sandbox)
  assert result.returncode == 0
  assert "CLUSTER_PI_LOG_WARNING:" in result.stdout
  assert "outbox_limit_c4_only" in result.stdout
  assert not (sandbox.parent / "collector-call.txt").exists()
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 1
  for number in range(16):
    assert (outbox / f"pending.saved{number}/cluster_pi_debug.log").read_text() == f"saved report {number}\n"


@pytest.mark.parametrize("returncode, output, expected", [
  (0, "CLUSTER_PI_LOG_WARNING: Pi file saved for retry\n", "Pi file saved for retry"),
  (0, "both files uploaded\n", "Upload completed successfully"),
  (1, "C4 upload failed\n", "Upload failed"),
])
def test_c4_settings_upload_preserves_pi_warning_in_completion_dialog(returncode, output, expected):
  path = ROOT / "openpilot/selfdrive/ui/layouts/settings/community.py"
  parsed = ast.parse(path.read_text(encoding="utf-8"))
  helper = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "execute_log_upload")
  layout = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == "CommunityLayout")
  upload = next(node for node in layout.body if isinstance(node, ast.FunctionDef) and node.name == "_upload_log")
  result = subprocess.CompletedProcess([], returncode, output, "")
  runner = Mock(return_value=result)
  dialogs = []
  def dialog(message, _button, callback=None):
    value = SimpleNamespace(message=message, callback=callback)
    dialogs.append(value)
    return value
  namespace = {"subprocess": SimpleNamespace(run=runner), "Path": lambda _path: SimpleNamespace(exists=lambda: True),
               "ConfirmDialog": dialog, "tr": lambda text: text, "DialogResult": SimpleNamespace(CONFIRM=1),
               "gui_app": SimpleNamespace(push_widget=Mock()),
               "threading": SimpleNamespace(Thread=lambda target: SimpleNamespace(start=target))}
  exec(compile(ast.Module(body=[helper, upload], type_ignores=[]), str(path), "exec"), namespace)
  namespace["_upload_log"](None, "/data/log/cluster_debug.log", "cluster_debug.log")
  dialogs[0].callback(1)
  assert dialogs[-1].message == expected
  runner.assert_called_once()


def test_failed_c4_upload_does_not_start_pi_collection(sandbox):
  result = _upload(sandbox, MOCK_C4_UPLOAD_FAIL="1")
  assert result.returncode == 1
  assert not (sandbox.parent / "collector-call.txt").exists()
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 1
  pending = list((sandbox.parent / ".cluster-pi-pending").glob("pending.*/cluster_debug.log"))
  assert len(pending) == 1
  assert pending[0].read_text() == "C4 driving log\n"


def test_unrelated_log_upload_keeps_single_file_behavior(sandbox):
  (sandbox.parent / "navi_debug.log").write_text("navigation log\n")
  result = _upload(sandbox, filename="navi_debug.log")
  assert result.returncode == 0
  assert not (sandbox.parent / "collector-call.txt").exists()
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 1


def test_missing_collector_uploads_explanatory_sidecar_instead_of_blocking_c4(sandbox):
  (sandbox / "openpilot/selfdrive/addon/cluster/hdmi_display/pi_log_collector.py").unlink()
  result = _upload(sandbox)
  assert result.returncode == 0
  assert "CLUSTER_PI_LOG_WARNING:" in result.stdout
  assert "collector missing" in (sandbox.parent / "uploaded/cluster_pi_debug.log").read_text()
