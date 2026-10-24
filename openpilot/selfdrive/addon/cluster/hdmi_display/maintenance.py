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

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.receiver_build import package_digest

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
  def __init__(self, scripts="/data/openpilot/scripts", log_file="/data/log/cluster_debug.log", runner=run_command,
               commit_file="/data/params/d/CommitCompare", package_reader=None):
    self.scripts = Path(scripts)
    self.log_file = Path(log_file)
    self.runner = runner
    self.jobs = OrderedDict()
    self.active = None
    self.tasks = set()
    self.commit_file = Path(commit_file)
    self.checks = {}
    package = self.scripts.parent / "openpilot/selfdrive/addon/cluster/hdmi_display/orange_pi"
    self.package_reader = package_reader or (lambda: package_digest(package))

  def _public(self, job):
    return {**self.checks.get(job["owner"], {}), **{key: value for key, value in job.items() if key != "owner"}}

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
    if action not in ("pi_update", "upload_logs", "commit_check", "git_pull"):
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
    if action == "git_pull" and not self.checks.get(owner, {}).get("update_available"):
      raise ValueError("Check commits before requesting Git Pull")
    if action in ("commit_check", "git_pull"):
      self.checks[owner] = {**self.checks.get(owner, {}), "update_available": False, "commit_state": "checking"}
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
    script = self.scripts / {"pi_update": "pi_update.sh", "upload_logs": "log_upload.sh",
                             "commit_check": "commit_compare.sh", "git_pull": "gitpull.sh"}[action]
    args = [job["owner"]] if action == "pi_update" else [str(self.log_file)] if action == "upload_logs" else []
    LOG.info("[CLUSTER_TOUCH_ACTION] action=%s job=%s state=running", action, job["job_id"])
    try:
      if action == "commit_check":
        try:
          digest = await asyncio.to_thread(self.package_reader)
          self.checks[job["owner"]]["c4_pi_sha256"] = digest
        except (OSError, UnicodeError, ValueError):
          self.checks[job["owner"]].pop("c4_pi_sha256", None)
          LOG.warning("C4 receiver package version unavailable", exc_info=True)
      if not script.is_file() or (action == "upload_logs" and not self.log_file.is_file()):
        job.update(state="failed", message="Required script or cluster log is missing")
        return
      code, output = await self.runner(["bash", str(script), *args], 900.0 if action == "pi_update" else 20.0 if action == "commit_check" else 240.0)
      if code != 0:
        job.update(state="failed", message=f"Command failed (exit {code}); check C4 dashboard logs")
        LOG.warning("[CLUSTER_TOUCH_ACTION] action=%s job=%s exit=%s output_tail=%s", action, job["job_id"], code, output[-4096:])
      elif action == "upload_logs" and "CLUSTER_LOG_WARNING:" in output:
        job.update(state="warning", message="Cluster log uploaded; some diagnostic logs need collection/upload retry")
      elif action == "upload_logs" and "CLUSTER_PI_LOG_WARNING:" in output:
        job.update(state="warning", message="C4 log uploaded; Pi log needs collection/upload retry")
      elif action == "commit_check":
        comparison = self.commit_file.read_text(encoding="ascii").strip()
        match = re.fullmatch(r'"([a-f0-9]{7,40})" (==|!=) "([a-f0-9]{7,40})"', comparison)
        if match is None or (match[1] == match[3]) != (match[2] == "=="):
          job.update(state="failed", message="Invalid commit comparison; check again")
        else:
          self.checks[job["owner"]].update(update_available=match[2] == "!=", commit_compare=comparison,
                                           commit_state="available" if match[2] == "!=" else "current")
          job.update(state="succeeded", message="Commit check completed")
      elif action == "git_pull":
        self.checks[job["owner"]]["commit_state"] = "checking"
        job.update(state="succeeded", message="C4 Git Pull started in tmux; C4 will restart after updating")
      else:
        unchanged = action == "pi_update" and "CLUSTER_PI_UNCHANGED" in output
        job.update(state="succeeded", unchanged=unchanged,
                   message="Pi already up to date" if unchanged else "Pi reboot scheduled" if action == "pi_update" else "Cluster log upload completed")
    except TimeoutError:
      job.update(state="failed", message="Operation timed out")
    except asyncio.CancelledError:
      job.update(state="failed", message="Operation interrupted")
      raise
    except Exception:
      LOG.exception("[CLUSTER_TOUCH_ACTION] action=%s job=%s", action, job["job_id"])
      job.update(state="failed", message="Operation could not be started")
    finally:
      if action == "commit_check" and job["state"] != "succeeded":
        self.checks[job["owner"]].update(update_available=False, commit_state="failed")
      self.active = None
      LOG.info("[CLUSTER_TOUCH_ACTION] action=%s job=%s state=%s", action, job["job_id"], job["state"])
