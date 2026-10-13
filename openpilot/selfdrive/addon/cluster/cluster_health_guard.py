"""Limit auxiliary display work during input faults; leave driving alerts intact."""

from collections.abc import Mapping
import json
import math
import re


DRIVING_FAULT_ALERTS = frozenset({
  "commIssue", "commIssueAvgFreq", "selfdrivedLagging", "modeldLagging",
  "processNotRunning", "cameraMalfunction", "cameraFrameRate",
})


def parse_comm_issue(record):
  """Keep only selfdrived's structured failing-service lists, never raw logs."""
  if not isinstance(record, str) or len(record) > 16384 or 'commIssue' not in record:
    return None
  try:
    data = json.loads(record)
  except (ValueError, TypeError):
    return None
  if not isinstance(data, dict) or not isinstance(data.get("msg"), dict):
    return None
  msg = data["msg"]
  if msg.get("event") != "commIssue":
    return None
  result = {"event": "commIssue"}
  for key in ("invalid", "not_alive", "not_freq_ok"):
    names = msg.get(key, [])
    if not isinstance(names, list):
      return None
    result[key] = [name for name in names[:32] if isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)]
  for key in ("created", "process"):
    value = data.get(key)
    if isinstance(value, (int, float)) and math.isfinite(value):
      result[key] = value
  return result


class ClusterHealthGuard:
  def __init__(self, fps=5, recovery_s=1.0):
    self.fps = fps
    self.recovery_s = recovery_s
    self.degraded = False
    self._healthy_since = None
    self._reason = ""
    self._next_render_at = 0.0

  @staticmethod
  def _fault_reasons(health):
    if not isinstance(health, Mapping) or health.get("onroad_started") is False:
      return []
    reasons = []
    for seen, age, label in (
      ("model_seen", "model_age_ms", "model_stale"),
      ("device_motion_seen", "device_motion_age_ms", "motion_stale"),
    ):
      value = health.get(age)
      if health.get(seen) is True and isinstance(value, (int, float)) and math.isfinite(value) and value > 500:
        reasons.append(label)
    if health.get("device_motion_seen") is True and health.get("device_motion_inputs_ok") is False:
      reasons.append("motion_inputs_invalid")
    alert = health.get("drive_fault_alert")
    if isinstance(alert, str) and alert.split("/", 1)[0] in DRIVING_FAULT_ALERTS:
      reasons.append(alert.split("/", 1)[0])
    # A brief alert can be raised and cleared between render-loop samples.
    # Its latched diagnostic still gives the source processes a recovery window.
    events = health.get("diagnostic_events", ())
    if isinstance(events, (list, tuple)):
      for event in events:
        if not isinstance(event, Mapping):
          continue
        name = 'commIssue' if event.get('event') == 'commIssue' else str(event.get('alert_type', '')).split('/', 1)[0]
        if name in DRIVING_FAULT_ALERTS and name not in reasons:
          reasons.append(name)
    return reasons

  def update(self, health, now):
    reasons = self._fault_reasons(health)
    if reasons:
      self._healthy_since = None
      reason = ",".join(reasons)
      changed = not self.degraded or reason != self._reason
      self.degraded = True
      self._reason = reason
      if changed:
        return f"[CLUSTER_LOAD_GUARD] active=1 | render_limit={self.fps} | reason={reason} | mono={now:.3f}"
    elif self.degraded:
      if self._healthy_since is None:
        self._healthy_since = now
      if now - self._healthy_since >= self.recovery_s:
        self.degraded = False
        self._reason = ""
        self._next_render_at = now
        return f"[CLUSTER_LOAD_GUARD] active=0 | reason=inputs_recovered | mono={now:.3f}"
    return None

  def allow_render(self, now):
    if not self.degraded:
      return True
    if now < self._next_render_at:
      return False
    self._next_render_at = now + 1.0 / self.fps
    return True
