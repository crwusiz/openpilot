import os
from pathlib import Path
import shutil
import subprocess
import sys

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
  cp -- "$1" "$MOCK_UPLOADED_DIR/$(basename "$1")"
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
Path(os.environ['MOCK_COLLECT_CALLS']).write_text(args.c4_log)
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


def test_failed_c4_upload_does_not_start_pi_collection(sandbox):
  result = _upload(sandbox, MOCK_C4_UPLOAD_FAIL="1")
  assert result.returncode == 1
  assert not (sandbox.parent / "collector-call.txt").exists()
  assert len((sandbox.parent / "upload-names.txt").read_text().splitlines()) == 1


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
