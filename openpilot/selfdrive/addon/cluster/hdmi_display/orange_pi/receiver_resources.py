"""Bounded, read-only receiver process and owned UDP socket resource samples."""
from itertools import islice
import math
from pathlib import Path
import socket
import struct
import sys
import time
import weakref


MAX_SENSOR_ENTRIES = 32
MAX_STATUS_BYTES = 16384
MAX_SENSOR_BYTES = 128
MAX_CPU_LINE_BYTES = 512
MAX_UDP_TRANSPORTS = 8
UDP_METRIC_NAMES = ("udp_sockets", "udp_drops_total", "udp_drops_delta", "udp_rmem_bytes", "udp_rcvbuf_bytes")
_SOCKET_MEMINFO = struct.Struct("=9I")


class ReceiverUDPSampler:
  """Sample only registered ICE sockets; never receive packets or enumerate process sockets.

  Totals cover the current socket lifetimes. A delta is valid only when every
  socket was readable in consecutive samples and the owned socket set is unchanged.
  """
  def __init__(self, platform=sys.platform, socket_api=socket):
    self._level = socket_api.SOL_SOCKET
    self._option = getattr(socket_api, "SO_MEMINFO", 55) if platform.startswith("linux") and self._level == 1 else None
    self._transports = []
    self._next_token = 0
    self._previous = None
    self._complete = True

  @staticmethod
  def empty_snapshot(socket_count=None):
    return dict.fromkeys(UDP_METRIC_NAMES) | {"udp_sockets": socket_count}

  def _active(self):
    active = []
    for token, reference in self._transports:
      transport = reference()
      try:
        if transport is not None and not transport.is_closing():
          active.append((token, reference))
      except (AttributeError, OSError, RuntimeError):
        pass
    self._transports = active
    return active

  def register(self, transport):
    active = self._active()
    if any(reference() is transport for _, reference in active):
      return True
    if len(active) >= MAX_UDP_TRANSPORTS:
      self._complete = False  # Untracked sockets make aggregate coverage unknown.
      return False
    try:
      reference = weakref.ref(transport)
    except TypeError:
      self._complete = False
      return False
    self._next_token += 1
    self._transports.append((self._next_token, reference))
    return True

  def sample(self):
    active = self._active()
    result = self.empty_snapshot(len(active))
    current, allocated, capacity = {}, 0, 0
    complete = self._complete and self._option is not None and bool(active)
    for token, reference in active if complete else ():
      transport = reference()
      try:
        sock = transport.get_extra_info("socket")
        # Linux SO_MEMINFO returns the native-endian u32 SK_MEMINFO_* array.
        values = _SOCKET_MEMINFO.unpack(sock.getsockopt(self._level, self._option, _SOCKET_MEMINFO.size))
        current[(token, id(sock))] = values[8]
        allocated += values[0]
        capacity += values[1]
      except (AttributeError, OSError, RuntimeError, TypeError, ValueError, struct.error):
        complete = False
        break
    if complete:
      result.update(udp_drops_total=sum(current.values()), udp_rmem_bytes=allocated, udp_rcvbuf_bytes=capacity)
      if self._previous is not None and current.keys() == self._previous.keys():
        differences = [value - self._previous[key] for key, value in current.items()]
        if all(value >= 0 for value in differences):
          result["udp_drops_delta"] = sum(differences)
    self._previous = current if complete else None
    return result


def _read(path, limit=MAX_SENSOR_BYTES):
  try:
    with path.open("rb") as source:
      value = source.read(limit + 1)
    return value.decode("ascii").strip() if len(value) <= limit else None
  except (OSError, UnicodeError):
    return None


def _integer(path):
  try:
    return int(_read(path))
  except (TypeError, ValueError):
    return None


def _paths(root, pattern):
  try:
    return list(islice(root.glob(pattern), MAX_SENSOR_ENTRIES))
  except OSError:
    return []


def _cpu_times(proc):
  """Read only the bounded CPU prefix; /proc/stat's IRQ tail can be very large."""
  counters = {}
  try:
    with (proc / "stat").open("rb") as source:
      for _ in range(MAX_SENSOR_ENTRIES + 1):
        line = source.readline(MAX_CPU_LINE_BYTES + 1)
        if len(line) > MAX_CPU_LINE_BYTES:
          break
        fields = line.decode("ascii").split()
        if not fields or not (fields[0] == "cpu" or fields[0].startswith("cpu") and fields[0][3:].isdigit()):
          break
        try:
          # guest/guest_nice (fields 9/10) are already included in user/nice.
          values = [int(value) for value in fields[1:9]]
          if len(values) >= 4 and all(value >= 0 for value in values):
            counters[fields[0]] = sum(values), values[3] + (values[4] if len(values) > 4 else 0)
        except ValueError:
          continue
  except (OSError, UnicodeError):
    pass
  return counters


def _cpu_percent(current, previous):
  if previous is None:
    return None
  total, idle = current[0] - previous[0], current[1] - previous[1]
  if total <= 0 or not 0 <= idle <= total:
    return None
  return 100 * (total - idle) / total


class ReceiverResourceSampler:
  """CPU percent includes native threads; 100% represents one fully used core."""
  def __init__(self, proc=Path("/proc"), sysfs=Path("/sys"), clock=time.monotonic, cpu_clock=time.process_time, modules=None):
    self.proc, self.sysfs = Path(proc), Path(sysfs)
    self.clock, self.cpu_clock = clock, cpu_clock
    self.modules = sys.modules if modules is None else modules
    self.previous = None
    self.previous_cpu_times = {}

  def sample(self):
    now, cpu_now = self.clock(), self.cpu_clock()
    cpu_pct = None
    if self.previous is not None:
      elapsed, cpu_elapsed = now - self.previous[0], cpu_now - self.previous[1]
      if elapsed > 0 and cpu_elapsed >= 0 and math.isfinite(elapsed) and math.isfinite(cpu_elapsed):
        cpu_pct = 100 * cpu_elapsed / elapsed
    self.previous = (now, cpu_now) if math.isfinite(now) and math.isfinite(cpu_now) else None

    cpu_times = _cpu_times(self.proc)
    cpu_usage = {name: _cpu_percent(counters, self.previous_cpu_times.get(name)) for name, counters in cpu_times.items()}
    self.previous_cpu_times = cpu_times
    core_usage = {name: value for name, value in cpu_usage.items() if name != "cpu" and value is not None}

    rss_mb, threads = None, None
    for line in (_read(self.proc / "self/status", MAX_STATUS_BYTES) or "").splitlines():
      fields = line.split()
      if len(fields) < 2:
        continue
      try:
        if fields[0] == "VmRSS:" and len(fields) == 3 and fields[2] == "kB" and int(fields[1]) >= 0:
          rss_mb = int(fields[1]) / 1024
        elif fields[0] == "Threads:" and int(fields[1]) > 0:
          threads = int(fields[1])
      except ValueError:
        continue

    temperatures = []
    for zone in _paths(self.sysfs / "class/thermal", "thermal_zone*"):
      kind = (_read(zone / "type") or "").lower()
      # DDR/NPU/GPU/PMIC temperatures must not masquerade as CPU temperature.
      if "cpu" not in kind and kind != "x86_pkg_temp":
        continue
      temperature = _integer(zone / "temp")
      if temperature is not None and -40000 <= temperature <= 150000:
        temperatures.append(temperature / 1000)

    fan_pwm_pct = None
    for hwmon in _paths(self.sysfs / "class/hwmon", "hwmon*"):
      if _read(hwmon / "name") not in ("pwmfan", "pwm-fan"):
        continue
      pwm = _integer(hwmon / "pwm1")
      if pwm is not None and 0 <= pwm <= 255:
        fan_pwm_pct = round(pwm * 100 / 255)
        break

    frequencies = []
    policies = _paths(self.sysfs / "devices/system/cpu/cpufreq", "policy*")
    if not policies:
      policies = [cpu / "cpufreq" for cpu in _paths(self.sysfs / "devices/system/cpu", "cpu[0-9]*")]
    for policy in policies:
      frequency = _integer(policy / "scaling_cur_freq")
      if frequency is not None and 0 < frequency <= 10000000:
        frequencies.append(frequency / 1000)

    opencv_threads = None
    cv2 = self.modules.get("cv2")
    if cv2 is not None:
      try:
        count = cv2.getNumThreads()
        if isinstance(count, int) and count >= 0:
          opencv_threads = count
      except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    return {
      "cpu_pct": cpu_pct,
      "rss_mb": rss_mb,
      "threads": threads,
      "cpu_temp_max_c": max(temperatures, default=None),
      "fan_pwm_pct": fan_pwm_pct,
      "cpu_freq_min_mhz": min(frequencies, default=None),
      "cpu_freq_max_mhz": max(frequencies, default=None),
      "opencv_threads": opencv_threads,
      "system_cpu_pct": cpu_usage.get("cpu"),
      "cpu_cores_pct": core_usage or None,
    }


def format_receiver_resources(snapshot):
  fields = []
  for key in ("cpu_pct", "rss_mb", "threads", "cpu_temp_max_c", "fan_pwm_pct",
              "cpu_freq_min_mhz", "cpu_freq_max_mhz", "opencv_threads", "system_cpu_pct", "cpu_cores_pct"):
    value = snapshot.get(key)
    if key == "cpu_cores_pct" and value is not None:
      formatted = ",".join(f"{name}:{percent:.1f}" for name, percent in value.items())
    else:
      formatted = "n/a" if value is None else f"{value:.1f}" if isinstance(value, float) else str(value)
    fields.append(f"{key}={formatted}")
  return " | ".join(fields)
