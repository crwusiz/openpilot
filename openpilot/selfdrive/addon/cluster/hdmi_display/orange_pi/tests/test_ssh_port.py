import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is needed to check SSH port migration")


@pytest.fixture
def ssh_sandbox(tmp_path):
  scripts = tmp_path / "package with spaces" / "scripts"
  shutil.copytree(SCRIPTS, scripts)
  script = scripts / "ssh_port.sh"
  script.write_text(script.read_text(encoding="utf-8")
                    .replace("SSH_DIR=/etc/ssh", 'SSH_DIR="$TEST_SSH_DIR"')
                    .replace("UNIT_DIR=/etc/systemd/system", 'UNIT_DIR="$TEST_UNIT_DIR"')
                    .replace("BACKUP_DIR=/var/lib/cluster-receiver/ssh-port", 'BACKUP_DIR="$TEST_BACKUP_DIR"')
                    .replace("LOCK_FILE=/run/lock/cluster-ssh-port.lock", 'LOCK_FILE="$TEST_LOCK_FILE"'),
                    encoding="utf-8", newline="\n")
  ssh_dir = tmp_path / "ssh configuration"
  (ssh_dir / "sshd_config.d").mkdir(parents=True)
  (ssh_dir / "sshd_config").write_text("#Port 22\nPasswordAuthentication yes\n", encoding="utf-8")
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  mocks = {
    "id": 'printf "0\\n"',
    "flock": "exit 0",
    "sshd": '''shopt -s nullglob
files=("$TEST_SSH_DIR/sshd_config" "$TEST_SSH_DIR"/sshd_config.d/*.conf)
if [[ -n ${TEST_EXTERNAL_INCLUDE:-} ]]; then files+=("$TEST_EXTERNAL_INCLUDE"); fi
if [[ $1 == -t ]]; then
  if [[ ${MOCK_VALIDATE_ERROR:-} == 1 ]] && grep -qiE '^[[:space:]]*port[[:space:]]+9122' "${files[@]}"; then exit 4; fi
  exit 0
fi
[[ $1 == -T ]] || exit 1
awk '
  tolower($1) == "port" { ports[$2] = 1; port_count++ }
  tolower($1) == "listenaddress" { addresses[$2] = 1; address_count++ }
  END {
    if (port_count == 0) ports[22] = 1
    for (port in ports) print "port " port
    if (address_count > 0) {
      for (address in addresses) print "listenaddress " address
    } else {
      for (port in ports) {
        print "listenaddress 0.0.0.0:" port
        print "listenaddress [::]:" port
      }
    }
  }' "${files[@]}"
if [[ ${MOCK_DUPLICATE_ADDRESS:-} == 1 ]]; then printf 'listenaddress 0.0.0.0:9122\\n'; fi
''',
    "systemctl": '''printf '%s\\n' "$*" >> "$TEST_CALL_LOG"
case "$1" in
  is-active) [[ ${MOCK_SOCKET_ACTIVE:-} == 1 && ${!#} == ssh.socket ]];;
  show) [[ ${!#} == "${MOCK_SERVICE_NAME:-ssh.service}" ]] && printf 'loaded\\n' || printf 'not-found\\n';;
  reload|restart)
    if [[ ${MOCK_ACTIVATE_ERROR:-} == 1 && ! -f activation-failed ]]; then touch activation-failed; exit 7; fi
    sshd -T | awk '$1 == "port" { print $2 }' > "$TEST_LISTENERS"
    [[ ${MOCK_KEEP_22:-} != 1 ]] || printf '22\\n' >> "$TEST_LISTENERS";;
esac
''',
    "ss": '''port=22
[[ "$*" != *:9122* ]] || port=9122
if grep -qx "$port" "$TEST_LISTENERS"; then printf 'LISTEN 0 128 0.0.0.0:%s 0.0.0.0:*\\n' "$port"; fi
exit 0
''',
  }
  if os.name == "nt":
    # NTFS cannot model Linux root ownership/modes. Keep directory creation
    # real, and mock only the permission operations at this platform boundary.
    mocks["install"] = '''[[ $1 == -d ]] || exit 1
shift
[[ ${1:-} != -m ]] || shift 2
[[ ${1:-} != -- ]] || shift
mkdir -p -- "$@"
'''
    mocks["chmod"] = "exit 0"
  for name, contents in mocks.items():
    executable = bin_dir / name
    executable.write_text("#!/usr/bin/env bash\n" + contents + "\n", encoding="utf-8", newline="\n")
    executable.chmod(0o755)
  (tmp_path / "listeners").write_text("22\n", encoding="utf-8")
  return tmp_path, scripts, ssh_dir


def _run(sandbox, extra_env=None):
  root, scripts, _ssh_dir = sandbox
  environment = dict(os.environ, TEST_SSH_DIR="ssh configuration", TEST_UNIT_DIR="systemd units", TEST_BACKUP_DIR="ssh backups",
                     TEST_LOCK_FILE="ssh.lock", TEST_CALL_LOG="systemctl.log", TEST_LISTENERS="listeners")
  environment.update(extra_env or {})
  return subprocess.run([BASH, "-c", 'export PATH="$PWD/bin:/usr/bin:/bin"; exec bash "$@"', "ssh-port-test",
                         (scripts.relative_to(root) / "ssh_port.sh").as_posix()], cwd=root, env=environment,
                        capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)


@pytest.mark.parametrize("contents", [
  "#Port 22\nPasswordAuthentication yes\n",
  "Port 22\nPasswordAuthentication yes\n",
  "  pOrT   22  # vehicle SSH\nPasswordAuthentication yes\n",
  "Include sshd_config.d/*.conf\n#Port 22\nMatch User guest\n  PasswordAuthentication no\n",
])
def test_default_or_explicit_22_moves_to_9122_and_closes_22(ssh_sandbox, contents):
  root, _scripts, ssh_dir = ssh_sandbox
  original = ssh_dir / "sshd_config"
  original.write_text(contents, encoding="utf-8")
  result = _run(ssh_sandbox)
  assert result.returncode == 0, result.stdout + result.stderr
  assert "22 -> 9122" in result.stdout
  assert (root / "listeners").read_text().splitlines() == ["9122"]
  assert "PasswordAuthentication" in original.read_text()
  backup = next((root / "ssh backups").iterdir())
  assert (backup / "0").read_text() == contents
  assert "reload ssh.service" in (root / "systemctl.log").read_text().splitlines()


def test_port_include_and_explicit_bind_addresses_are_updated_together(ssh_sandbox):
  root, _scripts, ssh_dir = ssh_sandbox
  (ssh_dir / "sshd_config").write_text("Include sshd_config.d/*.conf\nListenAddress 0.0.0.0:22\nListenAddress [::]:22\n", encoding="utf-8")
  include = ssh_dir / "sshd_config.d" / "01-port.conf"
  include.write_text("Port 22\nPermitRootLogin yes\n", encoding="utf-8")
  result = _run(ssh_sandbox)
  assert result.returncode == 0, result.stdout + result.stderr
  assert include.read_text() == "Port 9122\nPermitRootLogin yes\n"
  assert "ListenAddress 0.0.0.0:9122" in (ssh_dir / "sshd_config").read_text()
  assert "ListenAddress [::]:9122" in (ssh_dir / "sshd_config").read_text()
  assert (root / "listeners").read_text().splitlines() == ["9122"]


def test_other_ssh_ports_are_preserved_when_22_is_removed(ssh_sandbox):
  root, _scripts, ssh_dir = ssh_sandbox
  (ssh_dir / "sshd_config").write_text("Port 22\nPort 2222\n", encoding="utf-8")
  result = _run(ssh_sandbox)
  assert result.returncode == 0, result.stdout + result.stderr
  assert set((root / "listeners").read_text().splitlines()) == {"9122", "2222"}


@pytest.mark.parametrize("port", [9122, 2222])
def test_existing_custom_port_is_not_changed(ssh_sandbox, port):
  root, _scripts, ssh_dir = ssh_sandbox
  original = ssh_dir / "sshd_config"
  contents = f"Port {port}\n"
  original.write_text(contents, encoding="utf-8")
  result = _run(ssh_sandbox)
  assert result.returncode == 0, result.stdout + result.stderr
  assert original.read_text() == contents
  assert not (root / "ssh backups").exists()
  assert not any(line.startswith(("restart ", "reload ")) for line in (root / "systemctl.log").read_text().splitlines())


@pytest.mark.parametrize("configured", [22, 9122])
@pytest.mark.parametrize("duplicate", [False, True])
def test_socket_activation_moves_the_actual_listener_too(ssh_sandbox, configured, duplicate):
  root, _scripts, ssh_dir = ssh_sandbox
  (ssh_dir / "sshd_config").write_text(f"Port {configured}\n", encoding="utf-8")
  result = _run(ssh_sandbox, {"MOCK_SOCKET_ACTIVE": "1", "MOCK_DUPLICATE_ADDRESS": "1" if duplicate else "0"})
  assert result.returncode == 0, result.stdout + result.stderr
  override = root / "systemd units" / "ssh.socket.d" / "90-cluster-port.conf"
  assert override.read_text().splitlines() == ["[Socket]", "BindIPv6Only=ipv6-only", "ListenStream=",
                                             "ListenStream=0.0.0.0:9122", "ListenStream=[::]:9122"]
  assert (root / "listeners").read_text().splitlines() == ["9122"]
  assert "restart ssh.socket ssh.service" in (root / "systemctl.log").read_text().splitlines()


def test_sshd_service_name_is_supported(ssh_sandbox):
  root, _scripts, _ssh_dir = ssh_sandbox
  result = _run(ssh_sandbox, {"MOCK_SERVICE_NAME": "sshd.service"})
  assert result.returncode == 0, result.stdout + result.stderr
  assert "reload sshd.service" in (root / "systemctl.log").read_text().splitlines()


@pytest.mark.parametrize("failure", ["MOCK_VALIDATE_ERROR", "MOCK_ACTIVATE_ERROR", "MOCK_KEEP_22"])
def test_failed_migration_restores_all_configuration_and_port_22(ssh_sandbox, failure):
  root, _scripts, ssh_dir = ssh_sandbox
  original = ssh_dir / "sshd_config"
  contents = "#Port 22\nPasswordAuthentication yes\n"
  include = ssh_dir / "sshd_config.d" / "01-custom.conf"
  include.write_text("Port 22\nPermitRootLogin yes\n", encoding="utf-8")
  result = _run(ssh_sandbox, {failure: "1"})
  assert result.returncode != 0
  assert "previous SSH configuration restored" in result.stderr
  assert original.read_text() == contents
  assert include.read_text() == "Port 22\nPermitRootLogin yes\n"
  assert set((root / "listeners").read_text().splitlines()) == {"22"}


@pytest.mark.parametrize("existing", [False, True])
def test_socket_failure_restores_or_removes_its_override(ssh_sandbox, existing):
  root, _scripts, _ssh_dir = ssh_sandbox
  override = root / "systemd units" / "ssh.socket.d" / "90-cluster-port.conf"
  previous = "[Socket]\nListenStream=22\n"
  if existing:
    override.parent.mkdir(parents=True)
    override.write_text(previous, encoding="utf-8")
  result = _run(ssh_sandbox, {"MOCK_SOCKET_ACTIVE": "1", "MOCK_ACTIVATE_ERROR": "1"})
  assert result.returncode != 0
  if existing:
    assert override.read_text() == previous
  else:
    assert not override.exists()


def test_unhandled_external_port_include_aborts_and_restores_configuration(ssh_sandbox):
  root, _scripts, ssh_dir = ssh_sandbox
  original = (ssh_dir / "sshd_config").read_text()
  external = root / "external.conf"
  external.write_text("Port 22\n", encoding="utf-8")
  result = _run(ssh_sandbox, {"TEST_EXTERNAL_INCLUDE": "external.conf"})
  assert result.returncode != 0
  assert (ssh_dir / "sshd_config").read_text() == original
  assert external.read_text() == "Port 22\n"
