from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.selfdrived import selfdrived


@pytest.mark.parametrize('timeout_is_slowest', [False, True])
def test_lag_diagnostics_use_the_prior_timing_window(monkeypatch, timeout_is_slowest):
  driver = selfdrived.SelfdriveD.__new__(selfdrived.SelfdriveD)
  driver.car_state_sock = object()
  driver.last_car_state_publish_ns = 0
  driver.initialized = True
  driver.enabled = False
  driver.sm = Mock()
  driver.rk = Mock()
  driver.rk.avg_dt.get_average.return_value = 0.0112
  driver.loop_timings = deque(maxlen=100)
  driver.lagging_prev = False
  driver.events = SimpleNamespace(names=[])
  receive_one = Mock()
  log_event = Mock()
  monkeypatch.setattr(selfdrived.messaging, 'recv_one', receive_one)
  monkeypatch.setattr(selfdrived.cloudlog, 'event', log_event)

  frames = (
    (100.005, 0.005, 100_000_000_000, -2.0),
    (100.042, 0.009 if timeout_is_slowest else 0.030, 100_030_000_000, 24.0),
    (100.062, 0.020, None, None),
    (100.070, 0.008, 0, -4.0),
    (100.074, 0.004, 100_075_000_000, 3.0),  # A future timestamp is unavailable.
    (100.084, 0.010, 100_080_000_000, -1.0),
    (100.140, 0.042, 100_130_000_000, 100.0),  # Current frame triggers reporting.
  )
  for index, (receive_end, receive_s, publish_ns, card_lag_ms) in enumerate(frames):
    times = iter((receive_end - receive_s, receive_end))
    monkeypatch.setattr(selfdrived, 'time', SimpleNamespace(monotonic=times.__next__))
    if publish_ns is None:
      receive_one.return_value = None
    else:
      state = SimpleNamespace(cumLagMs=card_lag_ms, brakePressed=index == 1, gasPressed=index == 6,
                              aEgo=-2.5 if index == 1 else 0.0, canValid=True, canTimeout=False)
      receive_one.return_value = SimpleNamespace(logMonoTime=publish_ns, carState=state)

    driver.CS_prev = driver.data_sample()
    if publish_ns in (None, 0, 100_075_000_000):
      assert driver.car_state_publish_gap_ms is None
      assert driver.car_state_delivery_ms is None
    if index == len(frames) - 1:
      driver.events.names.append(selfdrived.EventName.selfdrivedLagging)
    driver._monitor_loop_time(receive_s + 0.004)

  log_event.assert_called_once()
  assert log_event.call_args.args == ('selfdrived.lagging',)
  diagnostics = log_event.call_args.kwargs
  assert diagnostics['loop_avg_ms'] == pytest.approx(11.2)
  assert diagnostics['car_state_receive_max_ms'] == pytest.approx(20.0 if timeout_is_slowest else 30.0)
  assert diagnostics['processing_max_ms'] == pytest.approx(4.0)
  assert diagnostics['car_state_timeouts'] == 1
  assert diagnostics['card_lag_ms'] == 100.0
  assert diagnostics['card_lag_max_ms'] == 24.0
  assert diagnostics['car_state_publish_gap_max_ms'] == pytest.approx(30.0)
  assert diagnostics['car_state_delivery_max_ms'] == pytest.approx(12.0)
  assert diagnostics['brake_pressed'] is False
  assert diagnostics['longitudinal_accel'] == 0.0
  assert diagnostics['brake_pressed_at_receive_max'] is (None if timeout_is_slowest else True)
  assert diagnostics['gas_pressed_at_receive_max'] is (None if timeout_is_slowest else False)
  assert diagnostics['longitudinal_accel_at_receive_max'] == (None if timeout_is_slowest else -2.5)
