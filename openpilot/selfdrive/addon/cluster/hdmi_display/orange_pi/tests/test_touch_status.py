from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace
import subprocess
import os

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.touch_status import ActionHTTPError, PiStatus, TouchWorker
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import touch_status


def _write(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(str(value), encoding="ascii")


def test_cpu_usage_uses_counter_deltas_and_temperature_uses_the_hottest_cpu(tmp_path):
  proc, sysfs = tmp_path / "proc", tmp_path / "sys"
  _write(proc / "stat", "cpu 100 0 0 100 20 0 0 0 50 0\n")
  for index, (kind, temperature) in enumerate((("cpul_thermal_zone", 52000), ("cpub_thermal_zone", 61000), ("pmic", 82000))):
    zone = sysfs / "class/thermal" / f"thermal_zone{index}"
    _write(zone / "type", kind)
    _write(zone / "temp", temperature)
  fan = sysfs / "class/hwmon/hwmon3"
  _write(fan / "name", "pwmfan")
  _write(fan / "pwm1", 153)
  sampler = PiStatus(proc, sysfs)
  first = sampler.sample()
  assert first == {"cpu": None, "temperature": 61.0, "gpu": None, "gpu_temperature": None, "fan_pwm": 60, "fan_rpm": None}
  # The extra guest counter must not be counted a second time.
  _write(proc / "stat", "cpu 120 0 0 180 20 0 0 0 70 0\n")
  _write(fan / "fan1_input", 3200)
  second = sampler.sample()
  assert second["cpu"] == pytest.approx(20.0)
  assert second["fan_rpm"] == 3200
  _write(proc / "stat", "cpu 0 0 0 0\n")
  assert sampler.sample()["cpu"] is None


def test_missing_or_invalid_sensors_are_unknown_rather_than_zero(tmp_path):
  sampler = PiStatus(tmp_path / "proc", tmp_path / "sys")
  assert all(value is None for value in sampler.sample().values())
  zone = sampler.sysfs / "class/thermal/thermal_zone0"
  _write(zone / "type", "cpu")
  _write(zone / "temp", 999999)
  assert sampler.sample()["temperature"] is None


@pytest.mark.parametrize("usage", [0, 28.5, 100])
def test_gpu_uses_powervr_total_load_and_dedicated_temperature(tmp_path, usage):
  sampler = PiStatus(tmp_path / "proc", tmp_path / "sys")
  _write(sampler.sysfs / "kernel/debug/pvr/status", f"Driver Status: OK\nGPU Utilisation: {usage}%\n3D Utilisation: 98%\n")
  for index, (kind, temp) in enumerate((("cpub_thermal_zone", 61000), ("gpu_thermal_zone", 64500),
                                       ("gpu_soc", 62000), ("npu_thermal_zone", 90000), ("ddr_thermal_zone", 80000))):
    zone = sampler.sysfs / f"class/thermal/thermal_zone{index}"
    _write(zone / "type", kind)
    _write(zone / "temp", temp)
  stats = sampler.sample()
  assert stats["gpu"] == usage
  assert stats["gpu_temperature"] == 64.5
  assert stats["temperature"] == 61


@pytest.mark.parametrize("text", ["Driver Status: OK\n3D Utilisation: 50%\n", "GPU Utilisation: -1%", "GPU Utilisation: 101%",
                                 "GPU Utilisation: nan%", "GPU Utilisation: 30%\n" + "a" * 4097])
def test_invalid_or_unavailable_gpu_load_never_becomes_zero_or_cpu_usage(tmp_path, text):
  sampler = PiStatus(tmp_path / "proc", tmp_path / "sys")
  _write(sampler.sysfs / "kernel/debug/pvr/status", text)
  assert sampler.sample()["gpu"] is None
  assert sampler.sample()["gpu_temperature"] is None


@pytest.mark.parametrize("value", ["43", "43%", "43@600000000Hz"])
def test_gpu_devfreq_load_ignores_other_accelerators_and_does_not_parse_frequency(tmp_path, value):
  sampler = PiStatus(tmp_path / "proc", tmp_path / "sys")
  _write(sampler.sysfs / "class/devfreq/100.ddr/load", "98")
  _write(sampler.sysfs / "class/devfreq/200.npu/load", "99")
  _write(sampler.sysfs / "class/devfreq/1800000.gpu/load", value)
  _write(sampler.sysfs / "class/devfreq/1800000.gpu/cur_freq", "600000000")
  assert sampler.sample()["gpu"] == 43
  _write(sampler.sysfs / "class/devfreq/1800000.gpu/load", "600000000")
  assert sampler.sample()["gpu"] is None


@pytest.mark.parametrize("result", [SimpleNamespace(returncode=0, stdout=b"GPU Utilisation: 34%\n"),
                                   SimpleNamespace(returncode=1, stdout=b""), subprocess.TimeoutExpired("sudo", 0.5)])
def test_powervr_permission_fallback_is_fixed_bounded_and_noninteractive(monkeypatch, result):
  original = Path.open
  def denied_status(path, *args, **kwargs):
    if path == Path("/sys/kernel/debug/pvr/status"):
      raise PermissionError("root-only debugfs")
    return original(path, *args, **kwargs)
  monkeypatch.setattr(Path, "open", denied_status)
  monkeypatch.setattr(Path, "glob", lambda *_args: iter(()))
  run = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
  monkeypatch.setattr(touch_status.subprocess, "run", run)
  assert touch_status._gpu_usage(Path("/sys")) == (34 if getattr(result, "returncode", None) == 0 else None)
  args, kwargs = run.call_args
  assert args[0] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/head", "-c", "4097", "--", "/sys/kernel/debug/pvr/status"]
  assert kwargs["timeout"] == 0.5 and kwargs["stderr"] == subprocess.DEVNULL


def test_gpu_permission_failure_has_a_cooldown_instead_of_a_sudo_attempt_every_second(monkeypatch, tmp_path):
  now = [10.0]
  sampler = PiStatus(tmp_path / "proc", tmp_path / "sys", clock=lambda: now[0])
  read = Mock(side_effect=[None, b"GPU Utilisation: 24%\n"])
  monkeypatch.setattr(touch_status, "_privileged_gpu_read", read)
  assert sampler._privileged_gpu_status() is None
  for second in range(11, 70):
    now[0] = second
    assert sampler._privileged_gpu_status() is None
  assert read.call_count == 1
  now[0] = 70
  assert sampler._privileged_gpu_status() == b"GPU Utilisation: 24%\n"
  assert read.call_count == 2


def test_gpu_access_setup_validates_and_publishes_only_the_exact_read_command(monkeypatch, tmp_path):
  monkeypatch.setattr(touch_status.os, "geteuid", lambda: 0, raising=False)
  calls = []

  def run(args, **kwargs):
    calls.append((args, kwargs))
    if args[0] == "/usr/bin/systemctl":
      return SimpleNamespace(stdout="receiver\n", returncode=0)
    path = Path(args[-1])
    assert path.name.startswith(".cluster-gpu-")
    assert path.read_text().endswith("receiver ALL=(root) NOPASSWD: /usr/bin/head -c 4097 -- /sys/kernel/debug/pvr/status\n")
    assert not (tmp_path / "cluster-gpu-status").exists()
    return SimpleNamespace(returncode=0)

  touch_status.install_gpu_access(directory=tmp_path, run=run)
  target = tmp_path / "cluster-gpu-status"
  text = target.read_text()
  assert text == (touch_status.GPU_RULE_MARKER
                  + "receiver ALL=(root) NOPASSWD: /usr/bin/head -c 4097 -- /sys/kernel/debug/pvr/status\n")
  assert "*" not in text and "NOPASSWD: ALL" not in text
  assert len(calls) == 2 and calls[1][0][:3] == ["/usr/sbin/visudo", "-c", "-f"]
  assert list(tmp_path.iterdir()) == [target]
  if os.name == "posix":
    assert target.stat().st_mode & 0o777 == 0o440


@pytest.mark.parametrize("failure", ["validation", "command"])
def test_gpu_access_setup_failure_keeps_the_existing_rule(monkeypatch, tmp_path, failure):
  monkeypatch.setattr(touch_status.os, "geteuid", lambda: 0, raising=False)
  target = tmp_path / "cluster-gpu-status"
  previous = touch_status.GPU_RULE_MARKER + "previous GPU reader rule\n"
  target.write_text(previous)
  run = Mock(return_value=SimpleNamespace(returncode=1)) if failure == "validation" else Mock(side_effect=OSError("visudo missing"))
  with pytest.raises((RuntimeError, OSError)):
    touch_status.install_gpu_access("receiver", tmp_path, run)
  assert target.read_text() == previous
  assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("account", ["bad user", "user ALL=(ALL) ALL", "user\nroot", "user; echo bad"])
def test_gpu_access_setup_rejects_accounts_that_could_change_rule_scope(monkeypatch, tmp_path, account):
  monkeypatch.setattr(touch_status.os, "geteuid", lambda: 0, raising=False)
  run = Mock()
  with pytest.raises(ValueError):
    touch_status.install_gpu_access(account, tmp_path, run)
  assert not list(tmp_path.iterdir())
  run.assert_not_called()


def test_gpu_access_setup_never_overwrites_an_unmanaged_rule_or_runs_without_root(monkeypatch, tmp_path):
  monkeypatch.setattr(touch_status.os, "geteuid", lambda: 1000, raising=False)
  with pytest.raises(PermissionError):
    touch_status.install_gpu_access("receiver", tmp_path)
  monkeypatch.setattr(touch_status.os, "geteuid", lambda: 0)
  target = tmp_path / "cluster-gpu-status"
  target.write_text("administrator rule\n")
  with pytest.raises(OSError, match="not managed"):
    touch_status.install_gpu_access("receiver", tmp_path)
  assert target.read_text() == "administrator rule\n"


def _worker(request, clock=lambda: 0):
  return TouchWorker(sampler=Mock(sample=Mock(return_value={"cpu": 20.0})), request=request, clock=clock)


def test_lost_post_reply_retries_the_same_id_and_then_polls_instead_of_redeploying():
  calls = []

  def request(host, payload):
    calls.append((host, None if payload is None else dict(payload)))
    if len(calls) == 1:
      raise OSError("response lost after acceptance")
    identity = calls[0][1]
    return {**identity, "state": "running" if payload else "succeeded"}

  worker = _worker(request)
  assert worker.exchange("192.168.0.82", "pi_update")[1]["state"] == "checking"
  assert worker.exchange("192.168.0.82")[1]["state"] == "running"
  assert worker.exchange("192.168.0.82")[1]["state"] == "succeeded"
  assert calls[0][1] == calls[1][1]
  assert calls[2][1] is None


def test_a_rejected_action_is_not_replaced_by_an_older_success_and_a_new_retry_can_start():
  def request(_host, payload):
    if payload is not None and request.reject:
      raise ActionHTTPError(403)
    return {**payload, "state": "running"} if payload else {"state": "idle"}
  request.reject = True
  worker = _worker(request)
  failed = worker.exchange("192.168.0.82", "upload_logs")[1]
  assert failed["state"] == "failed" and failed["reason"] == "not_connected"
  assert worker.exchange("192.168.0.82")[1] == failed
  request.reject = False
  retried = worker.exchange("192.168.0.82", "upload_logs")[1]
  assert retried["state"] == "running"
  assert retried["request_id"] != failed["request_id"]


def test_an_old_c4_dashboard_gives_an_update_instruction():
  worker = _worker(Mock(side_effect=ActionHTTPError(404)))
  assert worker.exchange("192.168.0.82", "pi_update")[1]["reason"] == "update_c4"


def test_network_failure_does_not_mark_an_accepted_job_as_complete():
  request = Mock(side_effect=[{"action": "pi_update", "state": "running", "request_id": "a" * 32}, OSError("offline")])
  worker = _worker(request)
  first = worker.exchange("192.168.0.82")[1]
  assert first["state"] == "running"
  assert worker.exchange("192.168.0.82")[1] == first


def test_lost_server_job_is_reported_instead_of_leaving_the_panel_busy_forever():
  request = Mock(side_effect=[{"action": "pi_update", "state": "running", "request_id": "a" * 32}, {"state": "idle"}])
  worker = _worker(request)
  worker.exchange("192.168.0.82")
  assert worker.exchange("192.168.0.82")[1]["state"] == "failed"
