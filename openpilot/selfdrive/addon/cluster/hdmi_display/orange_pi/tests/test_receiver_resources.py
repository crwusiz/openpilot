from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.receiver_resources import (
  MAX_SENSOR_ENTRIES,
  MAX_STATUS_BYTES,
  ReceiverResourceSampler,
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
                                    "cpu_freq_min_mhz": 1200.0, "cpu_freq_max_mhz": 1800.0}) == (
    "cpu_pct=250.0 | rss_mb=64.0 | threads=17 | cpu_temp_max_c=61.2 | fan_pwm_pct=60 | "
    + "cpu_freq_min_mhz=1200.0 | cpu_freq_max_mhz=1800.0 | opencv_threads=n/a")
