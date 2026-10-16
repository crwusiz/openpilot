from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.touch_status import ActionHTTPError, PiStatus, TouchWorker


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
  assert first == {"cpu": None, "temperature": 61.0, "fan_pwm": 60, "fan_rpm": None}
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
