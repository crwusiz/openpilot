from collections.abc import Mapping
import math
from pathlib import Path
import threading
import time


def read_process_resources(status_path="/proc/self/status"):
  """Read current Linux RSS/native threads; unavailable fields stay unknown."""
  rss_mb = threads = None
  try:
    for line in Path(status_path).read_text(encoding="ascii").splitlines():
      fields = line.split()
      if len(fields) >= 2 and fields[0] == "VmRSS:" and len(fields) >= 3 and fields[2] == "kB":
        rss_mb = int(fields[1]) / 1024
      elif len(fields) >= 2 and fields[0] == "Threads:":
        threads = int(fields[1])
  except (OSError, UnicodeError, ValueError):
    pass
  return rss_mb, threads


def _number(value):
  if isinstance(value, (int, float)) and math.isfinite(value):
    return float(value)
  return None


def _format_number(value):
  number = _number(value)
  return f"{number:.1f}" if number is not None else "n/a"


def _format_bool(value):
  return str(int(value)) if isinstance(value, bool) else "n/a"


class ClusterRuntimeMetrics:
  """Sample health cheaply on the render thread and report every ten seconds.

  Process CPU is the change in CPU seconds divided by wall time: 100% means
  one saturated core, and a multithreaded process can exceed 100%. This records
  correlations with model/locationd inputs; it does not infer causation.
  """

  _COUNTERS = {
    "model_lagging_updates": "model_lagging_update_total",
    "locationd_input_error_updates": "device_motion_input_error_total",
    "posenet_error_updates": "device_motion_posenet_error_total",
  }
  _PEAKS = {
    "model_drop_max": ("model_drop_perc", "model_drop_peak_perc"),
    "model_exec_max_ms": ("model_exec_ms",),
    "device_cpu_max_pct": ("device_cpu_max_pct",),
    "cpu_temp_max_c": ("cpu_temp_max_c",),
    "device_memory_max_pct": ("device_memory_pct",),
  }

  def __init__(self, interval=10.0, clock=time.monotonic, cpu_clock=time.process_time, resources=read_process_resources):
    self.interval = max(float(interval), 0.1)
    self._clock = clock
    self._cpu_clock = cpu_clock
    self._resources = resources
    self._started = clock()
    self._cpu_started = cpu_clock()
    self._latest = {}
    self._peaks = dict.fromkeys(self._PEAKS)
    self._last_counts = dict.fromkeys(self._COUNTERS, 0)

  def sample(self, health):
    if isinstance(health, Mapping):
      self._latest = dict(health)
      for peak, fields in self._PEAKS.items():
        values = [_number(health.get(field)) for field in fields]
        values.append(self._peaks[peak])
        self._peaks[peak] = max((value for value in values if value is not None), default=None)
    now = self._clock()
    elapsed = now - self._started
    if elapsed < self.interval:
      return None

    cpu_now = self._cpu_clock()
    cpu_pct = max(0.0, (cpu_now - self._cpu_started) * 100 / max(elapsed, 1e-6))
    rss_mb, threads = self._resources()
    counts = {}
    for label, field in self._COUNTERS.items():
      total = int(_number(self._latest.get(field)) or 0)
      previous = self._last_counts[label]
      counts[label] = total - previous if total >= previous else total
      self._last_counts[label] = total
    detail = [
      f"cpu_pct={cpu_pct:.1f}", f"rss_mb={_format_number(rss_mb)}",
      f"threads={threads if threads is not None else 'n/a'}", f"python_threads={threading.active_count()}",
      f"onroad_started={_format_bool(self._latest.get('onroad_started'))}",
      f"car_state_seen={_format_bool(self._latest.get('car_state_seen'))}",
      f"car_state_age_ms={_format_number(self._latest.get('car_state_age_ms'))}",
      f"model_seen={_format_bool(self._latest.get('model_seen'))}",
      f"model_age_ms={_format_number(self._latest.get('model_age_ms'))}",
      f"model_drop_perc={_format_number(self._latest.get('model_drop_perc'))}",
      f"model_exec_ms={_format_number(self._latest.get('model_exec_ms'))}",
      f"device_motion_seen={_format_bool(self._latest.get('device_motion_seen'))}",
      f"device_motion_age_ms={_format_number(self._latest.get('device_motion_age_ms'))}",
      f"inputs_ok={_format_bool(self._latest.get('device_motion_inputs_ok'))}",
      f"posenet_ok={_format_bool(self._latest.get('device_motion_posenet_ok'))}",
      f"thermal_status={self._latest.get('thermal_status') or 'n/a'}",
    ]
    detail += [f"{label}={_format_number(value)}" for label, value in self._peaks.items()]
    detail += [f"{label}={count}" for label, count in counts.items()]
    self._started = now
    self._cpu_started = cpu_now
    self._peaks = dict.fromkeys(self._PEAKS)
    return "[CLUSTER_RESOURCE_PERF] " + " | ".join(detail)
