import asyncio
import os
import signal
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.maintenance import ClusterActions
from openpilot.selfdrive.addon.cluster.hdmi_display import maintenance


PEER = "192.168.0.84"


@pytest.fixture
def actions(tmp_path):
  for name in ("pi_update.sh", "commit_compare.sh", "gitpull.sh", "log_upload.sh", "cluster_debug.log"):
    (tmp_path / name).touch()
  return ClusterActions(tmp_path, runner=AsyncMock(return_value=(0, "done")),
                        commit_file=tmp_path / "CommitCompare", package_reader=lambda: "a" * 64,
                        log_file=tmp_path / "cluster_debug.log", park_check=Mock(return_value=True))


def test_update_targets_only_the_connected_pi_and_retries_do_not_deploy_twice(actions):
  async def run():
    first = actions.submit("pi_update", PEER, "a" * 32, PEER)
    # A lost HTTP response followed by video disconnection retains the job.
    assert actions.submit("pi_update", PEER, "a" * 32, None) == first
    with pytest.raises(RuntimeError, match="already running"):
      actions.submit("commit_check", PEER, "b" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    actions.runner.assert_awaited_once_with(["bash", str(actions.scripts / "pi_update.sh"), PEER], 900.0)
    completed = actions.status(PEER, None)
    assert completed["state"] == "succeeded" and "reboot" in completed["message"].lower()
    assert "owner" not in completed
    assert actions.submit("pi_update", PEER, "a" * 32, None) == completed

  asyncio.run(run())


def test_stale_pi_receivers_cannot_start_touch_uploads(actions):
  with pytest.raises(ValueError, match="Unknown cluster action"):
    actions.submit("upload_logs", PEER, "b" * 32, PEER)
  assert actions.status(PEER, PEER) == {"state": "idle"}
  actions.runner.assert_not_called()


@pytest.mark.parametrize(("valid", "service", "gear", "age", "parked"), [
  (True, "carState", "park", 0.1, True),
  (True, "carState", "reverse", 0.1, False),
  (True, "carState", "drive", 0.1, False),
  (True, "carState", "park", 0.6, False),
  (True, "carState", "park", -0.1, False),
  (False, "carState", "park", 0.1, False),
  (True, "controlsState", "park", 0.1, False),
  (False, None, None, 0.0, False),
])
def test_car_is_parked_requires_latest_valid_car_state(monkeypatch, valid, service, gear, age, parked):
  socket = object()
  event = SimpleNamespace(valid=valid, which=lambda: service, logMonoTime=int((10.0 - age) * 1e9),
                          carState=SimpleNamespace(gearShifter=gear)) if service else None
  messaging = SimpleNamespace(sub_sock=Mock(return_value=socket), recv_one=Mock(return_value=event))
  structs = SimpleNamespace(CarState=SimpleNamespace(GearShifter=SimpleNamespace(park="park")))
  monkeypatch.setitem(sys.modules, "openpilot.cereal", SimpleNamespace(messaging=messaging))
  monkeypatch.setitem(sys.modules, "opendbc.car", SimpleNamespace(structs=structs))
  monkeypatch.setattr(maintenance, "time", SimpleNamespace(monotonic=lambda: 10.0))

  assert maintenance.car_is_parked() is parked
  messaging.sub_sock.assert_called_once_with("carState", timeout=200, conflate=True)
  messaging.recv_one.assert_called_once_with(socket)


def test_park_upload_cannot_run_without_fresh_gear_or_connected_pi(actions):
  async def run():
    action = maintenance.PARK_UPLOAD_ACTION
    with pytest.raises(PermissionError, match="Connected Orange Pi required"):
      actions.submit(action, "192.168.0.99", "a" * 32, PEER)
    actions.park_check.assert_not_called()
    actions.park_check.return_value = False
    job = actions.submit(action, PEER, "a" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    result = actions.status(PEER, PEER)
    assert result["job_id"] == job["job_id"]
    assert result["state"] == "failed" and result["reason"] == "park_required"
    actions.park_check.assert_called_once_with()
    actions.runner.assert_not_called()

  asyncio.run(run())


@pytest.mark.parametrize(("output", "state"), [
  ("uploaded", "succeeded"), ("CLUSTER_PI_LOG_WARNING: retry", "warning"), ("CLUSTER_LOG_WARNING: retry", "warning"),
])
def test_park_upload_uses_fixed_cluster_log_and_deduplicates_retries(actions, output, state):
  async def run():
    action = maintenance.PARK_UPLOAD_ACTION
    actions.runner.return_value = 0, output
    job = actions.submit(action, PEER, "a" * 32, PEER)
    assert actions.submit(action, PEER, "a" * 32, PEER) == job
    await asyncio.gather(*actions.tasks)
    actions.park_check.assert_called_once_with()
    actions.runner.assert_awaited_once_with(["bash", str(actions.scripts / "log_upload.sh"), str(actions.log_file)], 240.0)
    assert actions.status(PEER, PEER)["state"] == state

  asyncio.run(run())


@pytest.mark.parametrize("action, request_id", [("reboot_c4", "a" * 32), ("pi_update; reboot", "a" * 32),
                                               ("pi_update", "$(unsafe)"), ("pi_update", None)])
def test_unknown_commands_and_invalid_request_ids_never_start_a_process(actions, action, request_id):
  with pytest.raises(ValueError):
    actions.submit(action, PEER, request_id, PEER)
  actions.runner.assert_not_called()


def test_other_hotspot_clients_cannot_update_pi_or_read_its_job(actions):
  with pytest.raises(PermissionError):
    actions.submit("pi_update", "192.168.0.99", "a" * 32, PEER)
  with pytest.raises(PermissionError):
    actions.status("192.168.0.99", PEER)
  with pytest.raises(PermissionError):
    actions.status(None, None)
  assert actions.status(PEER, PEER) == {"state": "idle"}
  actions.runner.assert_not_called()


@pytest.mark.parametrize("error", [TimeoutError(), OSError("unavailable")])
def test_failed_jobs_release_the_busy_state_and_do_not_publish_command_output(actions, error):
  async def run():
    actions.runner.side_effect = error
    actions.submit("pi_update", PEER, "a" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    assert actions.active is None
    assert actions.status(PEER, None)["state"] == "failed"
    actions.runner.side_effect = None
    actions.runner.return_value = 5, "private command output must not be returned"
    actions.submit("pi_update", PEER, "b" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    result = actions.status(PEER, None)
    assert result["state"] == "failed" and "exit 5" in result["message"]
    assert "private command" not in str(result)
  asyncio.run(run())


def test_missing_scripts_do_not_report_success(actions):
  async def run():
    (actions.scripts / "pi_update.sh").unlink()
    actions.submit("pi_update", PEER, "a" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    assert actions.status(PEER, PEER)["state"] == "failed"
    actions.runner.assert_not_called()
  asyncio.run(run())


def test_expired_command_kills_its_linux_process_group_including_ssh_children(monkeypatch):
  async def run():
    async def blocked_read(_size):
      await asyncio.Event().wait()
    process = SimpleNamespace(pid=12345, returncode=None, stdout=SimpleNamespace(read=blocked_read),
                              wait=AsyncMock(side_effect=[TimeoutError(), 0]))
    create = AsyncMock(return_value=process)
    kill = Mock()
    monkeypatch.setattr(maintenance.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(maintenance, "os", SimpleNamespace(environ=os.environ, name="posix", killpg=kill))
    with pytest.raises(TimeoutError):
      await maintenance.run_command(["bash", "pi_update.sh", PEER], 0.001)
    assert kill.call_args_list[0].args == (12345, signal.SIGTERM)
    assert kill.call_args_list[1].args[0] == 12345
    assert create.call_args.kwargs["start_new_session"] is True
  asyncio.run(run())


def test_command_output_is_bounded_while_the_pipe_is_fully_drained(monkeypatch):
  async def run():
    chunks = [b"x" * 4096] * 20 + [b"latest error", b""]
    process = SimpleNamespace(returncode=0, stdout=SimpleNamespace(read=AsyncMock(side_effect=chunks)), wait=AsyncMock(return_value=0))
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(maintenance.asyncio, "create_subprocess_exec", create)
    code, output = await maintenance.run_command(["bash", "log_upload.sh"], 1.0)
    assert code == 0 and len(output) == maintenance.MAX_OUTPUT
    assert output.endswith("latest error")
    assert process.stdout.read.await_count == len(chunks)
    assert create.call_args.kwargs["stderr"] == asyncio.subprocess.STDOUT
  asyncio.run(run())


@pytest.mark.parametrize("comparison, available, state", [
  ('"1234567" != "abcdef0"', True, "succeeded"), ('"1234567" == "1234567"', False, "succeeded"),
  (f'"{"a" * 40}" != "{"b" * 40}"', True, "succeeded"), (f'"{"a" * 40}" == "{"a" * 40}"', False, "succeeded"),
  ('"ERROR" != "ERROR"', False, "failed"), ('"1234567" != "1234567"', False, "failed"),
])
def test_git_pull_requires_successful_commit_check_with_different_commits(actions, comparison, available, state):
  async def run():
    with pytest.raises(ValueError, match="Check commits"):
      actions.submit("git_pull", PEER, "a" * 32, PEER)
    actions.commit_file.write_text(comparison)
    actions.submit("commit_check", PEER, "b" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    result = actions.status(PEER, PEER)
    assert result["state"] == state and result["update_available"] is available
    assert result["c4_pi_sha256"] == "a" * 64
    assert result["commit_state"] == ("available" if available else "current" if state == "succeeded" else "failed")
    if available:
      first = actions.submit("git_pull", PEER, "c" * 32, PEER)
      assert actions.submit("git_pull", PEER, "c" * 32, None) == first
      await asyncio.gather(*actions.tasks)
      actions.runner.assert_awaited_with(["bash", str(actions.scripts / "gitpull.sh")], 240.0)
      result = actions.status(PEER, PEER)
      assert "started in tmux" in result["message"] and not result["update_available"]
    else:
      with pytest.raises(ValueError, match="Check commits"):
        actions.submit("git_pull", PEER, "c" * 32, PEER)
  asyncio.run(run())


@pytest.mark.parametrize("failure", ["command", "timeout", "invalid_result", "missing_result"])
def test_failed_recheck_clears_previously_available_update(actions, failure):
  async def run():
    actions.commit_file.write_text('"1234567" != "abcdef0"')
    actions.submit("commit_check", PEER, "a" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    if failure == "command":
      actions.runner.return_value = 124, "Commit check failed: remote lookup timed out after 10 seconds."
    elif failure == "timeout":
      actions.runner.side_effect = TimeoutError()
    elif failure == "invalid_result":
      actions.commit_file.write_text('"ERROR" != "ERROR"')
    else:
      actions.commit_file.unlink()
    pending = actions.submit("commit_check", PEER, "b" * 32, PEER)
    assert pending["commit_state"] == "checking" and not pending["update_available"]
    assert "commit_compare" not in pending
    await asyncio.gather(*actions.tasks)
    result = actions.status(PEER, PEER)
    assert result["state"] == "failed" and not result["update_available"]
    assert result["commit_state"] == "failed" and "commit_compare" not in result
    with pytest.raises(ValueError, match="Check commits"):
      actions.submit("git_pull", PEER, "c" * 32, PEER)
  asyncio.run(run())


def test_unchanged_update_does_not_claim_a_reboot(actions):
  async def run():
    actions.runner.return_value = 0, "CLUSTER_PI_UNCHANGED: already current"
    actions.submit("pi_update", PEER, "a" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    result = actions.status(PEER, PEER)
    assert result["unchanged"] and result["message"] == "Pi already up to date"
  asyncio.run(run())
