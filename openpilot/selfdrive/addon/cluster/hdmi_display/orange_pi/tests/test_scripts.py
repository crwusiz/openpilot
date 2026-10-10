import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is needed to check Orange Pi scripts")


@pytest.fixture
def script_sandbox(tmp_path):
  scripts = tmp_path / "package with spaces" / "scripts"
  shutil.copytree(SCRIPTS, scripts)
  common = scripts / "common.sh"
  # Replace only the receiver boundary: run the actual wrappers without Python,
  # a DRM device, or any process that would draw on a developer's screen.
  with common.open("a", encoding="utf-8", newline="\n") as file:
    file.write('''
run_receiver() {
  printf 'driver=%s renderer=%s acceleration=%s\n' "${SDL_VIDEODRIVER:-}" "${SDL_RENDER_DRIVER:-}" "${SDL_FRAMEBUFFER_ACCELERATION:-}"
  printf 'argument=%s\n' "$@"
}
''')
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  for command, contents in {
    "id": "case ${1:-} in -u) printf '0\\n';; -gn) printf 'orangepi\\n';; *) exit 0;; esac",
    "nmcli": '''case "$*" in
  *GENERAL.TYPE*) printf 'wifi\\n';;
  *GENERAL.CON-UUID*) printf '%s\\n' "${MOCK_ACTIVE_UUID:---}";;
  *DEVICE,TYPE*) printf 'wlan0:wifi\\n';;
  *) printf 'nmcli argument=%s\\n' "$@";;
esac''',
    "ip": "printf 'ip argument=%s\\n' \"$@\"",
    "iw": '''printf '%s\\n' "$*" >> "${IW_CALL_LOG:-iw.log}"
[[ ${MOCK_IW_ERROR:-} != 1 ]] || exit 1
[[ "$*" != *'get power_save' ]] || printf 'Power save: %s\\n' "${MOCK_IW_POWER:-off}"''',
    "systemctl": '''printf '%s\\n' "$*" >> "$SYSTEMCTL_CALL_LOG"
case "$1" in
  show) printf 'loaded\\n';;
  get-default) printf 'graphical.target\\n';;
  reset-failed)
    case ${MOCK_RESET_ERROR:-} in
      not-loaded)
        printf 'Failed to reset failed state of unit cluster-hdmi.service: Unit cluster-hdmi.service not loaded.\\n' >&2
        exit 1;;
      access-denied) printf 'Failed to reset failed state: Access denied\\n' >&2; exit 1;;
    esac;;
  enable|restart)
    if [[ ${MOCK_START_ERROR:-} == 1 ]]; then
      printf 'Service start failed\\n' >&2
      exit 1
    fi;;
esac''',
  }.items():
    path = bin_dir / command
    path.write_text("#!/usr/bin/env bash\n" + contents + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
  return scripts, bin_dir


def _run_script(script_sandbox, name, *args, display=None, extra_env=None, timeout=10):
  scripts, bin_dir = script_sandbox
  # Relative paths also work under Git Bash on Windows without drive conversion.
  env = dict(os.environ, PATH="bin:/usr/bin:/bin")
  env.pop("DISPLAY", None)
  if display is not None:
    env["DISPLAY"] = display
  env.update(extra_env or {})
  # Git for Windows prepends its own tools to PATH on startup. Set the mock
  # command path inside Bash before executing the wrapper under test.
  return subprocess.run([BASH, "-c", 'export PATH="bin:/usr/bin:/bin"; exec bash "$@"', "script-test",
                         (scripts.relative_to(bin_dir.parent) / name).as_posix(), *args],
                        cwd=bin_dir.parent, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout, check=False)


def test_desktop_requires_the_logged_in_session(script_sandbox):
  result = _run_script(script_sandbox, "run_desktop.sh")
  assert result.returncode == 1
  assert "terminal in the Orange Pi desktop" in result.stderr
  assert "driver=" not in result.stdout


def test_desktop_uses_software_and_preserves_cli_arguments(script_sandbox):
  result = _run_script(script_sandbox, "run_desktop.sh", "--width", "1920", "--rotation", "0", "--log-touch", display=":3")
  assert result.returncode == 0, result.stderr
  assert "driver=x11 renderer=software acceleration=0" in result.stdout
  assert result.stdout.splitlines()[1:] == ["argument=--width", "argument=1920", "argument=--rotation", "argument=0", "argument=--log-touch"]


def test_wifi_prompts_for_password_and_treats_ssid_as_one_argument(script_sandbox):
  ssid = "Android hotspot; $(touch unexpected-file)"
  result = _run_script(script_sandbox, "connect_wifi.sh", ssid, "wlan1")
  assert result.returncode == 0, result.stderr
  assert result.stdout.splitlines() == [
    "nmcli argument=--ask", "nmcli argument=device", "nmcli argument=wifi", "nmcli argument=connect",
    f"nmcli argument={ssid}", "nmcli argument=ifname", "nmcli argument=wlan1",
    "ip argument=-4", "ip argument=addr", "ip argument=show", "ip argument=dev", "ip argument=wlan1",
  ]
  assert not (script_sandbox[1].parent / "unexpected-file").exists()


@pytest.mark.parametrize("status, expected", [("connected", "2\n"), ("disconnected", "")])
def test_card_selection_skips_disconnected_hdmi_and_gpu_only_cards(tmp_path, status, expected):
  sysfs = tmp_path / "drm"
  for name, state in (("card0-HDMI-A-1", "disconnected"), ("card2-HDMI-A-1", status)):
    connector = sysfs / name
    connector.mkdir(parents=True)
    (connector / "status").write_text(state + "\n", encoding="utf-8")
  (sysfs / "card1").mkdir()
  result = subprocess.run([BASH, "-c", 'source "$1"; hdmi_card_index "$2"', "test", (SCRIPTS / "common.sh").as_posix(), "drm"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False)
  assert result.returncode == (0 if expected else 1), result.stderr
  assert result.stdout == expected


def _service_environment(script_sandbox):
  scripts, bin_dir = script_sandbox
  # Redirect only filesystem boundaries to the sandbox. systemctl is a recorder,
  # so this verifies service actions without changing the host's boot settings.
  service = scripts / "service.sh"
  service.write_text(service.read_text(encoding="utf-8")
                     .replace("/opt/cluster-receiver", '"$PACKAGE_DIR"')
                     .replace("/var/lib/cluster-receiver", '"$TEST_STATE_DIR"')
                     .replace("/etc/systemd/system", '"$TEST_UNIT_DIR"'), encoding="utf-8", newline="\n")
  helper = scripts / "wifi_boot_setup.sh"
  helper.write_text(helper.read_text(encoding="utf-8")
                    .replace("TARGET=/opt/cluster-receiver", 'TARGET="$PACKAGE_DIR"')
                    .replace("WIFI_CONF=/etc/systemd/system/cluster-hdmi.service.d/wifi.conf",
                             'WIFI_CONF="$TEST_UNIT_DIR/cluster-hdmi.service.d/wifi.conf"')
                    .replace("WIFI_DISPATCHER=/etc/NetworkManager/dispatcher.d/90-cluster-wifi-power",
                             'WIFI_DISPATCHER="$TEST_DISPATCHER_DIR/90-cluster-wifi-power"'), encoding="utf-8", newline="\n")
  (scripts.parent / "cluster_receiver.py").touch()
  shutil.copyfile(SCRIPTS.parent / "cluster-hdmi.service", scripts.parent / "cluster-hdmi.service")
  return {"TEST_STATE_DIR": "state with spaces", "TEST_UNIT_DIR": "units with spaces",
          "TEST_DISPATCHER_DIR": "NM dispatcher", "SYSTEMCTL_CALL_LOG": "systemctl.log"}


def test_service_enable_preserves_target_and_desktop_restores_it(script_sandbox):
  environment = _service_environment(script_sandbox)
  scripts, bin_dir = script_sandbox
  state = bin_dir.parent / environment["TEST_STATE_DIR"]
  units = bin_dir.parent / environment["TEST_UNIT_DIR"]
  log = bin_dir.parent / environment["SYSTEMCTL_CALL_LOG"]

  for _ in range(2):
    result = _run_script(script_sandbox, "service.sh", "enable", "receiver", extra_env=environment)
    assert result.returncode == 0, result.stderr
    assert (state / "previous-target").read_text() == "graphical.target\n"
  assert (units / "cluster-hdmi.service.d" / "account.conf").read_text() == "[Service]\nUser=receiver\nGroup=orangepi\n"
  assert "ExecStartPre=-+" in (units / "cluster-hdmi.service.d" / "wifi.conf").read_text()
  actions = log.read_text().splitlines()
  assert actions.count("get-default") == 1
  assert actions.index("stop display-manager.service") < actions.index("enable --now cluster-hdmi.service")
  assert "set-default multi-user.target" in actions

  result = _run_script(script_sandbox, "service.sh", "desktop", extra_env=environment)
  assert result.returncode == 0, result.stderr
  actions = log.read_text().splitlines()
  assert actions[-3:] == ["disable cluster-hdmi.service", "set-default graphical.target", "start display-manager.service"]


@pytest.mark.parametrize("action", ["enable", "restart"])
def test_service_starts_when_reset_reports_unit_not_loaded(script_sandbox, action):
  environment = _service_environment(script_sandbox)
  environment["MOCK_RESET_ERROR"] = "not-loaded"
  result = _run_script(script_sandbox, "service.sh", action, extra_env=environment)
  assert result.returncode == 0, result.stderr
  assert "No loaded receiver state to reset" in result.stdout
  assert not result.stderr
  actions = (script_sandbox[1].parent / "systemctl.log").read_text().splitlines()
  start = "enable --now cluster-hdmi.service" if action == "enable" else "restart cluster-hdmi.service"
  assert start in actions
  assert actions[-1] == "status cluster-hdmi.service --no-pager"
  if action == "enable":
    assert actions.index(start) < actions.index("set-default multi-user.target")


@pytest.mark.parametrize("action", ["enable", "restart"])
def test_service_preserves_unexpected_reset_error(script_sandbox, action):
  environment = _service_environment(script_sandbox)
  environment["MOCK_RESET_ERROR"] = "access-denied"
  result = _run_script(script_sandbox, "service.sh", action, extra_env=environment)
  assert result.returncode != 0
  assert "Access denied" in result.stderr
  actions = (script_sandbox[1].parent / "systemctl.log").read_text().splitlines()
  assert not any(line.startswith(("enable ", "restart ", "set-default ")) for line in actions)


@pytest.mark.parametrize("action", ["enable", "restart"])
def test_service_start_errors_are_not_ignored_after_reset_is_skipped(script_sandbox, action):
  environment = _service_environment(script_sandbox)
  environment.update(MOCK_RESET_ERROR="not-loaded", MOCK_START_ERROR="1")
  result = _run_script(script_sandbox, "service.sh", action, extra_env=environment)
  assert result.returncode != 0
  assert "Service start failed" in result.stderr
  actions = (script_sandbox[1].parent / "systemctl.log").read_text().splitlines()
  assert not any(line.startswith("set-default ") for line in actions)
  assert actions[-1] != "status cluster-hdmi.service --no-pager"


def test_service_status_only_queries_enabled_and_running_state(script_sandbox):
  scripts, bin_dir = script_sandbox
  result = _run_script(script_sandbox, "service.sh", "status", extra_env={"SYSTEMCTL_CALL_LOG": "systemctl.log"})
  assert result.returncode == 0, result.stderr
  assert (bin_dir.parent / "systemctl.log").read_text().splitlines() == [
    "is-enabled cluster-hdmi.service", "status cluster-hdmi.service --no-pager",
  ]


@pytest.mark.parametrize("name", ["run_console.sh", "run_desktop.sh", "service.sh", "diagnose.sh", "ensure_wifi.sh", "wifi_power_save.sh"])
def test_help_needs_no_desktop_or_service_changes(script_sandbox, name):
  result = _run_script(script_sandbox, name, "--help")
  assert result.returncode == 0, result.stderr
  assert "Usage:" in result.stdout


def test_script_line_endings_and_bash_syntax():
  for script in sorted(SCRIPTS.glob("*.sh")):
    assert b"\r" not in script.read_bytes(), f"{script.name} must use LF line endings on Linux"
    result = subprocess.run([BASH, "-n", script.as_posix()], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, f"{script.name}: {result.stderr}"


@pytest.fixture
def wifi_sandbox(script_sandbox):
  scripts, bin_dir = script_sandbox
  root = bin_dir.parent
  state = root / "wifi profiles"
  state.mkdir()
  ensure = scripts / "ensure_wifi.sh"
  ensure.write_text(ensure.read_text(encoding="utf-8")
                    .replace("LOCK_FILE=/run/lock/cluster-wifi.lock", 'LOCK_FILE="$TEST_WIFI_LOCK"'), encoding="utf-8", newline="\n")
  commands = {
    "flock": "exit 0",
    "nmcli": '''printf '%s\\n' "$*" >> "$NMCLI_CALL_LOG"
field=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --terse) shift;;
    --fields|--get-values) field=$2; shift 2;;
    --escape|--wait) shift 2;;
    *) break;;
  esac
done
case "$1 $2" in
  'device status') printf '%s\\n' "${MOCK_DEVICES:-wlan0:wifi}";;
  'device show')
    case "$field" in
      GENERAL.TYPE)
        if [[ "$3" == eth* ]]; then printf 'ethernet\\n'; else printf 'wifi\\n'; fi;;
      GENERAL.CON-UUID)
        if [[ -f "$NM_STATE/$3.active" ]]; then cat "$NM_STATE/$3.active"; else printf -- '--\\n'; fi;;
    esac;;
  'connection show')
    if [[ ${MOCK_NM_LIST_ERROR:-} == 1 ]]; then printf 'NetworkManager unavailable\\n' >&2; exit 10; fi
    if [[ $# == 2 ]]; then
      for file in "$NM_STATE"/*.type; do
        [[ -f "$file" ]] || continue
        uuid=${file##*/}; uuid=${uuid%.type}
        printf '%s:%s\\n' "$uuid" "$(< "$file")"
      done
    else
      case "$field" in
        802-11-wireless.ssid) cat "$NM_STATE/$4.ssid";;
        connection.interface-name) cat "$NM_STATE/$4.interface";;
      esac
    fi;;
  'connection add')
    if [[ ${MOCK_NM_ADD_ERROR:-} == 1 ]]; then printf 'Profile creation failed\\n' >&2; exit 1; fi
    uuid=00000000-0000-0000-0000-000000000099
    printf 'wifi\\n' > "$NM_STATE/$uuid.type"
    shift 2
    while [[ $# -gt 0 ]]; do
      case "$1" in
        ssid) printf '%s\\n' "$2" > "$NM_STATE/$uuid.ssid";;
        ifname) printf '%s\\n' "$2" > "$NM_STATE/$uuid.interface";;
        *) printf '%s\\n' "$2" > "$NM_STATE/$uuid.$1";;
      esac
      shift 2
    done;;
  'connection modify')
    uuid=$4; shift 4
    while [[ $# -gt 0 ]]; do
      printf '%s\\n' "$2" > "$NM_STATE/$uuid.$1"
      shift 2
    done;;
  'radio wifi') :;;
  *) printf 'Unexpected nmcli command\\n' >&2; exit 1;;
esac''',
  }
  for command, contents in commands.items():
    path = bin_dir / command
    path.write_text("#!/usr/bin/env bash\n" + contents + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
  return script_sandbox, state, {"NM_STATE": state.name, "NMCLI_CALL_LOG": "nmcli.log", "TEST_WIFI_LOCK": "wifi.lock"}


def _wifi_profile(state, ssid="Android", interface="wlan0", kind="wifi", uuid="00000000-0000-0000-0000-000000000001"):
  for key, value in {"ssid": ssid, "interface": interface, "type": kind, "con-name": "Renamed hotspot"}.items():
    (state / f"{uuid}.{key}").write_text(value + "\n", encoding="utf-8")
  return uuid


def test_vehicle_wifi_is_saved_without_an_ap_and_repeated_setup_reuses_it(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  for _ in range(2):
    result = _run_script(sandbox, "ensure_wifi.sh", extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "12345678" not in result.stdout + result.stderr
  assert len(list(state.glob("*.ssid"))) == 1
  uuid = "00000000-0000-0000-0000-000000000099"
  expected = {"ssid": "Android", "interface": "wlan0", "connection.autoconnect": "yes",
              "connection.autoconnect-priority": "100", "connection.autoconnect-retries": "0",
              "connection.permissions": "", "802-11-wireless.powersave": "2",
              "wifi-sec.key-mgmt": "wpa-psk", "wifi-sec.psk": "12345678", "wifi-sec.psk-flags": "0"}
  for key, value in expected.items():
    assert (state / f"{uuid}.{key}").read_text().rstrip("\n") == value
  log = (state.parent / "nmcli.log").read_text()
  assert log.count("connection add ") == 1
  assert log.count("connection modify ") == 1
  assert "connection up" not in log and "device wifi connect" not in log
  assert (state.parent / "iw.log").read_text().splitlines() == ["dev wlan0 set power_save off", "dev wlan0 get power_save"] * 2


def test_wifi_power_save_driver_error_preserves_the_profile_without_disconnect(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=dict(env, MOCK_IW_ERROR="1"))
  assert result.returncode == 0, result.stdout + result.stderr
  assert "saved profile applies on reconnection" in result.stderr
  uuid = "00000000-0000-0000-0000-000000000099"
  assert (state / f"{uuid}.802-11-wireless.powersave").read_text() == "2\n"
  assert "connection up" not in (state.parent / "nmcli.log").read_text()


@pytest.mark.parametrize("interface", ["wlan0", "", "--"])
@pytest.mark.parametrize("kind", ["wifi", "802-11-wireless"])
def test_existing_wifi_profile_is_matched_by_ssid_and_keeps_other_settings(wifi_sandbox, interface, kind):
  sandbox, state, env = wifi_sandbox
  uuid = _wifi_profile(state, interface=interface, kind=kind)
  (state / f"{uuid}.ipv4.method").write_text("manual\n", encoding="utf-8")
  (state / f"{uuid}.wifi-sec.psk-flags").write_text("1\n", encoding="utf-8")
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  assert "profile reused" in result.stdout
  assert len(list(state.glob("*.ssid"))) == 1
  assert (state / f"{uuid}.con-name").read_text() == "Renamed hotspot\n"
  assert (state / f"{uuid}.ipv4.method").read_text() == "manual\n"
  assert (state / f"{uuid}.wifi-sec.psk-flags").read_text() == "1\n"


@pytest.mark.parametrize("ssid, interface, kind", [("Guest", "wlan0", "wifi"), ("Android", "wlan1", "wifi"), ("Android", "wlan0", "802-3-ethernet")])
def test_vehicle_wifi_does_not_replace_unrelated_profiles(wifi_sandbox, ssid, interface, kind):
  sandbox, state, env = wifi_sandbox
  uuid = _wifi_profile(state, ssid=ssid, interface=interface, kind=kind)
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  assert "profile saved" in result.stdout
  assert (state / f"{uuid}.ssid").read_text() == ssid + "\n"
  assert (state / f"{uuid}.interface").read_text() == interface + "\n"
  assert not (state / f"{uuid}.wifi-sec.psk").exists()


def test_vehicle_wifi_preserves_literal_credentials(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  ssid, password = "AP;$(touch gotcha)", "pass;$(touch gotcha)"
  env = dict(env, CLUSTER_WIFI_SSID=ssid, CLUSTER_WIFI_PASSWORD=password)
  result = _run_script(sandbox, "ensure_wifi.sh", "wlan1", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  uuid = "00000000-0000-0000-0000-000000000099"
  assert (state / f"{uuid}.ssid").read_text() == ssid + "\n"
  assert (state / f"{uuid}.wifi-sec.psk").read_text() == password + "\n"
  assert not (state.parent / "gotcha").exists()
  assert password not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["MOCK_NM_LIST_ERROR", "MOCK_NM_ADD_ERROR"])
def test_vehicle_wifi_reports_networkmanager_errors(wifi_sandbox, failure):
  sandbox, state, env = wifi_sandbox
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=dict(env, **{failure: "1"}))
  assert result.returncode != 0
  assert not list(state.glob("*.ssid"))
  assert "Automatic connection enabled" not in result.stdout


@pytest.mark.parametrize("nm_failure", [False, True])
def test_wifi_boot_setup_preserves_existing_receiver_and_account(wifi_sandbox, nm_failure):
  sandbox, _state, env = wifi_sandbox
  env.update(_service_environment(sandbox))
  root = sandbox[1].parent
  units = root / env["TEST_UNIT_DIR"]
  (units / "cluster-hdmi.service.d").mkdir(parents=True)
  unit = units / "cluster-hdmi.service"
  unit.write_text("[Service]\nUser=custom\nExecStart=custom display options\n", encoding="utf-8")
  account = units / "cluster-hdmi.service.d" / "account.conf"
  account.write_text("keep original account\n", encoding="utf-8")
  if nm_failure:
    env["MOCK_NM_LIST_ERROR"] = "1"
  result = _run_script(sandbox, "service.sh", "wifi", extra_env=env)
  assert (result.returncode != 0) == nm_failure, result.stdout + result.stderr
  assert unit.read_text() == "[Service]\nUser=custom\nExecStart=custom display options\n"
  assert account.read_text() == "keep original account\n"
  settings = (units / "cluster-hdmi.service.d" / "wifi.conf").read_text()
  assert "Wants=NetworkManager.service" in settings
  assert "After=NetworkManager.service" in settings
  assert "ExecStartPre=-+/usr/bin/timeout --kill-after=2s 10s /bin/bash " in settings
  assert "ensure_wifi.sh wlan0" in settings
  assert "wifi_power_save.sh" in settings
  assert "12345678" not in settings
  assert (root / "systemctl.log").read_text().splitlines() == ["daemon-reload", "start NetworkManager.service"]


def _write_checksums(directory):
  files = sorted(path for path in directory.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
  (directory / "SHA256SUMS").write_text("".join(
    f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(directory).as_posix()}\n" for path in files
  ), encoding="utf-8", newline="\n")


@pytest.fixture
def update_sandbox(script_sandbox):
  scripts, bin_dir = script_sandbox
  root = bin_dir.parent
  updater = scripts / "update.sh"
  updater.write_text(updater.read_text(encoding="utf-8")
                     .replace("TARGET=/opt/cluster-receiver", 'TARGET="$TEST_TARGET_DIR"')
                     .replace("STATE=/var/lib/cluster-receiver/updates", 'STATE="$TEST_UPDATE_STATE_DIR"')
                     .replace("WIFI_CONF=/etc/systemd/system/cluster-hdmi.service.d/wifi.conf", 'WIFI_CONF="$TEST_WIFI_CONF"')
                     .replace("WIFI_DISPATCHER=/etc/NetworkManager/dispatcher.d/90-cluster-wifi-power",
                              'WIFI_DISPATCHER="$TEST_WIFI_DISPATCHER"')
                     .replace("/etc/systemd/system/cluster-hdmi.service", '"$TEST_UNIT_FILE"')
                     .replace("/usr/bin/python3", "preflight-python"), encoding="utf-8", newline="\n")
  environment = {
    "TEST_TARGET_DIR": "receiver with spaces", "TEST_UPDATE_STATE_DIR": "updates with spaces",
    "TEST_UNIT_FILE": "installed.service", "SYSTEMCTL_CALL_LOG": "systemctl.log",
    "MOCK_START_COUNT_FILE": "start-count", "MOCK_INVOCATION_COUNT_FILE": "invocation-count",
    "MOCK_WINDOWS_MODES": "1" if os.name == "nt" else "0",
    "TEST_WIFI_CONF": "unit dropins/wifi.conf", "TEST_WIFI_DISPATCHER": "NM dispatcher/90-cluster-wifi-power",
  }
  target = root / environment["TEST_TARGET_DIR"]
  incoming = root / "incoming with spaces"
  for directory, label in ((target, "old"), (incoming, "new")):
    (directory / "scripts").mkdir(parents=True)
    for name in ("cluster_receiver.py", "hdmi_display.py"):
      (directory / name).write_text(f"# {label}\n", encoding="utf-8", newline="\n")
    for name in ("README.md", "requirements.txt", "cluster-hdmi.service"):
      (directory / name).write_text(label + "\n", encoding="utf-8", newline="\n")
    shutil.copyfile(scripts / "common.sh", directory / "scripts" / "common.sh")
  # The first update must work when the previously installed release has no
  # updater, and recovery must keep working after that release is restored.
  shutil.copyfile(updater, incoming / "scripts" / "update.sh")
  for name in ("ensure_wifi.sh", "wifi_boot_setup.sh", "wifi_power_save.sh"):
    shutil.copyfile(scripts / name, incoming / "scripts" / name)
  helper = incoming / "scripts" / "wifi_boot_setup.sh"
  helper.write_text(helper.read_text(encoding="utf-8")
                    .replace("TARGET=/opt/cluster-receiver", 'TARGET="$TEST_TARGET_DIR"')
                    .replace("WIFI_CONF=/etc/systemd/system/cluster-hdmi.service.d/wifi.conf", 'WIFI_CONF="$TEST_WIFI_CONF"')
                    .replace("WIFI_DISPATCHER=/etc/NetworkManager/dispatcher.d/90-cluster-wifi-power",
                             'WIFI_DISPATCHER="$TEST_WIFI_DISPATCHER"'), encoding="utf-8", newline="\n")
  (incoming / "new_module.py").write_text("# added in new release\n", encoding="utf-8")
  _write_checksums(incoming)
  (target / "local-settings.json").write_text('{"rotation":270}\n', encoding="utf-8")
  (root / "installed.service").write_text("keep custom ExecStart, User and Group\n", encoding="utf-8")
  (root / "account.conf").write_text("keep account drop-in\n", encoding="utf-8")
  for command, contents in {
    "systemctl": '''printf '%s\\n' "$*" >> "$SYSTEMCTL_CALL_LOG"
count=0
[[ ! -s "$MOCK_START_COUNT_FILE" ]] || read -r count < "$MOCK_START_COUNT_FILE"
case "$1" in
  show)
    case "$2" in
      --property=ActiveState) printf '%s\\n' "${MOCK_ACTIVE_STATE:-active}";;
      --property=InvocationID)
        if [[ ${MOCK_UNSTABLE_INVOCATION:-} == 1 && $count == 1 ]]; then
          reads=0
          [[ ! -s "$MOCK_INVOCATION_COUNT_FILE" ]] || read -r reads < "$MOCK_INVOCATION_COUNT_FILE"
          reads=$((reads + 1)); printf '%s\\n' "$reads" > "$MOCK_INVOCATION_COUNT_FILE"
          printf '%032x\\n' "$reads"
        else printf '%032x\\n' "$count"; fi;;
    esac;;
  start)
    count=$((count + 1)); printf '%s\\n' "$count" > "$MOCK_START_COUNT_FILE"
    if [[ ${MOCK_NEW_START_ERROR:-} == 1 && $count == 1 ]]; then exit 1; fi;;
  is-active)
    if [[ ${MOCK_ALL_RUNTIME_ERROR:-} == 1 || ( ${MOCK_NEW_RUNTIME_ERROR:-} == 1 && $count == 1 ) ]]; then exit 3; fi;;
esac''',
    "journalctl": '''printf 'journal %s\\n' "$*" >> "$SYSTEMCTL_CALL_LOG"
read -r count < "$MOCK_START_COUNT_FILE"
if [[ ${MOCK_MISSING_READY:-} == 1 && $count == 1 ]]; then
  printf 'No ready message in this invocation\\n'
else printf 'Cluster receiver ready\\nOrange Pi HDMI display ready\\n'; fi''',
    "preflight-python": '''cat > /dev/null
if [[ ${MOCK_PREFLIGHT_ERROR:-} == 1 ]]; then printf 'Python preflight failed\\n' >&2; exit 1; fi''',
    "sleep": "exit 0",
    "flock": "[[ ${MOCK_LOCK_ERROR:-} != 1 ]]",
    "install": '''if [[ ${MOCK_COPY_ERROR:-} == 1 && "$*" == *"incoming with spaces/hdmi_display.py"* && "$*" == *".cluster-file."* ]]; then
  printf 'Simulated file copy failure\\n' >&2; exit 1
fi
if [[ ${MOCK_INTERRUPTED:-} == 1 && "$*" == *"incoming with spaces/hdmi_display.py"* && "$*" == *".cluster-file."* ]]; then
  kill -KILL "$PPID"
  exit 1
fi
if [[ ${MOCK_WINDOWS_MODES:-} == 1 ]]; then
  # Git Bash cannot represent owner-only Linux modes on all Windows ACLs.
  arguments=()
  for argument in "$@"; do
    if [[ "$argument" == 700 ]]; then arguments+=(755); else arguments+=("$argument"); fi
  done
  exec /usr/bin/install "${arguments[@]}"
fi
exec /usr/bin/install "$@"''',
  }.items():
    path = bin_dir / command
    path.write_text("#!/usr/bin/env bash\n" + contents + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
  return script_sandbox, environment, target, incoming


def _update(update_sandbox, action="apply", extra_env=None):
  sandbox, environment, _target, incoming = update_sandbox
  environment = dict(environment, **(extra_env or {}))
  args = (action, incoming.name) if action == "apply" else (action,)
  return _run_script(sandbox, "update.sh", *args, extra_env=environment, timeout=30)


def test_remote_update_and_manual_rollback_preserve_configuration(update_sandbox):
  sandbox, environment, target, _incoming = update_sandbox
  root = sandbox[1].parent
  (target / "__pycache__").mkdir()
  (target / "__pycache__" / "cluster_receiver.cpython-314.pyc").write_bytes(b"old cached bytecode")
  result = _update(update_sandbox)
  assert result.returncode == 0, result.stdout + result.stderr
  assert "Receiver apply complete" in result.stdout
  assert (target / "cluster_receiver.py").read_text() == "# new\n"
  assert (target / "new_module.py").exists()
  assert not list((target / "__pycache__").glob("*.pyc"))
  assert (target / "local-settings.json").read_text() == '{"rotation":270}\n'
  assert (root / "installed.service").read_text() == "keep custom ExecStart, User and Group\n"
  assert (root / "account.conf").read_text() == "keep account drop-in\n"
  state = root / environment["TEST_UPDATE_STATE_DIR"]
  backup = state / (state / "latest").read_text().strip()
  assert (backup / "previous" / "cluster_receiver.py").read_text() == "# old\n"
  assert (state / "update.sh").exists()

  result = _update(update_sandbox, "rollback")
  assert result.returncode == 0, result.stdout + result.stderr
  assert (target / "cluster_receiver.py").read_text() == "# old\n"
  assert not (target / "new_module.py").exists()
  assert not (target / "scripts" / "update.sh").exists()
  assert (state / "update.sh").exists()
  # Recovery always has a standalone script outside the runtime directory.
  result = _run_script(sandbox, f"../../{environment['TEST_UPDATE_STATE_DIR']}/update.sh", "rollback", extra_env=environment)
  assert result.returncode == 0, result.stdout + result.stderr
  assert (target / "cluster_receiver.py").read_text() == "# new\n"
  calls = (root / "systemctl.log").read_text().splitlines()
  assert "daemon-reload" in calls
  assert not any(line.startswith(("enable", "disable", "set-default")) or "display-manager" in line for line in calls)
  assert all("_SYSTEMD_INVOCATION_ID=" in line for line in calls if line.startswith("journal "))


def test_failed_automatic_recovery_remembers_the_correct_backup(update_sandbox):
  sandbox, environment, target, _incoming = update_sandbox
  state = sandbox[1].parent / environment["TEST_UPDATE_STATE_DIR"]
  result = _update(update_sandbox, extra_env={"MOCK_ALL_RUNTIME_ERROR": "1"})
  assert result.returncode != 0
  assert "Automatic recovery failed" in result.stderr
  assert (state / "recovery-needed").exists()
  assert not (state / "latest").exists()
  assert (target / "cluster_receiver.py").read_text() == "# old\n"
  result = _update(update_sandbox)
  assert result.returncode != 0
  assert "Run rollback before another update" in result.stderr
  result = _update(update_sandbox, "rollback")
  assert result.returncode == 0, result.stdout + result.stderr
  assert not (state / "recovery-needed").exists()
  assert (state / "latest").exists()
  assert (target / "cluster_receiver.py").read_text() == "# old\n"


def test_interrupted_update_can_restore_files_and_running_service(update_sandbox):
  sandbox, environment, target, _incoming = update_sandbox
  state = sandbox[1].parent / environment["TEST_UPDATE_STATE_DIR"]
  result = _update(update_sandbox, extra_env={"MOCK_INTERRUPTED": "1"})
  assert result.returncode != 0
  assert (target / "cluster_receiver.py").read_text() == "# new\n"
  assert (state / "recovery-needed").exists()
  assert not (state / "latest").exists()
  result = _update(update_sandbox, "rollback", extra_env={"MOCK_ACTIVE_STATE": "inactive"})
  assert result.returncode == 0, result.stdout + result.stderr
  assert (target / "cluster_receiver.py").read_text() == "# old\n"
  assert not (target / "new_module.py").exists()
  assert not (state / "recovery-needed").exists()
  assert "start cluster-hdmi.service" in (sandbox[1].parent / "systemctl.log").read_text()


@pytest.mark.parametrize("failure", ["MOCK_NEW_START_ERROR", "MOCK_NEW_RUNTIME_ERROR", "MOCK_MISSING_READY", "MOCK_UNSTABLE_INVOCATION", "MOCK_COPY_ERROR"])
def test_failed_update_restores_previous_files_and_service(update_sandbox, failure):
  sandbox, environment, target, _incoming = update_sandbox
  result = _update(update_sandbox, extra_env={failure: "1"})
  assert result.returncode != 0
  assert "Previous receiver files restored" in result.stderr, result.stdout + result.stderr
  assert (target / "cluster_receiver.py").read_text() == "# old\n"
  assert not (target / "new_module.py").exists()
  assert not (target / "scripts" / "update.sh").exists()
  state = sandbox[1].parent / environment["TEST_UPDATE_STATE_DIR"]
  assert not (state / "latest").exists()
  assert not (state / "recovery-needed").exists()
  assert (state / "update.sh").exists()


@pytest.mark.parametrize("failure", ["checksum", "path", "syntax", "MOCK_PREFLIGHT_ERROR", "MOCK_LOCK_ERROR"])
def test_invalid_update_does_not_stop_running_receiver(update_sandbox, failure):
  sandbox, _environment, target, incoming = update_sandbox
  extra_env = {}
  if failure == "checksum":
    (incoming / "cluster_receiver.py").write_text("# incomplete transfer\n", encoding="utf-8")
  elif failure == "path":
    with (incoming / "SHA256SUMS").open("a", encoding="utf-8") as file:
      file.write("0" * 64 + "  ../outside.py\n")
  elif failure == "syntax":
    (incoming / "scripts" / "common.sh").write_text("if\n", encoding="utf-8")
    _write_checksums(incoming)
  else:
    extra_env[failure] = "1"
  result = _update(update_sandbox, extra_env=extra_env)
  assert result.returncode != 0
  assert (target / "cluster_receiver.py").read_text() == "# old\n"
  log = sandbox[1].parent / "systemctl.log"
  assert not log.exists() or not any(line.startswith(("stop ", "start ")) for line in log.read_text().splitlines())


def test_update_keeps_explicitly_stopped_service_stopped(update_sandbox):
  sandbox, _environment, target, _incoming = update_sandbox
  result = _update(update_sandbox, extra_env={"MOCK_ACTIVE_STATE": "inactive"})
  assert result.returncode == 0, result.stdout + result.stderr
  assert "leaving it stopped" in result.stdout
  assert (target / "cluster_receiver.py").read_text() == "# new\n"
  calls = (sandbox[1].parent / "systemctl.log").read_text().splitlines()
  assert not any(line.startswith("start ") for line in calls)
