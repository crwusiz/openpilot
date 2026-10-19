import asyncio
import os
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.maintenance import ClusterActions
from openpilot.selfdrive.addon.cluster.hdmi_display import maintenance


PEER = "192.168.0.84"


@pytest.fixture
def actions(tmp_path):
  for name in ("pi_update.sh", "log_upload.sh", "cluster_debug.log"):
    (tmp_path / name).touch()
  return ClusterActions(tmp_path, tmp_path / "cluster_debug.log", runner=AsyncMock(return_value=(0, "done")))


def test_update_targets_only_the_connected_pi_and_retries_do_not_deploy_twice(actions):
  async def run():
    first = actions.submit("pi_update", PEER, "a" * 32, PEER)
    # A lost HTTP response followed by video disconnection retains the job.
    assert actions.submit("pi_update", PEER, "a" * 32, None) == first
    with pytest.raises(RuntimeError, match="already running"):
      actions.submit("upload_logs", PEER, "b" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    actions.runner.assert_awaited_once_with(["bash", str(actions.scripts / "pi_update.sh"), PEER], 900.0)
    completed = actions.status(PEER, None)
    assert completed["state"] == "succeeded" and "reboot" in completed["message"].lower()
    assert "owner" not in completed
    assert actions.submit("pi_update", PEER, "a" * 32, None) == completed

  asyncio.run(run())


@pytest.mark.parametrize("output, state", [
  ("upload done", "succeeded"), ("CLUSTER_PI_LOG_WARNING: Pi upload failed", "warning"),
  ("CLUSTER_LOG_WARNING: Crash log upload failed", "warning"),
  ("CLUSTER_LOG_WARNING: Crash log upload failed\nCLUSTER_PI_LOG_WARNING: Pi collection failed", "warning"),
])
def test_log_button_uses_existing_paired_log_upload_and_reports_partial_failure(actions, output, state):
  async def run():
    actions.runner.return_value = 0, output
    actions.submit("upload_logs", PEER, "b" * 32, PEER)
    await asyncio.gather(*actions.tasks)
    actions.runner.assert_awaited_once_with(["bash", str(actions.scripts / "log_upload.sh"), str(actions.log_file)], 240.0)
    assert actions.status(PEER, PEER)["state"] == state
    message = actions.status(PEER, PEER)["message"]
    if "CLUSTER_LOG_WARNING:" in output:
      assert "diagnostic logs" in message and "retry" in message
    elif state == "warning":
      assert "Pi log" in message and "retry" in message
    else:
      assert message == "Cluster log upload completed"
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
    actions.submit("upload_logs", PEER, "b" * 32, PEER)
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
    monkeypatch.setattr(maintenance.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    code, output = await maintenance.run_command(["bash", "log_upload.sh"], 1.0)
    assert code == 0 and len(output) == maintenance.MAX_OUTPUT
    assert output.endswith("latest error")
    assert process.stdout.read.await_count == len(chunks)
  asyncio.run(run())
