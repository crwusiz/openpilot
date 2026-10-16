from io import BytesIO
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display import pi_log_collector as collector


@pytest.fixture
def state(tmp_path, monkeypatch):
  log = tmp_path / "cluster_debug.log"
  log.write_text("[14:02:03] [CLUSTER_NETWORK_SUCCESS] Orange Pi connected from 192.168.0.84:59123.\n", encoding="utf-8")
  output = tmp_path / "cluster_pi_debug.log"
  monkeypatch.setattr(collector, "get_connected_pi_ip", lambda: None)
  monkeypatch.setattr(collector, "C4_CRASH_LOG_DIRECTORY", tmp_path)
  monkeypatch.setattr(collector.shutil, "which", lambda _command: "/mock/ssh")
  monkeypatch.setattr(collector, "collect_c4_wifi", lambda _host, _env, _deadline: "status=mock_local_wifi")
  return log, output


def test_report_keeps_c4_crash_history_even_if_pi_cannot_be_reached(state):
  log, output = state
  crash = log.parent / "cluster_crash.log"
  crash.write_text("[CLUSTER_CRASH] stage=bootstrap\nImportError: native backend unavailable\n", encoding="utf-8")
  assert not collector.collect_pi_log(log, output, {"CLUSTER_PI_HOST": "bad-host"}, Mock())
  text = output.read_text(encoding="utf-8")
  assert "stage=bootstrap" in text and "native backend unavailable" in text
  assert "Collection status: failed" in text


def test_crash_history_bounds_each_file_and_preserves_its_recent_tail(state):
  log, _ = state
  for suffix in ("", ".1"):
    (log.parent / ("cluster_crash.log" + suffix)).write_bytes(b"HEAD\n" + b"old history\n" * 20000 + b"LATEST FAILURE\n")
  text = collector.collect_c4_crashes()
  assert text.count("LATEST FAILURE") == 2 and text.count("HEAD") == 2
  assert len(text.encode()) < 2 * collector.MAX_C4_CRASH_BYTES + 1024
  assert "truncated" in text


def test_remote_snapshot_checks_asset_access_as_receiver_user():
  script = collector.REMOTE_SCRIPT
  assert "NRestarts,User,Group" in script
  assert "/opt/cluster-receiver/assets/fonts" in script and "stat -c" in script
  assert 'runuser -u "$receiver_user" -- /usr/bin/python3 /opt/cluster-receiver/receiver_build.py' in script


def test_explicit_address_precedes_connected_address_and_previous_log(state, monkeypatch):
  log, _ = state
  monkeypatch.setattr(collector, "get_connected_pi_ip", lambda: "192.168.0.90")
  assert collector.resolve_host(log, {"CLUSTER_PI_HOST": "192.168.0.95"}) == ("192.168.0.95", "CLUSTER_PI_HOST")
  assert collector.resolve_host(log, {}) == ("192.168.0.90", "current connection")


@pytest.mark.parametrize("host", ["bad-host", "192.168.0.84;touch bad", "-oProxyCommand=bad", "999.2.3.4", "::1"])
def test_invalid_explicit_address_does_not_fall_back_or_start_ssh(state, host):
  log, output = state
  runner = Mock()
  assert not collector.collect_pi_log(log, output, {"CLUSTER_PI_HOST": host}, runner)
  runner.assert_not_called()
  assert "Collection status: failed" in output.read_text(encoding="utf-8")


def test_disconnected_upload_uses_last_valid_peer_in_bounded_log_tail(state):
  log, _ = state
  log.write_text("x" * collector.MAX_C4_LOG_BYTES + "\n"
                 + "[CLUSTER_NETWORK_SUCCESS] Orange Pi connected from 192.168.0.91:50000.\n"
                 + "[CLUSTER_NETWORK_SUCCESS] Orange Pi connected from 999.1.1.1:50000.\n", encoding="utf-8")
  assert collector.resolve_host(log, {}) == ("192.168.0.91", "last peer in C4 log")


def test_disconnected_long_drive_finds_connection_older_than_one_megabyte(state):
  log, _ = state
  with log.open("ab") as file:
    file.write(b"[CLUSTER_NETWORK_PERF] fps=60\n" * 40000)
  assert log.stat().st_size > 1024 * 1024
  assert collector.resolve_host(log, {}) == ("192.168.0.84", "last peer in C4 log")


def test_connection_record_split_at_backwards_chunk_boundary_is_found(state):
  log, _ = state
  record = b"[CLUSTER_NETWORK_SUCCESS] Orange Pi connected from 192.168.0.92:50000.\n"
  log.write_bytes(record + b"x" * (collector.C4_LOG_CHUNK_BYTES - 20))
  assert collector.resolve_host(log, {}) == ("192.168.0.92", "last peer in C4 log")


def test_address_scan_honors_byte_limit_and_shared_collection_deadline(state, monkeypatch):
  log, output = state
  with log.open("ab") as file:
    file.write(b"x" * (3 * collector.C4_LOG_CHUNK_BYTES))
  monkeypatch.setattr(collector, "MAX_C4_LOG_BYTES", 2 * collector.C4_LOG_CHUNK_BYTES)
  with pytest.raises(ValueError, match="No Orange Pi address"):
    collector.resolve_host(log, {})
  ticks = iter([100.0, 126.0, 126.0])
  monkeypatch.setattr(collector, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
  runner = Mock()
  assert not collector.collect_pi_log(log, output, {}, runner)
  runner.assert_not_called()
  assert "25 second collection deadline" in output.read_text(encoding="utf-8")


def test_definite_usb_only_log_writes_skipped_sidecar_without_ssh(state):
  log, output = state
  log.write_text("[CLUSTER_CONFIG] Transport: usb\n[CLUSTER_USB_PERF] fps=20\n", encoding="utf-8")
  runner = Mock()
  assert collector.collect_pi_log(log, output, {}, runner)
  runner.assert_not_called()
  assert "Collection status: skipped" in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("header", ["Transport: network", "[CLUSTER_NETWORK_LISTEN] Waiting for Orange Pi"])
def test_usb_and_network_evidence_cannot_be_skipped_if_pi_never_connected(state, header):
  log, output = state
  log.write_text("Transport: usb\n" + header + "\n", encoding="utf-8")
  runner = Mock()
  assert not collector.collect_pi_log(log, output, {}, runner)
  runner.assert_not_called()
  assert "Collection status: failed" in output.read_text(encoding="utf-8")


def test_explicit_pi_address_still_collects_for_usb_log(state):
  log, output = state
  log.write_text("Transport: usb\n", encoding="utf-8")
  runner = Mock(return_value=collector.SSHResult(0, "Pi journal", ""))
  assert collector.collect_pi_log(log, output, {"CLUSTER_PI_HOST": "192.168.0.97"}, runner)
  assert runner.call_args.args[0][-2] == "192.168.0.97"


def test_missing_pi_address_writes_failure_report_without_network_activity(state):
  log, output = state
  log.write_text("USB cluster session\n", encoding="utf-8")
  runner = Mock()
  assert not collector.collect_pi_log(log, output, {}, runner)
  runner.assert_not_called()
  assert "set CLUSTER_PI_HOST" in output.read_text(encoding="utf-8")


def test_success_uses_9122_and_private_askpass_without_password_in_file_or_command(state):
  log, output = state
  calls = []
  password = 'private $() `value` with "quotes"'

  def runner(command, environ, timeout):
    calls.append((command, environ, timeout))
    askpass = Path(environ["SSH_ASKPASS"])
    assert askpass.is_file()
    assert password not in askpass.read_text(encoding="utf-8")
    assert password not in " ".join(command)
    assert environ["CLUSTER_PI_PASSWORD"] == password
    assert environ["SSH_ASKPASS_REQUIRE"] == "force"
    assert environ["DISPLAY"]
    if os.name == "posix":
      assert askpass.stat().st_mode & 0o777 == 0o700
    assert 0 < timeout <= 25
    return collector.SSHResult(0, "[CLUSTER_RX_PERF] display_fps=20.0\n", "")

  assert collector.collect_pi_log(log, output, {"CLUSTER_PI_PASSWORD": password}, runner)
  assert len(calls) == 1
  command, environ, _ = calls[0]
  assert command[command.index("-p") + 1] == "9122"
  assert command[command.index("-l") + 1] == "root"
  assert command[-2] == "192.168.0.84"
  assert command[-1] == collector.REMOTE_COMMAND
  assert "StrictHostKeyChecking=accept-new" in command
  assert not Path(environ["SSH_ASKPASS"]).exists()
  content = output.read_text(encoding="utf-8")
  assert "Collection status: ok" in content
  assert "display_fps=20.0" in content
  assert "C4 collection UTC:" in content
  assert "C4 monotonic uptime:" in content


@pytest.mark.parametrize("error", [
  "ssh: connect to host 192.168.0.84 port 9122: Connection refused\n",
  "ssh: connect to host 192.168.0.84 port 9122: Connection timed out\n",
  "Connection to 192.168.0.84 port 9122 timed out\n",
  "Connection closed by 192.168.0.84 port 9122\n",
  "Connection reset by 192.168.0.84 port 9122\n",
])
def test_transport_error_only_retries_port_22(state, error):
  log, output = state
  calls = []

  def runner(command, _env, _timeout):
    port = command[command.index("-p") + 1]
    calls.append(port)
    return collector.SSHResult(255, "", error) if port == "9122" else collector.SSHResult(0, "Pi journal", "")

  assert collector.collect_pi_log(log, output, {}, runner)
  assert calls == ["9122", "22"]
  assert "retrying port 22" in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("result", [
  collector.SSHResult(255, "", "root@pi: Permission denied (publickey,password)."),
  collector.SSHResult(255, "", "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"),
  collector.SSHResult(1, "service diagnostics", "journalctl: permission denied"),
  collector.SSHResult(-9, "partial diagnostics", "", timed_out=True),
])
def test_authentication_remote_or_deadline_failure_does_not_try_port_22(state, result):
  log, output = state
  runner = Mock(return_value=result)
  assert not collector.collect_pi_log(log, output, {}, runner)
  assert runner.call_count == 1
  assert "Collection status: failed" in output.read_text(encoding="utf-8")


def test_retry_shares_collection_deadline(state, monkeypatch):
  log, output = state
  ticks = iter([100.0, 101.0, 109.0, 112.0])
  monkeypatch.setattr(collector, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
  monkeypatch.setattr(collector, "get_connected_pi_ip", lambda: "192.168.0.84")
  timeouts = []

  def runner(command, _env, timeout):
    timeouts.append(timeout)
    return (collector.SSHResult(255, "", "Connection to pi port 9122 timed out\n") if len(timeouts) == 1
            else collector.SSHResult(0, "journal", ""))

  assert collector.collect_pi_log(log, output, {}, runner)
  assert timeouts == [24.0, 16.0]


def test_report_redacts_password_and_bounds_actual_utf8_file_size(state):
  log, output = state
  password = "secret-value"
  result = collector.SSHResult(0, "가" * collector.MAX_OUTPUT_BYTES + password, password, truncated=True)
  assert collector.collect_pi_log(log, output, {"CLUSTER_PI_PASSWORD": password}, Mock(return_value=result))
  assert output.stat().st_size <= collector.MAX_OUTPUT_BYTES
  content = output.read_text(encoding="utf-8")
  assert password not in content
  assert "<redacted>" in content
  assert "OUTPUT TRUNCATED" in content
  assert not list(output.parent.glob(".cluster_pi_debug.log.*"))


def test_collection_writes_progress_report_before_ssh_so_interruption_leaves_context(state):
  log, output = state

  def interrupted_runner(_command, _env, _timeout):
    content = output.read_text(encoding="utf-8")
    assert "Collection status: collecting" in content
    assert "Pi address: 192.168.0.84" in content
    raise KeyboardInterrupt

  with pytest.raises(KeyboardInterrupt):
    collector.collect_pi_log(log, output, {}, interrupted_runner)
  assert "Collection status: collecting" in output.read_text(encoding="utf-8")


def test_capture_drops_over_limit_data_but_drains_stdout():
  stream = BytesIO(b"abcdefghij")
  capture = collector._Capture(5)
  capture.read(stream)
  assert capture.text() == "abcde"
  assert capture.truncated
  assert stream.closed


def test_readonly_command_collects_journal_clock_renderer_and_filtered_wifi():
  command = collector.REMOTE_SCRIPT
  assert "journalctl -u cluster-hdmi.service --utc --no-pager --output=short-iso -n 2500 --reverse" in command
  assert "cat /proc/sys/kernel/random/boot_id" in command
  assert " -b " not in command and "--since" not in command
  assert "date -u" in command and "cat /proc/uptime" in command
  assert "^SDL_(VIDEODRIVER|RENDER_DRIVER)=" in command
  assert "iw dev wlan0 get power_save" in command
  assert "signal:" in command and "tx bitrate:" in command
  assert "connection show" not in command
  assert "--show-secrets" not in command
  assert "systemctl restart" not in command
  assert "set power_save" not in command


def test_subprocess_timeout_kills_detached_session_and_preserves_bounded_partial_output(monkeypatch):
  process = SimpleNamespace(pid=123456, stdout=BytesIO(b"partial journal"), stderr=BytesIO(b"network unavailable"),
                            returncode=-9, wait=Mock(side_effect=[subprocess.TimeoutExpired("ssh", 1), -9]), kill=Mock())
  spawn = Mock(return_value=process)
  killpg = Mock()
  monkeypatch.setattr(collector.subprocess, "Popen", spawn)
  monkeypatch.setattr(collector.os, "killpg", killpg, raising=False)
  result = collector._run_ssh(["mock-ssh"], {}, 1.0)
  assert result.timed_out
  assert result.stdout == "partial journal"
  assert result.stderr == "network unavailable"
  assert spawn.call_args.kwargs["start_new_session"]
  assert spawn.call_args.kwargs["stdin"] == subprocess.DEVNULL
  assert killpg.called if os.name == "posix" else process.kill.called


@pytest.fixture
def local_wifi(monkeypatch, tmp_path):
  monkeypatch.setattr(collector.sys, "platform", "linux")
  monkeypatch.setattr(collector.shutil, "which", lambda command, **_kwargs: "/mock/" + command)
  # A mutable fake clock exercises budgets without issuing local/network I/O.
  clock = SimpleNamespace(now=100.0)
  monkeypatch.setattr(collector.time, "monotonic", lambda: clock.now)
  return clock, tmp_path


def test_c4_wifi_uses_route_device_and_rebuilds_only_private_numeric_fields(local_wifi):
  clock, sysfs = local_wifi
  statistics = sysfs / "wlan1" / "statistics"
  statistics.mkdir(parents=True)
  (statistics / "rx_bytes").write_text("1234567\n", encoding="ascii")
  (statistics / "rx_errors").write_text("0\n", encoding="ascii")
  (statistics / "tx_packets").write_text("bad-sensitive-value", encoding="ascii")
  calls = []
  private = "PrivateSSID-AB:CD:EF:00:11:22"

  def runner(command, environ, timeout):
    calls.append(command)
    assert "CLUSTER_PI_PASSWORD" not in environ
    assert environ["LC_ALL"] == "C"
    assert 0 < timeout <= min(0.4, 102.0 - clock.now)
    clock.now += 0.05
    if command[0] == "/mock/ip":
      output = '[{"dev":"wlan1","prefsrc":"192.168.77.40","dst":"192.168.77.118"}]'
    elif command[-1] == "power_save":
      output = "Power save: off\n"
    elif command[-1] == "link":
      output = (f"Connected to AB:CD:EF:00:11:22\n\tSSID: {private}\n\tfreq: 2447.0\n\tsignal: -42 dBm\n"
                + "\trx bitrate: 86.7 MBit/s VHT-MCS 8\n\ttx bitrate: 65.0 MBit/s\n")
    elif command[-1] == "info":
      output = f"Interface wlan1\n\taddr AB:CD:EF:00:11:22\n\tssid {private}\n\ttype managed\n\tchannel 8 (2447 MHz), width: 20 MHz\n"
    else:
      output = "Station AB:CD:EF:00:11:22 (on wlan1)\n\ttx retries: 12\n\ttx failed: 3\n\tsignal avg: -44 [-44] dBm\n\texpected throughput: 11.123Mbps\n"
    return collector.SSHResult(0, output, private)

  text = collector.collect_c4_wifi("192.168.77.118", {"CLUSTER_PI_PASSWORD": "sensitive"}, 125.0, runner, sysfs)
  assert calls[0] == ["/mock/ip", "-j", "route", "get", "192.168.77.118"]
  assert all(command[2] == "wlan1" for command in calls[1:])
  for expected in ("route_interface=wlan1", "power_save=off", "interface_type=managed", "freq_mhz=2447.0",
                   "channel_freq_mhz=2447", "signal_dbm=-42", "rx_bitrate_mbps=86.7", "tx_bitrate_mbps=65.0",
                   "station_1_tx_retries=12", "station_1_tx_failed=3", "station_1_signal_avg_dbm=-44",
                   "station_1_expected_throughput_mbps=11.123", "interface_rx_bytes=1234567", "interface_rx_errors=0"):
    assert expected in text
  for excluded in (private, "AB:CD", "192.168.", "prefsrc", "sensitive", "bad-sensitive-value"):
    assert excluded not in text
  assert len(text.encode()) <= collector.MAX_LOCAL_WIFI_BYTES


@pytest.mark.parametrize("interface", ["../private", "wlan0/../../secret", "--help", "wlan0;id", "x" * 16, None])
def test_c4_wifi_rejects_unsafe_route_interfaces_before_iw_or_sysfs(local_wifi, interface):
  import json
  _, sysfs = local_wifi
  runner = Mock(return_value=collector.SSHResult(0, json.dumps([{"dev": interface}]), "private stderr"))
  text = collector.collect_c4_wifi("192.168.77.118", {}, 125.0, runner, sysfs)
  assert runner.call_count == 1
  assert "route_interface_unavailable" in text
  assert "private stderr" not in text


def test_c4_wifi_caps_anonymous_ap_stations_to_two(local_wifi):
  _, sysfs = local_wifi

  def runner(command, _env, _timeout):
    if command[0] == "/mock/ip":
      output = '[{"dev":"ap0"}]'
    elif command[-1] == "info":
      output = "\ttype AP\n\tchannel 1 (2412 MHz)\n"
    elif command[-1] == "dump":
      output = "".join(f"Station 00:11:22:33:44:{index:02x} (on ap0)\n\ttx retries: {index}\n" for index in range(4))
    else:
      output = "SSID: private\n"
    return collector.SSHResult(0, output, "")

  text = collector.collect_c4_wifi("192.168.77.118", {}, 125.0, runner, sysfs)
  assert "interface_type=AP" in text
  assert "anonymous_stations_observed=4" in text
  assert "station_1_tx_retries=0" in text and "station_2_tx_retries=1" in text
  assert "station_3" not in text and "additional_stations_omitted=1" in text
  assert "may not be the Pi" in text
  assert "00:11" not in text and "private" not in text


def test_c4_wifi_shares_two_second_local_budget_and_overall_deadline(local_wifi):
  clock, sysfs = local_wifi
  calls = []

  def runner(command, _env, timeout):
    calls.append(timeout)
    clock.now += timeout
    if command[0] == "/mock/ip":
      return collector.SSHResult(0, '[{"dev":"wlan0"}]', "")
    return collector.SSHResult(-9, "SSID: private", "private error", timed_out=True)

  text = collector.collect_c4_wifi("192.168.77.118", {}, 100.75, runner, sysfs)
  assert sum(calls) <= 0.75 and clock.now <= 100.75
  assert "command_status=timeout" in text and "local_deadline" in text
  assert "private" not in text


def test_c4_wifi_command_and_platform_failures_are_nonfatal(local_wifi, monkeypatch):
  _, sysfs = local_wifi
  runner = Mock(side_effect=FileNotFoundError("private command details"))
  text = collector.collect_c4_wifi("192.168.77.118", {}, 125.0, runner, sysfs)
  assert "command_status=unavailable" in text and "private" not in text
  monkeypatch.setattr(collector.sys, "platform", "win32")
  runner.reset_mock()
  assert collector.collect_c4_wifi("192.168.77.118", {}, 125.0, runner, sysfs) == "status=unsupported_platform"
  runner.assert_not_called()


def test_c4_wifi_metadata_precedes_pi_journal_and_survives_report_cap(state, monkeypatch):
  log, output = state
  monkeypatch.setattr(collector, "collect_c4_wifi", lambda _host, _env, _deadline: "power_save=off\nstation_1_tx_retries=10")
  runner = Mock(return_value=collector.SSHResult(0, "journal\n" * collector.MAX_OUTPUT_BYTES, ""))
  assert collector.collect_pi_log(log, output, {}, runner)
  text = output.read_text(encoding="utf-8")
  assert text.index("C4 Wi-Fi snapshot") < text.index("Orange Pi diagnostics ---")
  assert "power_save=off" in text and "station_1_tx_retries=10" in text
  assert output.stat().st_size <= collector.MAX_OUTPUT_BYTES


def test_local_command_capture_has_small_limits(monkeypatch):
  process = SimpleNamespace(pid=123456, stdout=BytesIO(b"a" * (collector.MAX_LOCAL_COMMAND_BYTES + 99)),
                            stderr=BytesIO(b"private" * 200), returncode=0, wait=Mock(return_value=0))
  monkeypatch.setattr(collector.subprocess, "Popen", Mock(return_value=process))
  result = collector._run_local(["mock-iw"], {}, 0.4)
  assert len(result.stdout) == collector.MAX_LOCAL_COMMAND_BYTES
  assert len(result.stderr) == 1024 and result.truncated


def test_local_wifi_timeouts_leave_remaining_deadline_for_pi_ssh(local_wifi, monkeypatch):
  clock, directory = local_wifi
  log, output = directory / "cluster_debug.log", directory / "cluster_pi_debug.log"
  log.write_text("", encoding="utf-8")
  monkeypatch.setattr(collector, "get_connected_pi_ip", lambda: "192.168.77.118")

  def local_runner(command, _env, timeout):
    clock.now += timeout
    if command[0] == "/mock/ip":
      return collector.SSHResult(0, '[{"dev":"wlan0"}]', "")
    return collector.SSHResult(-9, "SSID: private", "private timeout", timed_out=True)

  monkeypatch.setattr(collector, "_run_local", local_runner)
  ssh = Mock(return_value=collector.SSHResult(0, "Pi journal", ""))
  assert collector.collect_pi_log(log, output, {}, ssh)
  assert ssh.call_count == 1
  assert ssh.call_args.args[2] == pytest.approx(23.0)
  assert clock.now == pytest.approx(102.0)
  text = output.read_text(encoding="utf-8")
  assert "command_status=timeout" in text and "Collection status: ok" in text
  assert "Pi journal" in text and "private" not in text
