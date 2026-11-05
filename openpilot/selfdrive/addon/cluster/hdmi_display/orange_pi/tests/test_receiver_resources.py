import gc
import struct
from types import SimpleNamespace
import weakref

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.receiver_resources import (
  MAX_SENSOR_ENTRIES,
  MAX_STATUS_BYTES,
  MAX_UDP_TRANSPORTS,
  UDP_METRIC_NAMES,
  ReceiverResourceSampler,
  ReceiverUDPSampler,
  format_receiver_resources,
)


def _write(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(str(value), encoding="ascii")


def _sampler(tmp_path, clock=None, cpu_clock=None, modules=None):
  return ReceiverResourceSampler(tmp_path / "proc", tmp_path / "sys", clock=clock or (lambda: 0),
                                 cpu_clock=cpu_clock or (lambda: 0), modules={} if modules is None else modules)


def test_process_cpu_counts_all_native_threads_and_does_not_clamp_to_one_core(tmp_path):
  clocks, cpu_clocks = iter((10, 20, 30)), iter((2, 27, 32))
  sampler = _sampler(tmp_path, clock=lambda: next(clocks), cpu_clock=lambda: next(cpu_clocks))
  _write(sampler.proc / "self/status", "Name:\tpython3\nVmRSS:\t65536 kB\nThreads:\t17\n")
  first = sampler.sample()
  assert first["cpu_pct"] is None
  assert first["rss_mb"] == 64
  assert first["threads"] == 17
  assert sampler.sample()["cpu_pct"] == pytest.approx(250)
  assert sampler.sample()["cpu_pct"] == pytest.approx(50)


def test_cpu_temperature_frequency_and_fan_are_read_from_injected_sysfs(tmp_path):
  sampler = _sampler(tmp_path, modules={"cv2": SimpleNamespace(getNumThreads=lambda: 4)})
  for index, (kind, temperature) in enumerate((("cpub_thermal_zone", 61000), ("cpul_thermal_zone", 58000),
                                             ("gpu_thermal_zone", 82000), ("npu_thermal_zone", 90000),
                                             ("ddr_thermal_zone", 87000), ("soc", 95000))):
    zone = sampler.sysfs / "class/thermal" / f"thermal_zone{index}"
    _write(zone / "type", kind)
    _write(zone / "temp", temperature)
  wrong_fan = sampler.sysfs / "class/hwmon/hwmon0"
  _write(wrong_fan / "name", "some-other-device")
  _write(wrong_fan / "pwm1", 255)
  fan = sampler.sysfs / "class/hwmon/hwmon1"
  _write(fan / "name", "pwmfan")
  _write(fan / "pwm1", 153)
  for index, frequency in enumerate((1800000, 1200000, -1, 0)):
    _write(sampler.sysfs / "devices/system/cpu/cpufreq" / f"policy{index}" / "scaling_cur_freq", frequency)
  result = sampler.sample()
  assert result["cpu_temp_max_c"] == 61
  assert result["fan_pwm_pct"] == 60
  assert result["cpu_freq_min_mhz"] == 1200
  assert result["cpu_freq_max_mhz"] == 1800
  assert result["opencv_threads"] == 4


def test_frequency_can_fall_back_to_per_cpu_files_and_supports_loaded_cv2_only(tmp_path):
  modules = {}
  sampler = _sampler(tmp_path, modules=modules)
  _write(sampler.sysfs / "devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", 1416000)
  _write(sampler.sysfs / "devices/system/cpu/cpu4/cpufreq/scaling_cur_freq", 2016000)
  assert sampler.sample()["opencv_threads"] is None
  modules["cv2"] = SimpleNamespace(getNumThreads=lambda: 1)
  result = sampler.sample()
  assert result["opencv_threads"] == 1
  assert result["cpu_freq_min_mhz"] == 1416
  assert result["cpu_freq_max_mhz"] == 2016


def test_missing_invalid_and_oversize_inputs_are_unknown(tmp_path):
  sampler = _sampler(tmp_path, modules={"cv2": SimpleNamespace()})
  assert all(value is None for value in sampler.sample().values())
  _write(sampler.proc / "self/status", "VmRSS: invalid kB\nThreads: -2\n")
  zone = sampler.sysfs / "class/thermal/thermal_zone0"
  _write(zone / "type", "cpu")
  _write(zone / "temp", 999999)
  fan = sampler.sysfs / "class/hwmon/hwmon1"
  _write(fan / "name", "pwm-fan")
  _write(fan / "pwm1", "1" * 200)
  _write(sampler.sysfs / "devices/system/cpu/cpufreq/policy0/scaling_cur_freq", "invalid")
  result = sampler.sample()
  assert result["rss_mb"] is None
  assert result["threads"] is None
  assert result["cpu_temp_max_c"] is None
  assert result["fan_pwm_pct"] is None
  assert result["cpu_freq_min_mhz"] is None
  _write(sampler.proc / "self/status", "VmRSS: 1024 kB\n" + "a" * MAX_STATUS_BYTES)
  assert sampler.sample()["rss_mb"] is None


@pytest.mark.parametrize("clocks,cpu_clocks", [((0, 0, 10), (0, 1, 6)), ((10, 9, 19), (5, 4, 9)),
                                            ((0, float("nan"), 10, 20), (0, 1, 6, 11))])
def test_invalid_clock_deltas_rebase_without_emitting_bad_cpu_values(tmp_path, clocks, cpu_clocks):
  wall, cpu = iter(clocks), iter(cpu_clocks)
  sampler = _sampler(tmp_path, clock=lambda: next(wall), cpu_clock=lambda: next(cpu))
  assert sampler.sample()["cpu_pct"] is None
  assert sampler.sample()["cpu_pct"] is None
  if len(clocks) == 4:
    assert sampler.sample()["cpu_pct"] is None
  assert sampler.sample()["cpu_pct"] == pytest.approx(50)


def test_sensor_discovery_and_reads_are_bounded(tmp_path, monkeypatch):
  sampler = _sampler(tmp_path)
  for index in range(MAX_SENSOR_ENTRIES + 8):
    zone = sampler.sysfs / "class/thermal" / f"thermal_zone{index}"
    _write(zone / "type", "cpu")
    _write(zone / "temp", 50000)
  opened = []
  original_open = type(sampler.sysfs).open

  def record_open(path, *args, **kwargs):
    opened.append(path)
    return original_open(path, *args, **kwargs)

  monkeypatch.setattr(type(sampler.sysfs), "open", record_open)
  assert sampler.sample()["cpu_temp_max_c"] == 50
  assert len([path for path in opened if path.name == "type"]) == MAX_SENSOR_ENTRIES
  assert len([path for path in opened if path.name == "temp"]) == MAX_SENSOR_ENTRIES


def test_read_failures_and_loaded_opencv_failures_do_not_interrupt_stream(tmp_path, monkeypatch):
  def fail():
    raise RuntimeError("native library unavailable")

  sampler = _sampler(tmp_path, modules={"cv2": SimpleNamespace(getNumThreads=fail)})
  _write(sampler.proc / "self/status", "Threads: 9\n")

  def denied(*args, **kwargs):
    raise PermissionError("proc inaccessible")

  monkeypatch.setattr(type(sampler.proc), "open", denied)
  assert all(value is None for value in sampler.sample().values())


def test_format_includes_stable_units_and_unknowns():
  assert format_receiver_resources({"cpu_pct": 250.04, "rss_mb": 64.0, "threads": 17,
                                    "cpu_temp_max_c": 61.25, "fan_pwm_pct": 60,
                                    "cpu_freq_min_mhz": 1200.0, "cpu_freq_max_mhz": 1800.0,
                                    "system_cpu_pct": 50.0, "cpu_cores_pct": {"cpu0": 90.0, "cpu1": 10.0}}) == (
    "cpu_pct=250.0 | rss_mb=64.0 | threads=17 | cpu_temp_max_c=61.2 | fan_pwm_pct=60 | "
    + "cpu_freq_min_mhz=1200.0 | cpu_freq_max_mhz=1800.0 | opencv_threads=n/a | system_cpu_pct=50.0 | "
    + "cpu_cores_pct=cpu0:90.0,cpu1:10.0")


def test_system_and_per_core_cpu_distinguish_hot_core_and_do_not_double_count_guest(tmp_path):
  sampler = _sampler(tmp_path)
  _write(sampler.proc / "stat", "cpu 100 10 20 700 50 5 5 0 20 0\n"
         + "cpu0 20 0 10 300 10 0 0 0 5 0\ncpu1 80 10 10 400 40 5 5 0 15 0\n"
         + "intr " + "1 " * 100000)
  first = sampler.sample()
  assert first["system_cpu_pct"] is None
  assert first["cpu_cores_pct"] is None
  _write(sampler.proc / "stat", "cpu 180 10 40 780 70 5 5 0 50 0\n"
         + "cpu0 100 0 20 310 10 0 0 0 35 0\ncpu1 80 10 20 470 60 5 5 0 15 0\n")
  result = sampler.sample()
  assert result["system_cpu_pct"] == pytest.approx(50)
  assert result["cpu_cores_pct"] == {"cpu0": 90.0, "cpu1": 10.0}


def test_cpu_counter_reset_hotplug_and_missing_samples_rebase_cleanly(tmp_path):
  sampler = _sampler(tmp_path)
  _write(sampler.proc / "stat", "cpu 100 0 0 100\ncpu0 100 0 0 100\n")
  sampler.sample()
  # CPU0 reset and CPU1 is newly online: neither has a usable interval yet.
  _write(sampler.proc / "stat", "cpu 20 0 0 20\ncpu0 10 0 0 10\ncpu1 10 0 0 10\n")
  result = sampler.sample()
  assert result["system_cpu_pct"] is None
  assert result["cpu_cores_pct"] is None
  _write(sampler.proc / "stat", "cpu 40 0 0 40\ncpu0 30 0 0 10\ncpu1 10 0 0 30\n")
  assert sampler.sample()["cpu_cores_pct"] == {"cpu0": 100.0, "cpu1": 0.0}
  (sampler.proc / "stat").unlink()
  assert sampler.sample()["cpu_cores_pct"] is None
  _write(sampler.proc / "stat", "cpu 60 0 0 60\ncpu0 50 0 0 10\n")
  assert sampler.sample()["cpu_cores_pct"] is None


def test_cpu_stat_read_stops_at_bounded_cpu_prefix_and_rejects_invalid_counters(tmp_path):
  sampler = _sampler(tmp_path)
  _write(sampler.proc / "stat", "cpu 0 0 0 0\n" + "".join(f"cpu{index} 0 0 0 0\n" for index in range(100)))
  sampler.sample()
  _write(sampler.proc / "stat", "cpu 100 0 0 100\n" + "".join(f"cpu{index} 10 0 0 10\n" for index in range(100)))
  result = sampler.sample()
  assert len(result["cpu_cores_pct"]) == MAX_SENSOR_ENTRIES
  _write(sampler.proc / "stat", "cpu -1 0 0 100\ncpu0 invalid 0 0 10\ncpu1 0 0 0 10\n")
  result = sampler.sample()
  assert result["system_cpu_pct"] is None
  assert result["cpu_cores_pct"] is None


class _UDPSocket:
  def __init__(self, drops=0, allocated=0, capacity=2097152):
    self.drops, self.allocated, self.capacity = drops, allocated, capacity
    self.calls = []
    self.failure = None
    self.raw = None

  def getsockopt(self, *args):
    self.calls.append(args)
    if self.failure is not None:
      raise self.failure
    return self.raw if self.raw is not None else struct.pack("=9I", self.allocated, self.capacity, 101, 102, 103, 104, 105, 106,
                                                           self.drops)


class _UDPTransport:
  def __init__(self, sock=None):
    self.sock = sock or _UDPSocket()
    self.closing = False

  def is_closing(self):
    return self.closing

  def get_extra_info(self, key):
    assert key == "socket"
    return self.sock


def _udp_sampler(**kwargs):
  return ReceiverUDPSampler(platform="linux", socket_api=SimpleNamespace(SOL_SOCKET=1, **kwargs))


def test_udp_samples_only_owned_socket_meminfo_and_distinguishes_first_sample_from_zero_delta():
  sampler = _udp_sampler()
  transports = [_UDPTransport(_UDPSocket(0, 120)), _UDPTransport(_UDPSocket(7, 230))]
  for transport in transports:
    assert sampler.register(transport)
  assert sampler.sample() == {"udp_sockets": 2, "udp_drops_total": 7, "udp_drops_delta": None,
                              "udp_rmem_bytes": 350, "udp_rcvbuf_bytes": 4194304}
  assert all(transport.sock.calls == [(1, 55, 36)] for transport in transports)
  assert sampler.sample()["udp_drops_delta"] == 0
  transports[0].sock.drops, transports[1].sock.drops = 2, 10
  assert sampler.sample()["udp_drops_delta"] == 5


def test_udp_zero_drops_from_readable_socket_are_known_but_no_socket_is_unknown():
  sampler = _udp_sampler()
  assert sampler.sample() == {"udp_sockets": 0, "udp_drops_total": None, "udp_drops_delta": None,
                              "udp_rmem_bytes": None, "udp_rcvbuf_bytes": None}
  transport = _UDPTransport()
  sampler.register(transport)
  assert sampler.sample()["udp_drops_total"] == 0


def test_udp_unavailable_sampler_snapshot_has_no_claim_of_zero_owned_sockets():
  result = ReceiverUDPSampler.empty_snapshot()
  assert tuple(result) == UDP_METRIC_NAMES
  assert all(value is None for value in result.values())


@pytest.mark.parametrize("platform,level", [("win32", 65535), ("darwin", 65535), ("linux", 65535)])
def test_udp_abi_fallback_does_not_read_meminfo_on_other_socket_platforms(platform, level):
  sampler = ReceiverUDPSampler(platform=platform, socket_api=SimpleNamespace(SOL_SOCKET=level, SO_MEMINFO=55))
  transport = _UDPTransport()
  sampler.register(transport)
  result = sampler.sample()
  assert result["udp_sockets"] == 1
  assert result["udp_drops_total"] is None
  assert transport.sock.calls == []


def test_udp_uses_exported_meminfo_option_when_available():
  sampler = _udp_sampler(SO_MEMINFO=99)
  transport = _UDPTransport()
  sampler.register(transport)
  assert sampler.sample()["udp_drops_total"] == 0
  assert transport.sock.calls == [(1, 99, 36)]


@pytest.mark.parametrize("failure,raw", [(OSError("unsupported socket option"), None), (ValueError("closed socket"), None),
                                       (RuntimeError("transport closed"), None), (None, b""), (None, b"x" * 32),
                                       (None, b"x" * 40), (None, "not bytes")])
def test_udp_partial_or_unsupported_samples_are_unknown_and_require_a_new_baseline(failure, raw):
  sampler = _udp_sampler()
  first, second = _UDPTransport(_UDPSocket(3)), _UDPTransport(_UDPSocket(5))
  sampler.register(first)
  sampler.register(second)
  sampler.sample()
  second.sock.failure, second.sock.raw = failure, raw
  result = sampler.sample()
  assert result["udp_sockets"] == 2
  assert all(value is None for key, value in result.items() if key != "udp_sockets")
  second.sock.failure, second.sock.raw, second.sock.drops = None, None, 8
  assert sampler.sample()["udp_drops_delta"] is None
  assert sampler.sample()["udp_drops_delta"] == 0


def test_udp_counter_reset_or_wrap_is_unknown_even_when_another_socket_keeps_total_increasing():
  sampler = _udp_sampler()
  first, second = _UDPTransport(_UDPSocket(0xFFFFFFFF)), _UDPTransport(_UDPSocket(10))
  sampler.register(first)
  sampler.register(second)
  sampler.sample()
  first.sock.drops, second.sock.drops = 0, 0xFFFFFFFF
  assert sampler.sample()["udp_drops_delta"] is None
  first.sock.drops = 5
  assert sampler.sample()["udp_drops_delta"] == 5


def test_udp_changed_owned_socket_set_rebases_and_duplicate_registration_does_not():
  sampler = _udp_sampler()
  first = _UDPTransport(_UDPSocket(10))
  sampler.register(first)
  sampler.sample()
  assert sampler.register(first)
  first.sock.drops = 11
  assert sampler.sample()["udp_drops_delta"] == 1
  second = _UDPTransport(_UDPSocket(12))
  sampler.register(second)
  assert sampler.sample()["udp_drops_delta"] is None
  first.closing = True
  assert sampler.sample()["udp_drops_delta"] is None
  assert sampler.sample()["udp_sockets"] == 1
  assert sampler.sample()["udp_drops_delta"] == 0
  previous_socket = second.sock
  second.sock = _UDPSocket(previous_socket.drops)
  assert sampler.sample()["udp_drops_delta"] is None


def test_udp_transport_tracking_releases_dead_and_closing_transports_without_owning_them():
  sampler = _udp_sampler()
  first, second = _UDPTransport(), _UDPTransport()
  first_reference = weakref.ref(first)
  sampler.register(first)
  sampler.register(second)
  sampler.sample()
  del first
  gc.collect()
  assert first_reference() is None
  result = sampler.sample()
  assert result["udp_sockets"] == 1
  assert result["udp_drops_delta"] is None
  second.closing = True
  assert sampler.sample()["udp_sockets"] == 0


def test_udp_transport_setup_borrows_only_the_registered_live_set():
  sampler = _udp_sampler()
  first, second = _UDPTransport(), _UDPTransport()
  sampler.register(first)
  sampler.register(second)
  assert sampler.active_transports() == (first, second)
  second.closing = True
  assert sampler.active_transports() == (first,)
  first_reference = weakref.ref(first)
  del first
  gc.collect()
  assert first_reference() is None and sampler.active_transports() == ()


def test_udp_tracking_cap_preserves_unknown_coverage_instead_of_reporting_a_partial_zero():
  sampler = _udp_sampler()
  transports = [_UDPTransport() for _ in range(MAX_UDP_TRANSPORTS + 1)]
  for transport in transports[:-1]:
    assert sampler.register(transport)
  assert not sampler.register(transports[-1])
  result = sampler.sample()
  assert result["udp_sockets"] == MAX_UDP_TRANSPORTS
  assert result["udp_drops_total"] is None
  transports[0].closing = True
  assert sampler.register(transports[-1])
  assert sampler.sample()["udp_sockets"] == MAX_UDP_TRANSPORTS
  # An untracked interval keeps complete coverage unknown for this sampler.
  assert sampler.sample()["udp_drops_delta"] is None


def test_udp_nonweakrefable_transport_cannot_hold_resources_or_claim_complete_coverage():
  sampler = _udp_sampler()
  assert not sampler.register(SimpleNamespace())
  transport = _UDPTransport()
  assert sampler.register(transport)
  assert sampler.sample()["udp_drops_total"] is None
