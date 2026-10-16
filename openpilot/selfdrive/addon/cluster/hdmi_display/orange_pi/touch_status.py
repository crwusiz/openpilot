"""Small, dependency-free Pi samples and C4 jobs, used off SDL's owner thread."""
import http.client
import ipaddress
import json
from pathlib import Path
import time
import uuid


ACTION_PATH = "/api/cluster/actions"
DASHBOARD_PORT = 7000


def _integer(path):
  try:
    return int(path.read_text(encoding="ascii").strip())
  except (OSError, UnicodeError, ValueError):
    return None


class PiStatus:
  def __init__(self, proc=Path("/proc"), sysfs=Path("/sys"), clock=time.monotonic):
    self.proc, self.sysfs = Path(proc), Path(sysfs)
    self.clock = clock
    self.previous_cpu = None
    self.sampled_at = None

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

    temperatures = []
    for zone in (self.sysfs / "class/thermal").glob("thermal_zone*"):
      temperature = _integer(zone / "temp")
      if temperature is None or not -40000 <= temperature <= 150000:
        continue
      try:
        kind = (zone / "type").read_text(encoding="ascii").lower()
        if "cpu" in kind or "soc" in kind:
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
    return {"cpu": cpu, "temperature": max(temperatures, default=None), "fan_pwm": pwm, "fan_rpm": rpm}


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
    if result["state"] != "idle" and (result.get("action") not in ("pi_update", "upload_logs")
                                       or not isinstance(result.get("request_id"), str)):
      raise ValueError("Invalid C4 action identity")
    if payload is not None and (result.get("request_id") != payload["request_id"] or result.get("action") != payload["action"]):
      raise ValueError("C4 action reply does not match the request")
    return result
  finally:
    connection.close()


class TouchWorker:
  def __init__(self, sampler=None, request=request_action, clock=time.monotonic):
    self.sampler = sampler or PiStatus()
    self.request, self.clock = request, clock
    self.host = None
    self.pending = None
    self.pending_at = None
    self.job = None

  def exchange(self, host, action=None):
    """One serialized worker call; no pygame objects cross this boundary."""
    stats = self.sampler.sample()
    if host != self.host:
      self.host, self.pending, self.job = host, None, None
    if action is not None:
      self.pending = {"action": action, "request_id": uuid.uuid4().hex}
      self.pending_at = self.clock()
      self.job = {**self.pending, "state": "checking"}
    if host is None:
      return stats, {"state": "unavailable"}
    try:
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
    return stats, self.job
