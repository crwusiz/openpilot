import os

import pytest

from .test_scripts import (
  _run_script, _service_environment, _update, _wifi_profile, _write_checksums,
  script_sandbox as script_sandbox, update_sandbox as update_sandbox, wifi_sandbox as wifi_sandbox,
)


def _active(state, uuid, interface="wlan0"):
  (state / f"{interface}.active").write_text(uuid + "\n", encoding="utf-8")


def test_requested_hotspot_prefers_active_duplicate_uuid(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  first = _wifi_profile(state)
  active = _wifi_profile(state, uuid="00000000-0000-0000-0000-000000000002")
  _active(state, active)
  (state / f"{first}.802-11-wireless.powersave").write_text("3\n")
  (state / f"{first}.wifi-sec.psk").write_text("keep original credential\n")
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  assert len(list(state.glob("*.ssid"))) == 2
  assert (state / f"{first}.wifi-sec.psk").read_text() == "keep original credential\n"
  assert (state / f"{first}.802-11-wireless.powersave").read_text() == "3\n"
  assert (state / f"{active}.802-11-wireless.powersave").read_text() == "2\n"
  assert "actual=off active_profile=disabled" in result.stdout


@pytest.mark.parametrize("explicit_password", [None, "new explicit $(touch unexpected-file) secret"])
def test_reused_requested_hotspot_preserves_user_security_and_network_policy(wifi_sandbox, monkeypatch, explicit_password):
  sandbox, state, env = wifi_sandbox
  monkeypatch.delenv("CLUSTER_WIFI_PASSWORD", raising=False)
  active = _wifi_profile(state)
  _active(state, active)
  previous = {"wifi-sec.psk": "previous private password", "wifi-sec.key-mgmt": "sae", "wifi-sec.psk-flags": "2",
              "connection.permissions": "user:receiver:", "connection.autoconnect-priority": "-7",
              "ipv4.method": "manual", "ipv4.addresses": "192.168.0.11/24", "ipv6.method": "disabled"}
  for key, value in previous.items():
    (state / f"{active}.{key}").write_text(value + "\n")
  if explicit_password is not None:
    env = dict(env, CLUSTER_WIFI_PASSWORD=explicit_password)
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  for key, value in previous.items():
    expected = explicit_password if key == "wifi-sec.psk" and explicit_password is not None else value
    assert (state / f"{active}.{key}").read_text() == expected + "\n"
  assert (state / f"{active}.connection.autoconnect").read_text() == "yes\n"
  assert (state / f"{active}.connection.autoconnect-retries").read_text() == "0\n"
  assert (state / f"{active}.802-11-wireless.powersave").read_text() == "2\n"
  assert previous["wifi-sec.psk"] not in result.stdout + result.stderr
  if explicit_password is not None:
    assert explicit_password not in result.stdout + result.stderr
  assert not (state.parent / "unexpected-file").exists()


def test_reused_hotspot_does_not_create_missing_secret_settings(wifi_sandbox, monkeypatch):
  sandbox, state, env = wifi_sandbox
  monkeypatch.delenv("CLUSTER_WIFI_PASSWORD", raising=False)
  active = _wifi_profile(state)
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  for field in ("wifi-sec.psk", "wifi-sec.key-mgmt", "wifi-sec.psk-flags", "connection.permissions", "connection.autoconnect-priority"):
    assert not (state / f"{active}.{field}").exists()


def test_explicit_empty_password_is_rejected_without_defaulting_or_modifying_profile(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  active = _wifi_profile(state)
  (state / f"{active}.wifi-sec.psk").write_text("keep private password\n")
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=dict(env, CLUSTER_WIFI_PASSWORD=""))
  assert result.returncode != 0
  assert (state / f"{active}.wifi-sec.psk").read_text() == "keep private password\n"
  assert not (state.parent / "nmcli.log").exists()


def test_actual_active_wifi_profile_preserves_foreign_credentials_addresses_and_policy(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  active = _wifi_profile(state, ssid="Different network", interface="wlan1")
  _active(state, active, "wlan1")
  env = dict(env, MOCK_DEVICES="eth0:ethernet\nwlan1:wifi")
  previous = {"wifi-sec.psk": "different private credential", "ipv4.method": "manual",
              "ipv4.addresses": "192.168.0.7/24", "connection.autoconnect-priority": "-5"}
  for key, value in previous.items():
    (state / f"{active}.{key}").write_text(value + "\n")
  result = _run_script(sandbox, "ensure_wifi.sh", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  assert (state / f"{active}.ssid").read_text() == "Different network\n"
  for key, value in previous.items():
    assert (state / f"{active}.{key}").read_text() == value + "\n"
  assert (state / f"{active}.802-11-wireless.powersave").read_text() == "2\n"
  assert (state.parent / "iw.log").read_text().splitlines() == ["dev wlan1 set power_save off", "dev wlan1 get power_save"]
  assert "interface=wlan1 desired=off actual=off" in result.stdout
  assert previous["wifi-sec.psk"] not in result.stdout + result.stderr


@pytest.mark.parametrize("action", ["up", "reapply"])
def test_dispatcher_reapplies_power_setting_to_real_interface_after_reconnect(wifi_sandbox, action):
  sandbox, state, env = wifi_sandbox
  active = _wifi_profile(state, interface="wlan1")
  _active(state, active, "wlan1")
  env.update(_service_environment(sandbox))
  scripts, bin_dir = sandbox
  setup = _run_script(sandbox, "wifi_boot_setup.sh", "install", extra_env=env)
  assert setup.returncode == 0, setup.stdout + setup.stderr
  dispatcher = bin_dir.parent / env["TEST_DISPATCHER_DIR"] / "90-cluster-wifi-power"
  assert dispatcher.is_file() and not dispatcher.is_symlink()
  if os.name == "posix":
    assert dispatcher.stat().st_mode & 0o777 == 0o755
  # Redirect the installed wrapper's receiver boundary, then execute its actual
  # event filtering, timeout and argument forwarding with mocked NM/iw.
  dispatcher.write_text(dispatcher.read_text().replace(
    "helper=/opt/cluster-receiver/scripts/wifi_power_save.sh",
    'helper="$PWD/' + scripts.relative_to(bin_dir.parent).as_posix() + '/wifi_power_save.sh"',
  ), encoding="utf-8", newline="\n")
  for _ in range(2):
    (state / f"{active}.802-11-wireless.powersave").write_text("3\n")
    result = _run_script(sandbox, "../../" + env["TEST_DISPATCHER_DIR"] + "/90-cluster-wifi-power",
                         "wlan1", action, extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "interface=wlan1 desired=off actual=off" in result.stdout
    assert (state / f"{active}.802-11-wireless.powersave").read_text() == "2\n"
  assert (state.parent / "iw.log").read_text().count("dev wlan1 set power_save off") == 2
  assert "connection up" not in (state.parent / "nmcli.log").read_text()


@pytest.mark.parametrize("action", ["down", "connectivity-change", "dhcp4-change"])
def test_unrelated_dispatcher_events_do_not_change_wifi(wifi_sandbox, action):
  sandbox, state, env = wifi_sandbox
  result = _run_script(sandbox, "wifi_power_save.sh", "--dispatch", "wlan1", action, extra_env=env)
  assert result.returncode == 0
  assert not (state.parent / "iw.log").exists()
  assert not (state.parent / "nmcli.log").exists()


def test_dispatcher_does_not_apply_wireless_settings_to_ethernet(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  result = _run_script(sandbox, "wifi_power_save.sh", "--dispatch", "eth0", "up", extra_env=env)
  assert result.returncode == 0
  assert not (state.parent / "iw.log").exists()
  assert "connection modify" not in (state.parent / "nmcli.log").read_text()


def test_driver_ignoring_power_save_request_is_reported_as_on(wifi_sandbox):
  sandbox, state, env = wifi_sandbox
  active = _wifi_profile(state)
  _active(state, active)
  result = _run_script(sandbox, "wifi_power_save.sh", "wlan0", extra_env=dict(env, MOCK_IW_POWER="on"))
  assert result.returncode == 0
  assert "desired=off actual=on" in result.stdout
  assert "actual=off" not in result.stdout
  assert "could not verify" in result.stderr
  assert (state / f"{active}.802-11-wireless.powersave").read_text() == "2\n"


def test_wifi_boot_upgrade_keeps_custom_preparation_and_is_idempotent(wifi_sandbox):
  sandbox, _state, env = wifi_sandbox
  env.update(_service_environment(sandbox))
  root = sandbox[1].parent
  directory = root / env["TEST_UNIT_DIR"] / "cluster-hdmi.service.d"
  directory.mkdir(parents=True)
  original = "[Service]\nEnvironment=CUSTOM_WIFI_SETUP=1\nExecStartPre=/bin/true\n"
  (directory / "wifi.conf").write_text(original)
  for _ in range(2):
    result = _run_script(sandbox, "wifi_boot_setup.sh", "install", extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
  upgraded = (directory / "wifi.conf").read_text()
  assert upgraded.startswith(original)
  assert upgraded.count("scripts/wifi_power_save.sh") == 1
  assert "--kill-after=2s 8s" in upgraded


def _wifi_paths(update_sandbox):
  sandbox, env, _target, _incoming = update_sandbox
  root = sandbox[1].parent
  return root / env["TEST_WIFI_CONF"], root / env["TEST_WIFI_DISPATCHER"]


def test_update_repairs_old_receiver_wifi_setup_and_applies_live_power_save(update_sandbox):
  sandbox, _env, _target, _incoming = update_sandbox
  wifi, dispatcher = _wifi_paths(update_sandbox)
  result = _update(update_sandbox)
  assert result.returncode == 0, result.stdout + result.stderr
  assert "wifi_power_save.sh" in wifi.read_text()
  assert "--dispatch" in dispatcher.read_text()
  assert "interface=wlan0 desired=off actual=off" in result.stdout
  root = sandbox[1].parent
  assert (root / "installed.service").read_text() == "keep custom ExecStart, User and Group\n"
  assert (root / "account.conf").read_text() == "keep account drop-in\n"
  calls = (root / "systemctl.log").read_text().splitlines()
  assert calls.index("daemon-reload") < calls.index("start cluster-hdmi.service")
  assert not any("NetworkManager" in call or call.startswith(("enable", "disable", "set-default")) for call in calls)


@pytest.mark.parametrize("existing", [False, True])
def test_failed_update_restores_original_wifi_files_or_their_absence(update_sandbox, existing):
  wifi, dispatcher = _wifi_paths(update_sandbox)
  if existing:
    wifi.parent.mkdir()
    dispatcher.parent.mkdir()
    wifi.write_text("[Service]\nExecStartPre=/bin/true\n")
    dispatcher.write_text("#!/bin/sh\n# previous custom dispatcher\n")
  old_wifi = wifi.read_bytes() if existing else None
  old_dispatcher = dispatcher.read_bytes() if existing else None
  result = _update(update_sandbox, extra_env={"MOCK_NEW_START_ERROR": "1"})
  assert result.returncode != 0
  assert "Previous receiver files restored" in result.stderr
  assert (wifi.read_bytes() if wifi.exists() else None) == old_wifi
  assert (dispatcher.read_bytes() if dispatcher.exists() else None) == old_dispatcher


def test_manual_rollback_restores_absence_and_second_rollback_restores_guard(update_sandbox):
  _sandbox, _env, target, _incoming = update_sandbox
  wifi, dispatcher = _wifi_paths(update_sandbox)
  result = _update(update_sandbox)
  assert result.returncode == 0, result.stdout + result.stderr
  new_wifi, new_dispatcher = wifi.read_bytes(), dispatcher.read_bytes()
  result = _update(update_sandbox, "rollback")
  assert result.returncode == 0, result.stdout + result.stderr
  assert not wifi.exists() and not dispatcher.exists()
  assert not (target / "scripts/wifi_power_save.sh").exists()
  result = _update(update_sandbox, "rollback")
  assert result.returncode == 0, result.stdout + result.stderr
  assert wifi.read_bytes() == new_wifi and dispatcher.read_bytes() == new_dispatcher


def test_interrupted_wifi_migration_is_recovered_with_service_state(update_sandbox):
  _sandbox, _env, target, incoming = update_sandbox
  helper = incoming / "scripts/wifi_boot_setup.sh"
  with helper.open("a", encoding="utf-8", newline="\n") as file:
    file.write('kill -KILL "$PPID"\n')
  _write_checksums(incoming)
  wifi, dispatcher = _wifi_paths(update_sandbox)
  result = _update(update_sandbox)
  assert result.returncode != 0
  assert wifi.exists() and dispatcher.exists()
  result = _update(update_sandbox, "rollback", extra_env={"MOCK_ACTIVE_STATE": "inactive"})
  assert result.returncode == 0, result.stdout + result.stderr
  assert not wifi.exists() and not dispatcher.exists()
  assert (target / "cluster_receiver.py").read_text() == "# old\n"


@pytest.mark.parametrize("installed_service", [False, True])
def test_install_repairs_existing_wifi_without_changing_receiver_or_desktop(script_sandbox, installed_service):
  env = _service_environment(script_sandbox)
  scripts, bin_dir = script_sandbox
  root = bin_dir.parent
  unit = root / env["TEST_UNIT_DIR"] / "cluster-hdmi.service"
  if installed_service:
    unit.parent.mkdir()
    unit.write_text("custom receiver unit\n")
  installer = scripts / "install.sh"
  installer.write_text(installer.read_text().replace("/opt/cluster-receiver", '"$PACKAGE_DIR"')
                       .replace("/etc/systemd/system/cluster-hdmi.service", '"$TEST_UNIT_DIR/cluster-hdmi.service"')
                       .replace("/usr/bin/python3", "mock-python"), encoding="utf-8", newline="\n")
  for command in ("apt-get", "mock-python"):
    path = bin_dir / command
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
  result = _run_script(script_sandbox, "install.sh", extra_env=env)
  assert result.returncode == 0, result.stdout + result.stderr
  dispatcher = root / env["TEST_DISPATCHER_DIR"] / "90-cluster-wifi-power"
  assert dispatcher.exists() == installed_service
  if installed_service:
    assert unit.read_text() == "custom receiver unit\n"
    assert (root / "systemctl.log").read_text().splitlines() == ["daemon-reload"]
  else:
    assert not (root / "systemctl.log").exists()
