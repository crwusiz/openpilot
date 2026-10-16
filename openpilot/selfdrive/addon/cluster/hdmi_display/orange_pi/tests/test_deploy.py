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
  for name, content in {"cluster_receiver.py": "# receiver\n", "hdmi_display.py": "# display\n", "frame_stream.py": "# stream\n",
                        "display_controls.py": "# controls\n", "texture_presenter.py": "# presenter\n", "touch_status.py": "# touch status\n",
                        "tcp_diagnostics.py": "# TCP diagnostics\n", "receiver_build.py": "# receiver build\n", "cluster_protocol.py": "# protocol\n",
                        "h264_receiver.py": "# H264 receiver\n", "h264_protocol.py": "# H264 protocol\n", "h264_scene.py": "# H264 scene\n",
                        "cluster_renderer.py": "# shared renderer\n", "cluster_render_support.py": "# shared support\n",
                        "README.md": "연결 대기\r\n수신기\r\n", "requirements.txt": "pygame\n",
                        "cluster-hdmi.service": "[Service]\n"}.items():
    (package / name).write_bytes(content.encode("utf-8"))
  shutil.copyfile(PACKAGE / "receiver_assets.txt", package / "receiver_assets.txt")
  for name in (package / "receiver_assets.txt").read_text().splitlines():
    asset = package / name
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"binary asset\x00\r\n\x89")
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
  *'systemd-run '*)
    [[ ${MOCK_REBOOT_ERROR:-} != 1 ]] || exit 11;;
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


def _deploy(package, *args, extra_env=None, entrypoint=None):
  root = package.parents[1]
  env = dict(os.environ, MOCK_SSH_LOG=(root / "ssh.log").as_posix(),
             MOCK_CAPTURE_PAYLOAD=(root / "captured payload").as_posix(), TMPDIR="temporary files",
             CLUSTER_NETWORK_STATUS_FILE=str(root / "connection.json"), CLUSTER_PYTHON=Path(sys.executable).as_posix())
  env.pop("CLUSTER_PI_PASSWORD", None)
  env.update(extra_env or {})
  entrypoint = entrypoint or package / "scripts" / "deploy.sh"
  return subprocess.run([BASH, "-c", 'export PATH="$PWD/bin:/usr/bin:/bin"; exec bash "$@"', "deploy-test",
                         entrypoint.relative_to(root).as_posix(), *args],
                        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)


def _pi_update(package, *args, extra_env=None):
  repository = package.parents[1] / "C4 checkout with spaces"
  entrypoint = repository / "scripts" / "pi_update.sh"
  entrypoint.parent.mkdir(parents=True)
  shutil.copyfile(PACKAGE.parents[5] / "scripts" / "pi_update.sh", entrypoint)
  receiver = repository / "openpilot" / "selfdrive" / "addon" / "cluster" / "hdmi_display" / "orange_pi"
  shutil.copytree(package, receiver)
  shutil.copyfile(package.parent / "network_status.py", receiver.parent / "network_status.py")
  return _deploy(package, *args, extra_env=extra_env, entrypoint=entrypoint)


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
    if not relative.startswith("assets/"):
      assert b"\r\n" not in data
    assert not data.startswith(b"\xef\xbb\xbf")
  assert files == {"README.md", "requirements.txt", "cluster-hdmi.service", "cluster_receiver.py", "hdmi_display.py", "frame_stream.py",
                   "display_controls.py", "texture_presenter.py", "touch_status.py", "tcp_diagnostics.py", "receiver_build.py", "cluster_protocol.py",
                   "h264_receiver.py", "h264_protocol.py", "h264_scene.py", "cluster_renderer.py", "cluster_render_support.py", "receiver_assets.txt",
                   *(deploy_sandbox / "receiver_assets.txt").read_text().splitlines(),
                   *("scripts/" + path.name for path in (deploy_sandbox / "scripts").glob("*.sh"))}
  assert (captured / "README.md").read_text(encoding="utf-8") == "연결 대기\n수신기\n"
  assert (captured / "assets/icons/steer.png").read_bytes() == b"binary asset\x00\r\n\x89"
  calls = _calls(deploy_sandbox)
  apply = next(args for tool, args in calls if tool == "ssh" and " apply " in args[-1])
  assert "-tt" in apply
  assert "sudo bash '/tmp/cluster-update.Abc12345/payload/scripts/update.sh'" in apply[-1]
  assert any(args[-1] == "rm -rf -- '/tmp/cluster-update.Abc12345'" for tool, args in calls if tool == "ssh")
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []
  control_paths = {arg for _, args in calls for arg in args if arg.startswith("ControlPath=")}
  assert len(control_paths) == 1
  assert not any("systemd-run " in args[-1] for tool, args in calls if tool == "ssh")


def test_standalone_installer_repairs_asset_permissions_in_place(deploy_sandbox):
  package = deploy_sandbox
  root = package.parents[1]
  installer = package / "scripts/install.sh"
  installer.write_text(installer.read_text(encoding="utf-8")
                       .replace('"/opt/cluster-receiver/', '"${PACKAGE_DIR}/')
                       .replace("/opt/cluster-receiver", '"$PACKAGE_DIR"')
                       .replace("/etc/systemd/system/cluster-hdmi.service", "not-installed.service")
                       .replace("/usr/bin/python3", "true"), encoding="utf-8", newline="\n")
  (package / "scripts/prepare_h264.sh").write_text("exit 0\n", encoding="utf-8", newline="\n")
  (package / "scripts/fan_setup.sh").write_text('printf "%s\\n" "$*" > fan-install.log\n', encoding="utf-8", newline="\n")
  for name, body in {
    "id": "printf '0\\n'",
    "apt-get": "exit 0",
    "install": 'printf "%s\\n" "$*" >> "$MOCK_INSTALL_LOG"; exec /usr/bin/install "$@"',
  }.items():
    path = root / "bin" / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
  directories = [package / "assets", package / "assets/fonts", package / "assets/icons"]
  for directory in directories:
    directory.chmod(0o700)
  result = _deploy(package, entrypoint=installer, extra_env={"MOCK_INSTALL_LOG": (root / "install.log").as_posix()})
  assert result.returncode == 0, result.stdout + result.stderr
  assert (root / "fan-install.log").read_text() == "install\n"
  calls = (root / "install.log").read_text().splitlines()
  for relative, directory in zip(("assets", "assets/fonts", "assets/icons"), directories, strict=True):
    assert any(call.startswith("-d -m 755 -- ") and call.endswith("/" + relative) for call in calls)
    if os.name != "nt":
      assert directory.stat().st_mode & 0o777 == 0o755
  assert (package / "assets/icons/steer.png").read_bytes() == b"binary asset\x00\r\n\x89"


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


def test_missing_manifest_font_is_rejected_before_ssh(deploy_sandbox):
  (deploy_sandbox / "assets/fonts/NotoSansKR-Bold.ttf").unlink()
  result = _deploy(deploy_sandbox, "192.168.0.84", "--dry-run")
  assert result.returncode != 0
  assert "Package is missing assets/fonts/NotoSansKR-Bold.ttf" in result.stderr
  assert _calls(deploy_sandbox) == []


def test_shared_renderer_and_assets_are_mapped_from_full_checkout(deploy_sandbox):
  root = deploy_sandbox.parents[1]
  nested = root / "repo/selfdrive/addon/cluster/hdmi_display/orange_pi"
  shutil.copytree(deploy_sandbox, nested)
  cluster_dir, selfdrive_dir = nested.parents[1], nested.parents[3]
  for name in ("cluster_renderer.py", "cluster_render_support.py"):
    (nested / name).rename(cluster_dir / name)
  (nested / "assets").rename(selfdrive_dir / "assets")
  script = ('set -euo pipefail; PACKAGE_DIR=$(cd -- "$1" && pwd -P); '
            + 'fail() { printf "%s\\n" "$*" >&2; exit 1; }; '
            + 'source "$PACKAGE_DIR/scripts/package_sources.sh"; package_collect_files; printf "%s\\n" "${file_sources[@]}"')
  result = subprocess.run([BASH, "-c", script, "source-map-test", nested.relative_to(root).as_posix()],
                          cwd=root, capture_output=True, text=True, timeout=10, check=False)
  assert result.returncode == 0, result.stdout + result.stderr
  assert "/repo/selfdrive/addon/cluster/cluster_renderer.py" in result.stdout
  assert "/repo/selfdrive/assets/fonts/NotoSansKR-Bold.ttf" in result.stdout
  assert _calls(deploy_sandbox) == []


@pytest.mark.parametrize("helper", ["ensure_wifi.sh", "wifi_boot_setup.sh", "wifi_power_save.sh", "fan_setup.sh"])
def test_incomplete_boot_setup_payload_is_rejected_before_ssh(deploy_sandbox, helper):
  (deploy_sandbox / "scripts" / helper).unlink()
  result = _deploy(deploy_sandbox, "192.168.0.84")
  assert result.returncode != 0
  assert "Package is missing scripts/" + helper in result.stderr
  assert _calls(deploy_sandbox) == []


@pytest.mark.parametrize("helper", [
  "cluster_protocol.py", "frame_stream.py", "display_controls.py", "texture_presenter.py", "tcp_diagnostics.py", "receiver_build.py",
])
def test_incomplete_runtime_payload_is_rejected_before_ssh(deploy_sandbox, helper):
  (deploy_sandbox / helper).unlink()
  result = _deploy(deploy_sandbox, "192.168.0.84")
  assert result.returncode != 0
  assert "Package is missing " + helper in result.stderr
  assert _calls(deploy_sandbox) == []


@pytest.mark.parametrize("port_mode", ["9122", "22", "fallback"])
def test_pi_update_schedules_reboot_after_apply_cleanup_and_port_migration(deploy_sandbox, port_mode):
  arguments = ("--port", "22") if port_mode == "22" else ()
  environment = {"MOCK_PORT_9122_ERROR": "ssh: connect to host 192.168.0.84 port 9122: Connection refused"} if port_mode == "fallback" else {}
  result = _pi_update(deploy_sandbox, "192.168.0.84", "--user", "orangepi", *arguments, extra_env=environment)
  assert result.returncode == 0, result.stdout + result.stderr
  assert "Orange Pi reboot scheduled in about 5 seconds" in result.stdout
  calls = _calls(deploy_sandbox)
  reboot_calls = [(i, args) for i, (tool, args) in enumerate(calls) if tool == "ssh" and "systemd-run " in args[-1]]
  assert len(reboot_calls) == 1
  reboot_index, reboot = reboot_calls[0]
  apply_index = next(i for i, (tool, args) in enumerate(calls) if tool == "ssh" and " apply " in args[-1])
  cleanup_index = next(i for i, (tool, args) in enumerate(calls) if tool == "ssh" and args[-1].startswith("rm -rf"))
  assert apply_index < cleanup_index < reboot_index
  assert "-tt" in reboot and reboot[reboot.index("-l") + 1] == "orangepi"
  assert "sudo systemd-run" in reboot[-1]
  assert "--collect --on-active=5s --timer-property=AccuracySec=1s /usr/bin/systemctl reboot" in reboot[-1]
  assert reboot[reboot.index("-p") + 1] == ("9122" if port_mode == "9122" else "22")
  if port_mode != "9122":
    migration_index = next(i for i, (tool, args) in enumerate(calls) if tool == "ssh" and "/scripts/ssh_port.sh;" in args[-1])
    assert cleanup_index < migration_index < reboot_index
  assert not any(args[-1].startswith("rm -rf") for tool, args in calls[reboot_index + 1:] if tool == "ssh")
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []


@pytest.mark.parametrize("error, expected_exit", [
  ("MOCK_AUTH_ERROR", 255), ("MOCK_TRANSFER_ERROR", 8), ("MOCK_APPLY_ERROR", 7),
  ("MOCK_SSH_PORT_ERROR", 6), ("MOCK_CLEANUP_ERROR", 1), ("MOCK_REBOOT_ERROR", 11),
])
def test_pi_update_failure_does_not_report_a_scheduled_reboot(deploy_sandbox, error, expected_exit):
  result = _pi_update(deploy_sandbox, "192.168.0.84", "--port", "22", extra_env={error: "1"})
  assert result.returncode == expected_exit, result.stdout + result.stderr
  assert "Orange Pi reboot scheduled" not in result.stdout
  calls = _calls(deploy_sandbox)
  requested = any("systemd-run " in args[-1] for tool, args in calls if tool == "ssh")
  assert requested == (error == "MOCK_REBOOT_ERROR")
  if error == "MOCK_REBOOT_ERROR":
    assert "update succeeded, but Orange Pi reboot scheduling failed" in result.stderr
  assert list((deploy_sandbox.parents[1] / "temporary files").iterdir()) == []


@pytest.mark.parametrize("option", ["--dry-run", "--help", "--rollback"])
def test_pi_update_preview_help_and_rollback_do_not_reboot(deploy_sandbox, option):
  result = _pi_update(deploy_sandbox, "192.168.0.84", option)
  assert result.returncode == 0, result.stdout + result.stderr
  calls = _calls(deploy_sandbox)
  assert not any("systemd-run " in args[-1] for tool, args in calls if tool == "ssh")
  assert "Orange Pi reboot scheduled" not in result.stdout
  if option != "--rollback":
    assert calls == []
