import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display import network_status


PACKAGE = Path(__file__).resolve().parents[1]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is needed to check the C4 updater")


@pytest.fixture
def deploy_sandbox(tmp_path):
  package = tmp_path / "checkout with spaces" / "orange_pi"
  scripts = package / "scripts"
  shutil.copytree(PACKAGE / "scripts", scripts)
  for name, content in {"cluster_receiver.py": "# receiver\n", "hdmi_display.py": "# display\n",
                        "README.md": "연결 대기\r\n수신기\r\n", "requirements.txt": "pygame\n",
                        "cluster-hdmi.service": "[Service]\n"}.items():
    (package / name).write_bytes(content.encode("utf-8"))
  (package / "tests").mkdir()
  (package / "tests" / "test_excluded.py").write_text("# test\n")
  (package / ".venv").mkdir()
  (package / ".venv" / "excluded.py").write_text("# environment\n")
  shutil.copy2(PACKAGE.parent / "network_status.py", package.parent / "network_status.py")
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  (tmp_path / "temporary files").mkdir()
  mocks = {
    "ssh": '''printf 'tool=ssh\n' >> "$MOCK_SSH_LOG"
printf 'arg=%s\n' "$@" >> "$MOCK_SSH_LOG"
command=${!#}
port=0
arguments=("$@")
for (( i=0; i < $# - 1; i++ )); do
  [[ ${arguments[i]} != -p ]] || port=${arguments[i+1]}
done
case "$command" in
  true)
    if [[ $port == 9122 && -n ${MOCK_PORT_9122_ERROR:-} ]]; then
      printf '%s\n' "$MOCK_PORT_9122_ERROR" >&2
      exit 255
    fi
    [[ $port != 22 || ${MOCK_PORT_22_ERROR:-} != 1 ]] || exit 255
    [[ ${MOCK_AUTH_ERROR:-} != 1 ]] || exit 255
    # Interactive mode authenticates once too, without the automatic helper.
    [[ ${SSH_ASKPASS_REQUIRE:-} == force ]] || exit 0
    [[ ${SSH_ASKPASS_REQUIRE:-} == force && -n ${DISPLAY:-} && -x ${SSH_ASKPASS:-} ]] || exit 254
    password=$("$SSH_ASKPASS" "root@pi's password:") || exit 253
    [[ "$password" == "${MOCK_EXPECTED_PASSWORD-orangepi}" ]] || exit 252
    # A password helper must not answer a private-key passphrase or host-key question.
    if "$SSH_ASKPASS" "Enter passphrase for key 'private key':"; then exit 251; fi
    if "$SSH_ASKPASS" 'Are you sure you want to continue connecting (yes/no)?'; then exit 251; fi;;
  'mktemp '*)
    [[ ${MOCK_CONNECT_ERROR:-} != 1 ]] || exit 255
    if [[ ${MOCK_BAD_DIRECTORY:-} == 1 ]]; then printf '/tmp/../opt\n'
    elif [[ ${MOCK_NOISY_DIRECTORY:-} == 1 ]]; then printf 'login noise\n/tmp/cluster-update.Abc12345\n'
    else printf '/tmp/cluster-update.Abc12345\n'; fi
    [[ ${MOCK_STDERR:-} != 1 ]] || printf 'SSH diagnostic\n' >&2;;
  *' apply '*|*' rollback;'*)
    [[ ${MOCK_APPLY_ERROR:-} != 1 ]] || exit 7
    printf 'Receiver apply complete\n';;
  *'/scripts/ssh_port.sh;'*)
    [[ ${MOCK_SSH_PORT_ERROR:-} != 1 ]] || exit 6
    printf 'Orange Pi SSH port changed: 22 -> 9122.\n';;
  'rm -rf '*) [[ ${MOCK_CLEANUP_ERROR:-} != 1 ]] || exit 9;;
esac
''',
    "scp": '''printf 'tool=scp\n' >> "$MOCK_SSH_LOG"
printf 'arg=%s\n' "$@" >> "$MOCK_SSH_LOG"
[[ ${MOCK_TRANSFER_ERROR:-} != 1 ]] || exit 8
mkdir -p "$MOCK_CAPTURE_PAYLOAD"
cp -R payload/. "$MOCK_CAPTURE_PAYLOAD/"
''',
    # Git Bash lacks setsid; keep the actual SSH/askpass boundary under test.
    "setsid": '''[[ ${1:-} == --wait ]] || exit 1
shift
exec "$@"
''',
  }
  for name, content in mocks.items():
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + content, encoding="utf-8", newline="\n")
    path.chmod(0o755)
  return package


def _deploy(package, *args, extra_env=None):
  root = package.parents[1]
  env = dict(os.environ, MOCK_SSH_LOG=(root / "ssh.log").as_posix(),
             MOCK_CAPTURE_PAYLOAD=(root / "captured payload").as_posix(), TMPDIR="temporary files",
             CLUSTER_NETWORK_STATUS_FILE=str(root / "connection.json"), CLUSTER_PYTHON=Path(sys.executable).as_posix())
  env.pop("CLUSTER_PI_PASSWORD", None)
  env.update(extra_env or {})
  return subprocess.run([BASH, "-c", 'export PATH="$PWD/bin:/usr/bin:/bin"; exec bash "$@"', "deploy-test",
                         (package.relative_to(root) / "scripts" / "deploy.sh").as_posix(), *args],
                        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)


def _calls(package):
  log = package.parents[1] / "ssh.log"
  if not log.exists():
    return []
  calls = []
  for line in log.read_text(encoding="utf-8").splitlines():
    if line.startswith("tool="):
      calls.append((line[5:], []))
    else:
      calls[-1][1].append(line[4:])
  return calls


def test_c4_update_packages_receiver_only_with_checksums_and_cleans_staging(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env={"MOCK_STDERR": "1"})
  assert result.returncode == 0, result.stdout + result.stderr
  captured = deploy_sandbox.parents[1] / "captured payload"
  manifest = (captured / "SHA256SUMS").read_text().splitlines()
  files = set()
  for entry in manifest:
    digest, relative = entry[:64], entry[66:]
    assert entry[64:66] in ("  ", " *")
    files.add(relative)
    data = (captured / relative).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest
    assert b"\r\n" not in data
    assert not data.startswith(b"\xef\xbb\xbf")
  assert files == {"README.md", "requirements.txt", "cluster-hdmi.service", "cluster_receiver.py", "hdmi_display.py",
                   *("scripts/" + path.name for path in (deploy_sandbox / "scripts").glob("*.sh"))}
  assert (captured / "README.md").read_text(encoding="utf-8") == "연결 대기\n수신기\n"
  calls = _calls(deploy_sandbox)
  apply = next(args for tool, args in calls if tool == "ssh" and " apply " in args[-1])
  assert "-tt" in apply
  assert "sudo bash '/tmp/cluster-update.Abc12345/payload/scripts/update.sh'" in apply[-1]
  assert any(args[-1] == "rm -rf -- '/tmp/cluster-update.Abc12345'" for tool, args in calls if tool == "ssh")
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []
  control_paths = {arg for _, args in calls for arg in args if arg.startswith("ControlPath=")}
  assert len(control_paths) == 1


def test_c4_uses_current_peer_ip_without_a_manual_address(deploy_sandbox):
  network_status.write_network_status("192.168.0.90", deploy_sandbox.parents[1] / "connection.json")
  result = _deploy(deploy_sandbox)
  assert result.returncode == 0, result.stderr
  assert "root@192.168.0.90:9122" in result.stdout
  assert all("192.168.0.90" in args for tool, args in _calls(deploy_sandbox) if tool == "ssh")
  assert all(args[args.index("-p") + 1] == "9122" for tool, args in _calls(deploy_sandbox) if tool == "ssh")


def test_real_openssh_accepts_control_path_with_spaces(deploy_sandbox):
  git_ssh = GIT_BASH.parent.parent / "usr" / "bin" / "ssh.exe"
  ssh = str(git_ssh) if os.name == "nt" and git_ssh.exists() else shutil.which("ssh")
  if not ssh:
    pytest.skip("OpenSSH is needed to check connection sharing options")
  result = _deploy(deploy_sandbox, "192.168.0.84")
  assert result.returncode == 0, result.stderr
  args = next(args for tool, args in _calls(deploy_sandbox) if tool == "ssh" and args[-1].startswith("mktemp"))
  # -G parses configuration without connecting to any host.
  parsed = subprocess.run([ssh, "-G", "-F", "/dev/null", *args[:-1]], capture_output=True, text=True, timeout=10, check=False)
  assert parsed.returncode == 0, parsed.stderr
  assert any(line.startswith("controlpath ") and "temporary files/cluster-deploy." in line for line in parsed.stdout.splitlines())
  assert "stricthostkeychecking accept-new" in parsed.stdout.splitlines()
  assert "batchmode yes" in parsed.stdout.splitlines()


@pytest.mark.parametrize("password", [None, "different secret", "literal '$value'; $(touch unexpected-file)"])
def test_automatic_password_authenticates_before_upload_without_exposing_the_password(deploy_sandbox, password):
  environment = {} if password is None else {"CLUSTER_PI_PASSWORD": password, "MOCK_EXPECTED_PASSWORD": password}
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env=environment)
  assert result.returncode == 0, result.stderr
  calls = _calls(deploy_sandbox)
  first_tool, authentication = calls[0]
  assert first_tool == "ssh" and authentication[-1] == "true"
  assert "-n" in authentication and "NumberOfPasswordPrompts=1" in authentication
  assert "StrictHostKeyChecking=accept-new" in authentication
  assert all("BatchMode=yes" in args for _, args in calls[1:])
  expected_password = "orangepi" if password is None else password
  assert expected_password not in result.stdout + result.stderr
  assert expected_password not in (deploy_sandbox.parents[1] / "ssh.log").read_text(encoding="utf-8")
  assert not (deploy_sandbox.parents[1] / "unexpected-file").exists()
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []


def test_authentication_failure_stops_before_upload_and_removes_the_helper(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env={"MOCK_AUTH_ERROR": "1"})
  assert result.returncode == 255
  calls = _calls(deploy_sandbox)
  assert not any(tool == "scp" for tool, _ in calls)
  assert not any(" apply " in args[-1] or args[-1].startswith("mktemp") for _, args in calls)
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []


def test_manual_password_mode_uses_the_original_interactive_connection(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", "--ask-password")
  assert result.returncode == 0, result.stderr
  calls = _calls(deploy_sandbox)
  assert calls[0][1][-1] == "true"
  assert sum(args[-1] == "true" for _, args in calls) == 1
  assert "StrictHostKeyChecking=accept-new" in calls[0][1]
  assert all("BatchMode=yes" in args for _, args in calls[1:])
  assert any(tool == "scp" for tool, _ in calls)


@pytest.mark.parametrize("error", [
  "ssh: connect to host 192.168.0.84 port 9122: Connection refused",
  "ssh: connect to host 192.168.0.84 port 9122: Connection timed out",
  "ssh: connect to host 192.168.0.84 port 9122: No route to host",
  "Connection to 192.168.0.84 port 9122 timed out",
  "Connection reset by 192.168.0.84 port 9122",
])
@pytest.mark.parametrize("manual", [False, True])
def test_unavailable_9122_falls_back_to_22_and_migrates_after_apply(deploy_sandbox, error, manual):
  args = ("--ask-password",) if manual else ()
  result = _deploy(deploy_sandbox, "192.168.0.84", *args, extra_env={"MOCK_PORT_9122_ERROR": error})
  assert result.returncode == 0, result.stdout + result.stderr
  assert "SSH port 9122 is unavailable" in result.stdout
  calls = _calls(deploy_sandbox)
  authentication = [arguments for tool, arguments in calls if tool == "ssh" and arguments[-1] == "true"]
  assert [arguments[arguments.index("-p") + 1] for arguments in authentication] == ["9122", "22"]
  scp = next(arguments for tool, arguments in calls if tool == "scp")
  assert scp[scp.index("-P") + 1] == "22"
  apply_index = next(i for i, (tool, arguments) in enumerate(calls) if tool == "ssh" and " apply " in arguments[-1])
  cleanup_index = next(i for i, (tool, arguments) in enumerate(calls) if tool == "ssh" and arguments[-1].startswith("rm -rf"))
  migration_index = next(i for i, (tool, arguments) in enumerate(calls) if tool == "ssh" and "/scripts/ssh_port.sh;" in arguments[-1])
  assert apply_index < cleanup_index < migration_index
  assert not any(arguments[-1].startswith("rm -rf") for tool, arguments in calls[migration_index + 1:])
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []


@pytest.mark.parametrize("error", [
  "root@192.168.0.84: Permission denied (publickey,password).",
  "Host key verification failed.",
  "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!",
  "Unable to negotiate with 192.168.0.84 port 9122: no matching host key type found.",
  "ssh: connect to host jump-host port 22: Connection refused",
])
def test_reachable_9122_or_proxy_errors_do_not_try_port_22(deploy_sandbox, error):
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env={"MOCK_PORT_9122_ERROR": error})
  assert result.returncode == 255
  calls = _calls(deploy_sandbox)
  assert not any(tool == "scp" for tool, _arguments in calls)
  assert all(arguments[arguments.index("-p") + 1] == "9122" for tool, arguments in calls if tool == "ssh")


def test_explicit_port_disables_fallback(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", "--port", "9122", extra_env={
    "MOCK_PORT_9122_ERROR": "ssh: connect to host 192.168.0.84 port 9122: Connection refused",
  })
  assert result.returncode == 255
  assert all(arguments[arguments.index("-p") + 1] == "9122" for tool, arguments in _calls(deploy_sandbox) if tool == "ssh")


def test_failed_22_authentication_stops_before_upload(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env={
    "MOCK_PORT_9122_ERROR": "ssh: connect to host 192.168.0.84 port 9122: Connection refused", "MOCK_PORT_22_ERROR": "1",
  })
  assert result.returncode == 255
  assert not any(tool == "scp" for tool, _arguments in _calls(deploy_sandbox))


def test_explicit_22_update_also_migrates_ssh(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", "--port", "22")
  assert result.returncode == 0, result.stdout + result.stderr
  assert any("/scripts/ssh_port.sh;" in arguments[-1] for tool, arguments in _calls(deploy_sandbox) if tool == "ssh")


def test_ssh_port_migration_failure_is_reported_after_successful_update(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", "--port", "22", extra_env={"MOCK_SSH_PORT_ERROR": "1"})
  assert result.returncode == 6
  assert "Receiver apply complete" in result.stdout
  assert any("/scripts/ssh_port.sh;" in arguments[-1] for tool, arguments in _calls(deploy_sandbox) if tool == "ssh")


def test_9122_update_does_not_change_ssh_configuration(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84")
  assert result.returncode == 0, result.stderr
  assert not any("/scripts/ssh_port.sh;" in arguments[-1] for tool, arguments in _calls(deploy_sandbox) if tool == "ssh")


def test_failed_update_on_22_does_not_change_ssh_configuration(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", "--port", "22", extra_env={"MOCK_APPLY_ERROR": "1"})
  assert result.returncode == 7
  assert not any("/scripts/ssh_port.sh;" in arguments[-1] for tool, arguments in _calls(deploy_sandbox) if tool == "ssh")


def test_no_connection_requires_an_explicit_pi_address(deploy_sandbox):
  result = _deploy(deploy_sandbox)
  assert result.returncode != 0
  assert "Specify the Pi IP explicitly" in result.stderr
  assert _calls(deploy_sandbox) == []


def test_explicit_address_still_works_without_c4_status_helper(deploy_sandbox):
  (deploy_sandbox.parent / "network_status.py").unlink()
  result = _deploy(deploy_sandbox, "orangepizero3w")
  assert result.returncode == 0, result.stderr


def test_ssh_user_port_key_and_ipv6_arguments_are_preserved(deploy_sandbox):
  key = deploy_sandbox.parents[1] / "private key"
  key.write_text("key")
  result = _deploy(deploy_sandbox, "2001:db8::2", "--user", "orangepi", "--port", "2222", "--identity", "private key")
  assert result.returncode == 0, result.stderr
  calls = _calls(deploy_sandbox)
  scp = next(args for tool, args in calls if tool == "scp")
  ssh = next(args for tool, args in calls if tool == "ssh" and args[-1].startswith("mktemp"))
  assert scp[scp.index("-P") + 1] == ssh[ssh.index("-p") + 1] == "2222"
  assert ssh[ssh.index("-l") + 1] == "orangepi"
  assert scp[-1] == "orangepi@[2001:db8::2]:/tmp/cluster-update.Abc12345/"
  assert scp[scp.index("-i") + 1] == ssh[ssh.index("-i") + 1]
  assert ssh[ssh.index("-i") + 1].endswith("/private key")
  assert not any("StrictHostKeyChecking=no" in arg for _, args in calls for arg in args)


@pytest.mark.parametrize("error", ["MOCK_CONNECT_ERROR", "MOCK_BAD_DIRECTORY", "MOCK_NOISY_DIRECTORY", "MOCK_TRANSFER_ERROR"])
def test_transfer_errors_never_apply_files(deploy_sandbox, error):
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env={error: "1"})
  assert result.returncode != 0
  calls = _calls(deploy_sandbox)
  assert not any(" apply " in args[-1] for tool, args in calls if tool == "ssh")
  if error in ("MOCK_BAD_DIRECTORY", "MOCK_NOISY_DIRECTORY"):
    assert not any(args[-1].startswith("rm -rf") for tool, args in calls if tool == "ssh")
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []


def test_remote_apply_failure_is_returned_and_staging_is_cleaned(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env={"MOCK_APPLY_ERROR": "1"})
  assert result.returncode == 7
  assert any(args[-1].startswith("rm -rf") for tool, args in _calls(deploy_sandbox) if tool == "ssh")


def test_cleanup_failure_does_not_change_successful_apply_result(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", extra_env={"MOCK_CLEANUP_ERROR": "1"})
  assert result.returncode == 0
  assert "Remote staging cleanup failed" in result.stderr


def test_rollback_uses_retained_pi_runner_without_upload(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", "--rollback")
  assert result.returncode == 0, result.stderr
  calls = _calls(deploy_sandbox)
  assert not any(tool == "scp" for tool, _ in calls)
  assert any("sudo bash /var/lib/cluster-receiver/updates/update.sh rollback" in args[-1] for tool, args in calls if tool == "ssh")
  assert not any(args[-1].startswith("mktemp") for tool, args in calls if tool == "ssh")


def test_dry_run_does_not_use_ssh_or_create_staging(deploy_sandbox):
  result = _deploy(deploy_sandbox, "192.168.0.84", "--dry-run")
  assert result.returncode == 0, result.stderr
  assert "scripts/update.sh" in result.stdout
  assert _calls(deploy_sandbox) == []
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []


@pytest.mark.parametrize("arguments", [
  ("pi;touch injected",), ("$(touch injected)",), ("--host",), ("pi", "--port", "22;exit"),
  ("pi", "--port", "0"), ("pi", "--port", "65536"), ("pi", "--user", "root;exit"), ("pi", "other-pi"),
])
def test_invalid_arguments_stop_before_ssh(deploy_sandbox, arguments):
  result = _deploy(deploy_sandbox, *arguments)
  assert result.returncode != 0
  assert _calls(deploy_sandbox) == []
  assert not (deploy_sandbox.parents[1] / "injected").exists()


def test_unsafe_package_filename_is_rejected_before_ssh(deploy_sandbox):
  (deploy_sandbox / "bad name.py").write_text("# invalid\n")
  result = _deploy(deploy_sandbox, "192.168.0.84")
  assert result.returncode != 0
  assert "Unexpected package filename" in result.stderr
  assert _calls(deploy_sandbox) == []
