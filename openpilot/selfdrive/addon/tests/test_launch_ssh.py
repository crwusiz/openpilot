import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[4]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is required to check startup SSH preparation")


@pytest.mark.parametrize("ssh_enabled", [None, "0", "1"])
@pytest.mark.parametrize("existing_key", [None, "default-setup-key\n", "personal-github-key\n"])
@pytest.mark.parametrize("service_status", [0, 7])
def test_startup_enables_ssh_and_preserves_existing_keys(tmp_path, ssh_enabled, existing_key, service_status):
  source = (REPO / "launch_chffrplus.sh").read_text(encoding="utf-8")
  function = "function enable_ssh {" + source.split("function enable_ssh {", 1)[1].split("function install_addon_dependencies", 1)[0]
  params_dir = tmp_path / "params with spaces" / "d"
  if ssh_enabled is not None or existing_key is not None:
    params_dir.mkdir(parents=True)
  if ssh_enabled is not None:
    (params_dir / "SshEnabled").write_text(ssh_enabled, encoding="utf-8")
  if existing_key is not None:
    (params_dir / "GithubSshKeys").write_text(existing_key, encoding="utf-8")
    (params_dir / "GithubUsername").write_text("personal-user", encoding="utf-8")
  (tmp_path / "setup keys").write_text("default-setup-key\n", encoding="utf-8")

  script = tmp_path / "check.sh"
  script.write_text(function + '''
sudo() {
  printf '%s\n' "$*" >> "$PWD/calls.log"
  return "$SERVICE_STATUS"
}
enable_ssh "$PWD/params with spaces/d" "$PWD/setup keys"
ssh_result=$?
printf 'startup continued\n'
exit "$ssh_result"
''', encoding="utf-8", newline="\n")
  result = subprocess.run([BASH, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=15,
                          env=dict(os.environ, SERVICE_STATUS=str(service_status)))

  assert result.returncode == 0, result.stderr
  assert "startup continued" in result.stdout
  assert (params_dir / "SshEnabled").read_text() == "1"
  assert (params_dir / "GithubSshKeys").read_text() == (existing_key or "default-setup-key\n")
  assert (params_dir / "GithubUsername").read_text() == ("personal-user" if existing_key else "openpilot")
  assert (tmp_path / "calls.log").read_text().splitlines() == ["systemctl start ssh"]
  assert ("Failed to start SSH; continuing startup" in result.stderr) == bool(service_status)


def test_ssh_preparation_precedes_update_checks_dependencies_and_build():
  source = (REPO / "launch_chffrplus.sh").read_text(encoding="utf-8")
  launch = source.split("function launch {", 1)[1]
  enable_position = launch.index('enable_ssh "$PARAMS_ROOT/d"')
  assert launch.index('PARAMS_ROOT="/data/params"') < enable_position
  assert launch.index("if [ -f /AGNOS ]; then") < enable_position
  for later_step in ('${DIR}/.overlay_init', "agnos_init", "git branch -r", "git status", "install_addon_dependencies", "./build.py", "./manager.py"):
    assert enable_position < launch.index(later_step)
