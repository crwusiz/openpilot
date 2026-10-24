"""Small, dependency-free Pi samples and C4 jobs, used off SDL's owner thread."""
import http.client
import ipaddress
import json
from itertools import islice
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

try:
  from .receiver_build import ReceiverVersion
except ImportError:
  from receiver_build import ReceiverVersion

ACTION_PATH = "/api/cluster/actions"
DASHBOARD_PORT = 7000
MAX_SENSOR_BYTES = 4096
GPU_PERMISSION_RETRY_SECONDS = 60
GPU_RULE_MARKER = "# Managed by cluster receiver GPU telemetry.\n"
LOG = logging.getLogger("cluster_receiver.touch")


def _integer(path):
  try:
    return int(path.read_text(encoding="ascii").strip())
  except (OSError, UnicodeError, ValueError):
    return None


def _read_sensor(path):
  try:
    with path.open("rb") as source:
      value = source.read(MAX_SENSOR_BYTES + 1)
    return value.decode("ascii").strip() if len(value) <= MAX_SENSOR_BYTES else None
  except (OSError, UnicodeError):
    return None


def _privileged_gpu_read():
  try:
    result = subprocess.run(["/usr/bin/sudo", "-n", "--", "/usr/bin/head", "-c", str(MAX_SENSOR_BYTES + 1), "--",
                             "/sys/kernel/debug/pvr/status"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=0.5, check=False)
    return result.stdout if result.returncode == 0 else None
  except (OSError, subprocess.SubprocessError):
    return None


def _gpu_usage(sysfs, privileged_read=None):
  # Only GPU devfreq devices qualify: a DDR/NPU load is not GPU utilisation.
  try:
    devices = list(islice((sysfs / "class/devfreq").glob("*"), 32))
  except OSError:
    devices = []
  for device in devices:
    try:
      name = str(device.resolve().relative_to(sysfs.resolve())).lower()
    except ValueError:
      name = device.name.lower()
    except OSError:
      continue
    if not any(token in name for token in ("gpu", "mali", "pvr", "g3d")):
      continue
    for filename in ("load", "utilisation", "utilization", "gpu_load"):
      text = _read_sensor(device / filename)
      match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%?(?:\s*@\s*\d+\s*Hz)?", text or "")
      if match and 0 <= (value := float(match[1])) <= 100:
        return value

  # The A733's PowerVR driver publishes the whole GPU figure in debugfs.
  # Do not mistake a 2D/3D engine percentage or a clock value for GPU usage.
  status = sysfs / "kernel/debug/pvr/status"
  try:
    with status.open("rb") as source:
      raw = source.read(MAX_SENSOR_BYTES + 1)
  except PermissionError:
    raw = b""
    if status == Path("/sys/kernel/debug/pvr/status"):
      # Pi Update permits this exact read command only. Never prompt or change
      # debugfs permissions while sampling on the existing touch worker.
      raw = (privileged_read or _privileged_gpu_read)() or b""
  except OSError:
    raw = b""
  if len(raw) > MAX_SENSOR_BYTES:
    return None
  try:
    values = re.findall(r"^\s*GPU Utili[sz]ation\s*:\s*(\d+(?:\.\d+)?)\s*%\s*$",
                        raw.decode("ascii"), re.MULTILINE | re.IGNORECASE)
    return max((float(value) for value in values if 0 <= float(value) <= 100), default=None)
  except UnicodeError:
    return None


class PiStatus:
  def __init__(self, proc=Path("/proc"), sysfs=Path("/sys"), clock=time.monotonic):
    self.proc, self.sysfs = Path(proc), Path(sysfs)
    self.clock = clock
    self.previous_cpu = None
    self.sampled_at = None
    self._gpu_retry_at = 0.0

  def _privileged_gpu_status(self):
    now = self.clock()
    if now < self._gpu_retry_at:
      return None
    value = _privileged_gpu_read()
    if value is None:
      self._gpu_retry_at = now + GPU_PERMISSION_RETRY_SECONDS
      LOG.warning("GPU load read unavailable; retrying in %ds. Pi Update installs the scoped GPU reader permission.",
                  GPU_PERMISSION_RETRY_SECONDS)
    return value

  def sample(self):
    cpu = None
    now = self.clock()
    if self.sampled_at is not None and now - self.sampled_at > 3.0:
      self.previous_cpu = None
    self.sampled_at = now
    try:
      fields = (self.proc / "stat").read_text(encoding="ascii").splitlines()[0].split()
      if fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("Invalid CPU counters")
      # guest/guest_nice are already included in user/nice. iowait is idle.
      values = [int(value) for value in fields[1:9]]
      total, idle = sum(values), values[3] + (values[4] if len(values) > 4 else 0)
      if self.previous_cpu is not None:
        delta, idle_delta = total - self.previous_cpu[0], idle - self.previous_cpu[1]
        if delta > 0 and 0 <= idle_delta <= delta:
          cpu = 100.0 * (delta - idle_delta) / delta
      self.previous_cpu = total, idle
    except (OSError, UnicodeError, ValueError, IndexError):
      self.previous_cpu = None

    temperatures, gpu_temperatures = [], []
    for zone in (self.sysfs / "class/thermal").glob("thermal_zone*"):
      temperature = _integer(zone / "temp")
      if temperature is None or not -40000 <= temperature <= 150000:
        continue
      try:
        kind = (zone / "type").read_text(encoding="ascii").lower()
        if "gpu" in kind:
          gpu_temperatures.append(temperature / 1000)
        elif "cpu" in kind or "soc" in kind:
          temperatures.append(temperature / 1000)
      except (OSError, UnicodeError):
        pass

    pwm, rpm = None, None
    for hwmon in (self.sysfs / "class/hwmon").glob("hwmon*"):
      try:
        if (hwmon / "name").read_text(encoding="ascii").strip() != "pwmfan":
          continue
      except (OSError, UnicodeError):
        continue
      value = _integer(hwmon / "pwm1")
      if value is not None and 0 <= value <= 255:
        pwm = round(value * 100 / 255)
      value = _integer(hwmon / "fan1_input")
      if value is not None and 0 <= value <= 100000:
        rpm = value
      break
    return {"cpu": cpu, "temperature": max(temperatures, default=None), "gpu": _gpu_usage(self.sysfs, self._privileged_gpu_status),
            "gpu_temperature": max(gpu_temperatures, default=None), "fan_pwm": pwm, "fan_rpm": rpm}


class ActionHTTPError(RuntimeError):
  def __init__(self, status):
    self.status = status
    super().__init__(f"C4 maintenance HTTP {status}")


def request_action(host, payload=None):
  host = str(ipaddress.IPv4Address(host))
  connection = http.client.HTTPConnection(host, DASHBOARD_PORT, timeout=1.0)
  try:
    body = None if payload is None else json.dumps(payload).encode("ascii")
    connection.request("GET" if body is None else "POST", ACTION_PATH, body=body,
                       headers={"Content-Type": "application/json", "Connection": "close"})
    response = connection.getresponse()
    if response.status not in (200, 202):
      raise ActionHTTPError(response.status)
    data = response.read(4097)
    if len(data) > 4096:
      raise ValueError("Oversized C4 action response")
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("state") not in ("idle", "running", "succeeded", "warning", "failed"):
      raise ValueError("Invalid C4 action response")
    if result["state"] != "idle" and (result.get("action") not in ("pi_update", "upload_logs", "commit_check", "git_pull")
                                       or not isinstance(result.get("request_id"), str)):
      raise ValueError("Invalid C4 action identity")
    if payload is not None and (result.get("request_id") != payload["request_id"] or result.get("action") != payload["action"]):
      raise ValueError("C4 action reply does not match the request")
    return result
  finally:
    connection.close()


class TouchWorker:
  def __init__(self, sampler=None, request=request_action, clock=time.monotonic, versions=None, check_interval=60.0):
    self.sampler = sampler or PiStatus()
    self.request, self.clock = request, clock
    self.host = None
    self.pending = None
    self.pending_at = None
    self.job = None
    self.versions = versions or ReceiverVersion()
    self.check_interval = check_interval
    self._next_check = 0.0

  def _version_status(self, job, version):
    result = {**(job or {}), **version}
    current, desired = result.get("pi_package_sha256"), result.get("c4_pi_sha256")
    known = all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in (current, desired))
    result["pi_update_available"] = known and current != desired
    result["pi_version_state"] = "available" if result["pi_update_available"] else "current" if known else "unknown"
    return result

  def exchange(self, host, action=None):
    """One serialized worker call; no pygame objects cross this boundary."""
    stats = self.sampler.sample()
    version = self.versions.sample()
    if host != self.host:
      self.host, self.pending, self.job = host, None, None
      self._next_check = 0.0
    if action is not None:
      self.pending = {"action": action, "request_id": uuid.uuid4().hex}
      self.pending_at = self.clock()
      self.job = {**self.pending, "state": "checking"}
    if host is None:
      return stats, self._version_status({"state": "unavailable"}, version)
    try:
      job = self.request(host, self.pending)
      # The single Git Pull button reflects an automatic, periodically refreshed
      # commit check. Poll existing jobs before starting a new background check.
      if (self.pending is None and action is None and job["state"] != "running"
          and self.check_interval is not None and self.clock() >= self._next_check):
        self.pending = {"action": "commit_check", "request_id": uuid.uuid4().hex}
        self.pending_at = self.clock()
        self._next_check = self.clock() + self.check_interval
        self.job = {**job, **self.pending, "state": "checking", "commit_state": "checking", "update_available": False}
        job = self.request(host, self.pending)
      self.pending = None
      if job["state"] == "idle" and self.job is not None and self.job.get("state") in ("running", "checking"):
        self.job = {**self.job, "state": "failed", "reason": "interrupted"}
      elif not (self.job is not None and self.job.get("state") == "failed" and self.job.get("reason")
                and job.get("request_id") != self.job.get("request_id")):
        self.job = job
    except ActionHTTPError as error:
      if self.pending is not None:
        self.job = {**self.pending, "state": "failed",
                    "reason": {403: "not_connected", 404: "update_c4", 409: "busy"}.get(error.status, "connection")}
        self.pending = None
      elif self.job is None:
        self.job = {"state": "unavailable", "reason": "update_c4" if error.status == 404 else "connection"}
    except (OSError, http.client.HTTPException, ValueError):
      if self.pending is not None:
        # Retry the SAME ID: the first POST may have started a job before its
        # reply was lost. Never turn a transport timeout into a second update.
        expired = self.clock() - self.pending_at >= 30.0
        self.job = {**self.pending, "state": "failed" if expired else "checking",
                    "reason": "connection"}
        if expired:
          self.pending = None
      elif self.job is None:
        self.job = {"state": "unavailable", "reason": "connection"}
      # Keep a previously accepted running job busy until it can be queried.
    return stats, self._version_status(self.job, version)


def install_gpu_access(account=None, directory=Path("/etc/sudoers.d"), run=subprocess.run):
  """Root-only deployment step; allow one bounded read, not arbitrary commands."""
  if not hasattr(os, "geteuid") or os.geteuid() != 0:
    raise PermissionError("GPU reader setup requires root")
  if account is None:
    result = run(["/usr/bin/systemctl", "show", "--property=User", "--value", "cluster-hdmi.service"],
                 capture_output=True, text=True, timeout=5, check=True)
    account = result.stdout.strip()
  if not account or account == "root":
    return
  if re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", account) is None:
    raise ValueError("Invalid receiver service account")
  directory = Path(directory)
  if directory.is_symlink() or not directory.is_dir():
    raise OSError("GPU reader sudoers directory is unavailable or unsafe")
  info = directory.stat()
  if os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o022):
    raise PermissionError("GPU reader sudoers directory must be root-owned and protected")
  target = directory / "cluster-gpu-status"
  if target.is_symlink() or target.exists() and not target.is_file():
    raise OSError("GPU reader rule path is unsafe")
  if target.exists() and not target.read_text(encoding="ascii").startswith(GPU_RULE_MARKER):
    raise OSError("GPU reader rule is not managed by cluster; keeping the existing file")
  rule = (GPU_RULE_MARKER + f"{account} ALL=(root) NOPASSWD: "
          + "/usr/bin/head -c 4097 -- /sys/kernel/debug/pvr/status\n")
  temporary = None
  try:
    # Dotted files are ignored by sudo's includedir. Validate before publishing
    # an atomic, root-owned 0440 rule in the same protected directory.
    with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="\n", prefix=".cluster-gpu-",
                                     dir=directory, delete=False) as stream:
      temporary = Path(stream.name)
      stream.write(rule)
    if os.name == "posix":
      os.chown(temporary, 0, 0)
      os.chmod(temporary, 0o440)
    result = run(["/usr/sbin/visudo", "-c", "-f", str(temporary)], capture_output=True, text=True, timeout=5, check=False)
    if result.returncode != 0:
      raise RuntimeError("visudo rejected the GPU reader rule")
    os.replace(temporary, target)
    print(f"[CLUSTER_GPU_ACCESS] Installed a bounded, read-only GPU statistics rule for {account}.")
  finally:
    if temporary is not None and temporary.exists():
      temporary.unlink()


if __name__ == "__main__":
  args = sys.argv[1:]
  if not args or args[0] != "--install-gpu-access" or len(args) > 2:
    raise SystemExit("Usage: sudo python3 touch_status.py --install-gpu-access [service-account]")
  try:
    install_gpu_access(args[1] if len(args) == 2 else None)
  except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
    print(f"[CLUSTER_GPU_ACCESS] GPU usage will be unavailable: {error}", file=sys.stderr)
    raise SystemExit(1) from None
