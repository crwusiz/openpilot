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
  monkeypatch.setattr(collector.shutil, "which", lambda _command: "/mock/ssh")
  return log, output


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
