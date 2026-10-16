"""Fixed C4 maintenance jobs requested by the connected Orange Pi touch panel."""
import asyncio
from collections import OrderedDict
import ipaddress
import logging
import os
from pathlib import Path
import re
import signal
import uuid


LOG = logging.getLogger(__name__)
ACTION_PATH = "/api/cluster/actions"
MAX_OUTPUT = 64 * 1024


async def run_command(command, budget_s):
  env = {key: value for key, value in os.environ.items() if key not in ("TMUX", "TMUX_PANE")}
  process = await asyncio.create_subprocess_exec(
    *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT, start_new_session=True, env=env,
  )
  output = bytearray()

  async def read():
    while chunk := await process.stdout.read(4096):
      output.extend(chunk)
      del output[:-MAX_OUTPUT]
    return await process.wait(), output.decode("utf-8", errors="replace")

  try:
    return await asyncio.wait_for(read(), budget_s)
  finally:
    if process.returncode is None:
      # Deployment/upload shell children must stop too if the dashboard exits
      # or a job expires; otherwise a retry could start a second deployment.
      def stop(sig):
        try:
          if os.name == "posix":
            os.killpg(process.pid, sig)
          elif sig == signal.SIGTERM:
            process.terminate()
          else:
            process.kill()
        except ProcessLookupError:
          pass
      stop(signal.SIGTERM)
      try:
        await asyncio.wait_for(process.wait(), 3.0)
      except TimeoutError:
        stop(signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
        await process.wait()


class ClusterActions:
  def __init__(self, scripts="/data/openpilot/scripts", log_file="/data/log/cluster_debug.log", runner=run_command):
    self.scripts = Path(scripts)
    self.log_file = Path(log_file)
    self.runner = runner
    self.jobs = OrderedDict()
    self.active = None
    self.tasks = set()

  @staticmethod
  def _public(job):
    return {key: value for key, value in job.items() if key != "owner"}

  def status(self, owner, connected_peer):
    if owner is None:
      raise PermissionError("Connected Orange Pi required")
    for job in reversed(self.jobs.values()):
      if job["owner"] == owner:
        return self._public(job)
    if owner != connected_peer:
      raise PermissionError("Connected Orange Pi required")
    return {"state": "idle"}

  def submit(self, action, owner, request_id, connected_peer):
    owner = str(ipaddress.IPv4Address(owner))
    if action not in ("pi_update", "upload_logs"):
      raise ValueError("Unknown cluster action")
    if not isinstance(request_id, str) or re.fullmatch(r"[a-f0-9]{32}", request_id) is None:
      raise ValueError("Invalid action request ID")
    # Repeating a request after a lost HTTP reply never starts a second job,
    # even if Pi deployment has already disconnected its video connection.
    for job in self.jobs.values():
      if job["owner"] == owner and job["request_id"] == request_id:
        if job["action"] != action:
          raise ValueError("Action request ID was already used")
        return self._public(job)
    if owner != connected_peer:
      raise PermissionError("Connected Orange Pi required")
    if self.active is not None:
      raise RuntimeError("A cluster maintenance job is already running")
    job = {"job_id": uuid.uuid4().hex, "request_id": request_id, "owner": owner,
           "action": action, "state": "running", "message": ""}
    self.jobs[job["job_id"]] = job
    while len(self.jobs) > 8:
      self.jobs.popitem(last=False)
    self.active = job["job_id"]
    task = asyncio.create_task(self._run(job))
    self.tasks.add(task)
    task.add_done_callback(self.tasks.discard)
    return self._public(job)

  async def _run(self, job):
    action = job["action"]
    script = self.scripts / ("pi_update.sh" if action == "pi_update" else "log_upload.sh")
    args = [job["owner"]] if action == "pi_update" else [str(self.log_file)]
    LOG.info("[CLUSTER_TOUCH_ACTION] action=%s job=%s state=running", action, job["job_id"])
    try:
      if not script.is_file() or (action == "upload_logs" and not self.log_file.is_file()):
        job.update(state="failed", message="Required script or cluster log is missing")
        return
      code, output = await self.runner(["bash", str(script), *args], 900.0 if action == "pi_update" else 240.0)
      if code != 0:
        job.update(state="failed", message=f"Command failed (exit {code}); check C4 dashboard logs")
        LOG.warning("[CLUSTER_TOUCH_ACTION] action=%s job=%s exit=%s output_tail=%s", action, job["job_id"], code, output[-4096:])
      elif action == "upload_logs" and "CLUSTER_PI_LOG_WARNING:" in output:
        job.update(state="warning", message="C4 log uploaded; Pi log needs collection/upload retry")
      else:
        job.update(state="succeeded", message="Pi reboot scheduled" if action == "pi_update" else "C4 and Pi log upload completed")
    except TimeoutError:
      job.update(state="failed", message="Operation timed out")
    except asyncio.CancelledError:
      job.update(state="failed", message="Operation interrupted")
      raise
    except Exception:
      LOG.exception("[CLUSTER_TOUCH_ACTION] action=%s job=%s", action, job["job_id"])
      job.update(state="failed", message="Operation could not be started")
    finally:
      self.active = None
      LOG.info("[CLUSTER_TOUCH_ACTION] action=%s job=%s state=%s", action, job["job_id"], job["state"])
