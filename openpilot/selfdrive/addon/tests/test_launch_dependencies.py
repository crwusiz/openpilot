import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[4]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is required to check launch dependency preparation")


@pytest.mark.parametrize("check_status,install_status,expected,spinner", [(0, 0, 0, []), (1, 0, 0, ["START", "STOP"]),
                                                                       (1, 7, 1, ["START", "STOP"])])
def test_launcher_selects_dependencies_and_always_stops_spinner(tmp_path, check_status, install_status, expected, spinner):
  source = (REPO / "launch_chffrplus.sh").read_text(encoding="utf-8")
  function = "function install_addon_dependencies {" + source.split("function install_addon_dependencies {", 1)[1].split("function agnos_init", 1)[0]
  harness = function + '''
DIR="$PWD/checkout with spaces"
PARAMS_ROOT="$PWD/params with spaces"
start_boot_spinner() { printf 'START\n' >> "$MOCK_LOG"; }
stop_boot_spinner() { printf 'STOP\n' >> "$MOCK_LOG"; }
python3() {
  printf 'CALL' >> "$MOCK_LOG"
  printf '<%s>' "$@" >> "$MOCK_LOG"
  printf '\n' >> "$MOCK_LOG"
  if [[ ${!#} == --check ]]; then return "$CHECK_STATUS"; fi
  return "$INSTALL_STATUS"
}
install_addon_dependencies
'''
  script = tmp_path / "check.sh"
  script.write_text(harness, encoding="utf-8", newline="\n")
  log = tmp_path / "calls.log"
  result = subprocess.run([BASH, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=15,
                          env=dict(os.environ, MOCK_LOG=str(log), CHECK_STATUS=str(check_status), INSTALL_STATUS=str(install_status)))
  assert result.returncode == expected, result.stderr
  lines = log.read_text().splitlines()
  assert [line for line in lines if not line.startswith("CALL")] == spinner
  calls = [line for line in lines if line.startswith("CALL")]
  assert len(calls) == (1 if check_status == 0 else 2)
  assert "/checkout with spaces/openpilot/selfdrive/addon/dependencies.py><selected><--params-root>" in calls[0]
  assert calls[0].endswith("/params with spaces/d><--check>")
  if len(calls) == 2:
    assert calls[1] == calls[0].removesuffix("<--check>")


def test_launch_script_has_valid_bash_syntax():
  result = subprocess.run([BASH, "-n", str(REPO / "launch_chffrplus.sh")], capture_output=True, text=True, timeout=15)
  assert result.returncode == 0, result.stderr
