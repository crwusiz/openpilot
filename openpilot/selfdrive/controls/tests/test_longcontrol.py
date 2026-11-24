import math
from types import SimpleNamespace

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState, long_control_state_trans


class TestLongControlStateTransition(OpenpilotTestCase):

  def test_stay_stopped(self):
    active = True
    current_state = LongCtrlState.stopping
    next_state = long_control_state_trans(active, current_state,
                             should_stop=True, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(active, current_state,
                             should_stop=False, brake_pressed=True, cruise_standstill=False)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=True)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.pid
    active = False
    next_state = long_control_state_trans(active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.off

  def test_engage(self):
    active = True
    current_state = LongCtrlState.off
    next_state = long_control_state_trans(active, current_state,
                             should_stop=True, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(active, current_state,
                             should_stop=False, brake_pressed=True, cruise_standstill=False)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=True)
    assert next_state == LongCtrlState.stopping
    next_state = long_control_state_trans(active, current_state,
                             should_stop=False, brake_pressed=False, cruise_standstill=False)
    assert next_state == LongCtrlState.pid

  def test_legacy_starting_state_reselects_current_state(self):
    for active, stop, brake, standstill, expected in (
      (False, False, False, False, LongCtrlState.off),
      (True, False, False, False, LongCtrlState.pid),
      (True, True, False, False, LongCtrlState.stopping),
      (True, False, True, False, LongCtrlState.stopping),
      (True, False, False, True, LongCtrlState.stopping),
    ):
      with self.subTest(active=active, stop=stop, brake=brake, standstill=standstill):
        assert long_control_state_trans(active, LongCtrlState.starting, stop, brake, standstill) == expected


class TestLongControlOutput(OpenpilotTestCase):
  def setUp(self):
    self.CP = SimpleNamespace(stopAccel=-0.5, longitudinalTuning=SimpleNamespace(kiBP=[0.0], kiV=[0.1]))
    self.CS = SimpleNamespace(aEgo=0.0, vEgo=5.0, brakePressed=False, cruiseState=SimpleNamespace(standstill=False))
    self.control = LongControl(self.CP)

  def update(self, target=1.0, *, active=True, should_stop=False, limits=(-3.0, 2.0)):
    return self.control.update(active, self.CS, SimpleNamespace(aTarget=target, shouldStop=should_stop), limits)

  def test_stopping_caps_ramp_without_weakening_existing_braking(self):
    for previous, expected in ((-0.499, self.CP.stopAccel), (-0.9, -0.9)):
      with self.subTest(previous=previous):
        self.control.last_output_accel = previous
        assert self.update(should_stop=True) == expected
        assert self.control.long_control_state == LongCtrlState.stopping

  def test_stopping_ignores_nonfinite_target_and_blends_interrupted_release(self):
    stop_accel = self.update(math.nan, should_stop=True)
    assert self.CP.stopAccel < stop_accel < 0.0
    assert self.control.long_control_state == LongCtrlState.stopping

    self.CS.aEgo = 1.0
    assert stop_accel < self.update() < 1.0
    assert self.control.long_control_state == LongCtrlState.pid
    for _ in range(100):
      accel = self.update()
    assert accel == 1.0

  def test_inactive_returns_neutral_and_clears_stopping_history(self):
    self.update(should_stop=True)
    self.update()
    assert self.control.pid.i > 0.0 and self.control.stopping_accel_weight > 0.0
    assert self.update(active=False, limits=(-3.0, -1.0)) == 0.0
    assert self.control.long_control_state == LongCtrlState.off
    assert self.control.pid.i == self.control.stopping_accel_weight == self.control.last_output_accel == 0.0
    self.assertAlmostEqual(self.update(), 1.001)

  def test_brake_press_blocks_acceleration_without_winding_up_integral(self):
    self.update()
    integral = self.control.pid.i
    self.CS.brakePressed = True
    assert self.update() == 0.0
    assert self.control.long_control_state == LongCtrlState.pid
    assert self.update(-1.0) < 0.0
    assert self.control.pid.i == integral

    self.CS.brakePressed = False
    assert self.update() > 1.0
    assert self.control.pid.i > integral

  def test_nonfinite_pid_inputs_do_not_poison_future_outputs(self):
    for target, accel, speed in ((math.nan, 0.0, 5.0), (math.inf, 0.0, 5.0), (1.0, math.nan, 5.0), (1.0, 0.0, math.inf)):
      with self.subTest(target=target, accel=accel, speed=speed):
        self.update(should_stop=True)
        self.update()
        self.CS.aEgo, self.CS.vEgo = accel, speed
        assert self.update(target) == 0.0
        assert self.control.pid.i == self.control.stopping_accel_weight == self.control.last_output_accel == 0.0
        self.CS.aEgo, self.CS.vEgo = 0.0, 5.0
        self.assertAlmostEqual(self.update(), 1.001)

  def test_invalid_limits_return_neutral_and_recover_with_valid_limits(self):
    for limits in ((-3.0, math.nan), (-math.inf, 2.0), (1.0, -1.0)):
      with self.subTest(limits=limits):
        self.update()
        assert self.update(limits=limits) == 0.0
        assert self.control.pid.i == self.control.last_output_accel == 0.0
        self.assertAlmostEqual(self.update(), 1.001)
