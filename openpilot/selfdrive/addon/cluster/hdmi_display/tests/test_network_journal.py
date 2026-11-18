from io import BytesIO
import json
import shlex
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display import pi_log_collector as collector


@pytest.fixture
def remote_network():
  namespace = {"__name__": "network_journal_test"}
  exec(collector.REMOTE_NETWORK_JOURNAL_SCRIPT, namespace)
  return SimpleNamespace(**namespace)


def record(message, **fields):
  return json.dumps({"__REALTIME_TIMESTAMP": "1700000000000000", "__MONOTONIC_TIMESTAMP": "123400000", "_BOOT_ID": "a" * 32,
                     "MESSAGE": message, "_HOSTNAME": "private-host", **fields})


@pytest.mark.parametrize("message,expected", [
  ("<info> [1700000000.0] device (wlan0): state change: activated -> deactivating "
   + "(reason 'user-requested', sys-iface-state: 'managed')", "event=device_state from=activated to=deactivating reason=user-requested"),
  ("device (wlan0): state change: config -> failed (reason 'private-profile')", "event=device_state from=config to=failed reason=unknown"),
  ("device (wlan0): supplicant interface state: completed -> disconnected", "event=supplicant_state from=completed to=disconnected"),
  ("device (wlan0): state change: activated -> disconnected (reason 'none', managed-type: 'full')",
   "event=device_state from=activated to=disconnected reason=none"),
  ("device (wlan0): state change: config -> failed (reason 'private-profile', managed-type: 'private-profile')",
   "event=device_state from=config to=failed reason=unknown"),
])
def test_networkmanager_rebuilds_only_fixed_states_and_known_reason(remote_network, message, expected):
  event = remote_network.sanitize_record(record(message), "networkmanager")
  assert expected in event and "utc=2023-11-14T22:13:20.000Z" in event
  assert "mono_s=123.400000" in event
  assert "boot_id=" + "a" * 32 in event
  assert "private" not in event and "<info>" not in event and "[1700000000.0]" not in event
  assert "sys-iface-state" not in event and "managed-type" not in event


@pytest.mark.parametrize("suffix", [
  ", managed-type: 'full'",  # Missing the closing event parenthesis.
  ", managed-type: 'full') private-ssid",
  ", managed-type: 'private profile')",
  ", managed-type: 'full', profile: 'private-profile')",
  ", profile: 'private-profile')",
  ", managed-type: '" + "a" * 33 + "')",
])
def test_malformed_or_unknown_management_suffixes_never_copy_private_tails(remote_network, suffix):
  message = "device (wlan0): state change: activated -> disconnected (reason 'none'" + suffix
  assert remote_network.sanitize_record(record(message), "networkmanager") is None


@pytest.mark.parametrize("message,event", [
  ("wlan0: deauthenticated from aa:bb:cc:dd:ee:ff (Reason: 3=PRIVATE_SECRET)", "deauthenticated reason_code=3"),
  ("wlan0: deauthenticating from aa:bb:cc:dd:ee:ff by local choice (Reason: 3=DEAUTH_LEAVING)", "deauthenticating reason_code=3"),
  ("wlan0: Connection to AP aa:bb:cc:dd:ee:ff lost", "connection_lost"),
  ("wlan0: authenticate with aa:bb:cc:dd:ee:ff (local address=11:22:33:44:55:66)", "authenticating"),
  ("wlan0: associated", "associated"),
  ("wlan0: association with aa:bb:cc:dd:ee:ff timed out", "association_timeout"),
])
def test_kernel_events_keep_only_event_names_and_numeric_reason(remote_network, message, event):
  result = remote_network.sanitize_record(record(message), "kernel")
  assert "event=" + event in result
  assert all(secret not in result for secret in ("aa:bb", "11:22", "PRIVATE_SECRET", "DEAUTH_LEAVING", "private-host"))


@pytest.mark.parametrize("value", [
  "not-json private-ssid", "[]", "null", "42", record("private password=private-secret"),
  record("device (wlan1): state change: activated -> disconnected (reason 'none')"),
  record("device (wlan0): state change: private-profile -> disconnected (reason 'none')"),
  record("wlan0: associated", MESSAGE=[1, 2, 3]),
  record("wlan0: associated", __REALTIME_TIMESTAMP="99999999999999999999"),
  record("wlan0: associated", __REALTIME_TIMESTAMP="1700000000000000\nprivate"),
])
def test_malformed_unknown_and_other_interface_messages_are_never_copied(remote_network, value):
  assert remote_network.sanitize_record(value, "networkmanager") is None
  assert remote_network.sanitize_record(value, "kernel") is None


def test_unrecognized_boot_and_monotonic_identifiers_are_omitted_instead_of_copied(remote_network):
  event = remote_network.sanitize_record(record("wlan0: associated", _BOOT_ID="private-profile", __MONOTONIC_TIMESTAMP="private-ssid"), "kernel")
  assert "event=associated" in event and "boot_id=" not in event and "mono_s=" not in event and "private" not in event


@pytest.mark.parametrize("missing", ["timeout", "journalctl"])
def test_missing_tools_never_fall_back_to_an_unbounded_query(remote_network, monkeypatch, missing):
  monkeypatch.setattr(remote_network.shutil, "which", lambda name: None if name == missing else "/mock/" + name)
  spawn = Mock()
  monkeypatch.setattr(remote_network.subprocess, "Popen", spawn)
  assert remote_network.read_events([], "wlan0") == ([], "required_tool_unavailable", False)
  spawn.assert_not_called()


@pytest.mark.parametrize("status,expected", [(0, "ok"), (1, "query_unavailable_or_empty"),
                                             (124, "query_timeout_or_killed"), (137, "query_timeout_or_killed")])
def test_queries_bound_runtime_at_the_source_and_discard_all_stderr(remote_network, monkeypatch, status, expected):
  monkeypatch.setattr(remote_network.shutil, "which", lambda name: "/mock/" + name)
  process = SimpleNamespace(stdout=BytesIO(b"{}\n"), wait=Mock(return_value=status), kill=Mock())
  spawn = Mock(return_value=process)
  monkeypatch.setattr(remote_network.subprocess, "Popen", spawn)
  lines, result, limited = remote_network.read_events(["_TRANSPORT=kernel"], "wlan0")
  assert lines == ["{}"] and result == expected and not limited
  command = spawn.call_args.args[0]
  assert command[:4] == ["/mock/timeout", "--signal=KILL", "1s", "/mock/journalctl"]
  assert "--grep=wlan0" in command and command[-2:] == ["-n", "128"]
  assert "_TRANSPORT=kernel" in command and "-k" not in command and "-b" not in command
  assert spawn.call_args.kwargs["stdin"] == spawn.call_args.kwargs["stderr"] == subprocess.DEVNULL
  process.wait.assert_called_once_with(timeout=2)
  assert spawn.call_args.kwargs["start_new_session"] is True
  assert process.stdout.closed


def test_raw_scan_limit_keeps_complete_records_and_kills_only_the_query(remote_network, monkeypatch):
  monkeypatch.setattr(remote_network.shutil, "which", lambda name: "/mock/" + name)
  line = record("wlan0: associated") + "\n"
  namespace = remote_network.read_events.__globals__
  monkeypatch.setitem(namespace, "RAW_LIMIT", len(line.encode()) + 4)
  process = SimpleNamespace(stdout=BytesIO((line + "private-ssid" * 100).encode()), wait=Mock(return_value=-9), kill=Mock())
  monkeypatch.setattr(remote_network.subprocess, "Popen", Mock(return_value=process))
  lines, status, limited = remote_network.read_events([], "wlan0")
  assert lines == [line.rstrip()] and status == "scan_limited" and limited
  process.kill.assert_not_called()  # The timeout supervisor must remain alive to stop its journal child.
  process.wait.assert_called_once_with(timeout=2)
  assert process.stdout.closed


def test_wait_timeout_has_one_bounded_cleanup_retry(remote_network, monkeypatch):
  monkeypatch.setattr(remote_network.shutil, "which", lambda name: "/mock/" + name)
  process = SimpleNamespace(pid=12345678, stdout=BytesIO(b""), kill=Mock(),
                            wait=Mock(side_effect=[subprocess.TimeoutExpired("mock", 1), -9]))
  monkeypatch.setattr(remote_network.subprocess, "Popen", Mock(return_value=process))
  killpg = Mock()
  monkeypatch.setattr(remote_network.os, "killpg", killpg, raising=False)
  monkeypatch.setattr(remote_network.signal, "SIGKILL", 9, raising=False)
  assert remote_network.read_events([], "wlan0") == ([], "query_timeout_or_killed", False)
  assert process.wait.call_count == 2
  assert [call.kwargs["timeout"] for call in process.wait.call_args_list] == [2, 1]
  killpg.assert_called_once_with(process.pid, remote_network.signal.SIGKILL)
  process.kill.assert_not_called()


@pytest.mark.parametrize("boot", [None, "a" * 32])
def test_section_limits_include_headers_markers_rows_and_complete_records(remote_network, boot):
  lines = [record("device (wlan0): state change: activated -> deactivating (reason 'user-requested')", _BOOT_ID=boot)] * 200
  section = remote_network.section("networkmanager", lines, "ok", True)
  assert len(section.encode()) <= 8 * 1024 and section.count("utc=") <= 64
  assert "WIFI JOURNAL SECTION LIMITED" in section and "raw_scan_limited=1" in section
  assert "private-host" not in section
  assert all(line.startswith("utc=") for line in section.splitlines() if "utc=" in line)


def counters(section):
  line = next(line for line in section.splitlines() if line.startswith("source_rows="))
  return {key: int(value) for key, value in (field.split("=", 1) for field in line.split())}


def test_section_counts_recognized_ignored_and_emitted_rows_without_copying_them(remote_network):
  rows = [record("device (wlan0): state change: activated -> disconnected (reason 'none', managed-type: 'full')"),
          "not-json private-password", record("private-ssid"),
          record("device (wlan0): supplicant interface state: completed -> disconnected")]
  section = remote_network.section("networkmanager", rows, "ok", False)
  assert counters(section) == {"source_rows": 4, "matched_events": 2, "emitted_events": 2, "ignored_rows": 2, "omitted_events": 0}
  assert section.count("utc=") == 2 and "private" not in section
  assert "WIFI JOURNAL SECTION LIMITED" not in section


def test_successful_query_with_only_ignored_rows_is_distinct_from_an_empty_query(remote_network):
  ignored = remote_network.section("networkmanager", ["invalid-json", record("private-ssid")], "ok", False)
  empty = remote_network.section("networkmanager", [], "ok", False)
  assert "status=ok" in ignored and "status=ok" in empty
  assert counters(ignored) == {"source_rows": 2, "matched_events": 0, "emitted_events": 0, "ignored_rows": 2, "omitted_events": 0}
  assert counters(empty) == dict.fromkeys(counters(ignored), 0)
  assert "private" not in ignored and "utc=" not in ignored


def test_row_and_byte_omissions_are_counted_inside_the_section_budget(remote_network):
  short = record("wlan0: associated", _BOOT_ID=None, __MONOTONIC_TIMESTAMP=None)
  row_limited = remote_network.section("kernel", [short] * 128, "ok", False)
  counts = counters(row_limited)
  assert counts == {"source_rows": 128, "matched_events": 128, "emitted_events": 64, "ignored_rows": 0, "omitted_events": 64}
  long = record("device (wlan0): state change: activated -> deactivating (reason 'user-requested', managed-type: 'full')")
  byte_limited = remote_network.section("networkmanager", [long] * 128, "ok", False)
  counts = counters(byte_limited)
  assert 0 < counts["emitted_events"] < 64
  assert counts["matched_events"] == counts["emitted_events"] + counts["omitted_events"] == 128
  assert counts["ignored_rows"] == 0 and byte_limited.count("utc=") == counts["emitted_events"]
  assert all(len(section.encode()) <= 8192 for section in (row_limited, byte_limited))
  assert all("WIFI JOURNAL SECTION LIMITED" in section for section in (row_limited, byte_limited))


def test_section_recognition_work_is_limited_to_the_source_query_budget(remote_network):
  namespace = remote_network.section.__globals__
  parse = Mock(wraps=remote_network.sanitize_record)
  namespace["sanitize_record"] = parse
  section = remote_network.section("kernel", [record("wlan0: associated")] * 1000, "ok", True)
  assert parse.call_count == 128
  assert counters(section)["source_rows"] == counters(section)["matched_events"] == 128
  assert "raw_scan_limited=1" in section


def test_optional_failures_and_event_flood_leave_the_receiver_query_last(remote_network, capsys):
  namespace = remote_network.main.__globals__
  namespace["read_events"] = lambda _arguments, _pattern: ([record("wlan0: associated")] * 200, "query_timeout_or_killed", False)
  assert remote_network.main() == 0
  output = capsys.readouterr().out
  assert len(output.encode()) <= 16 * 1024 and "query_timeout_or_killed" in output
  tokens = shlex.split(collector.REMOTE_SCRIPT)
  assert tokens[-1] == collector.REMOTE_JOURNAL_SCRIPT
  assert tokens.index(collector.REMOTE_UDP_SCRIPT) < tokens.index(collector.REMOTE_NETWORK_JOURNAL_SCRIPT) < len(tokens) - 1

