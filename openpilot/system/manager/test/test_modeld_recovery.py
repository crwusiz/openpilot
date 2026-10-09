import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.system.manager.modeld_recovery import ChestnutModeldRecovery


class FakeSubMaster:
  def __init__(self):
    self.messages = {
      "modelV2": SimpleNamespace(big=False),
      "carState": SimpleNamespace(canValid=True, standstill=True, vEgo=0.),
      "selfdriveState": SimpleNamespace(enabled=False, active=False),
    }
    self.seen = dict.fromkeys(self.messages, True)
    self.alive = dict.fromkeys(self.messages, True)
    self.valid = dict.fromkeys(self.messages, True)
    self.logMonoTime = dict.fromkeys(self.messages, 0)

  def __getitem__(self, name):
    return self.messages[name]

class TestChestnutModeldRecovery(unittest.TestCase):
  def setUp(self):
    self.now = 100.
    self.clock = patch("openpilot.system.manager.modeld_recovery.time.monotonic", side_effect=lambda: self.now)
    self.clock.start()
    self.addCleanup(self.clock.stop)
    self.new_case()

  def new_case(self):
    self.now = 100.
    self.models_available = True
    self.power_identity = (1, 2)
    self.loading = False
    self.process_alive = True
    self.params = SimpleNamespace(get_bool=lambda name: self.loading if name == "ChestnutLoading" else False)
    self.sm = FakeSubMaster()
    self.modeld = SimpleNamespace(enabled=True, shutting_down=False,
                                 proc=SimpleNamespace(pid=42, exitcode=None, is_alive=lambda: self.process_alive))
    self.power_probe = Mock(side_effect=lambda: self.power_identity)
    self.recovery = ChestnutModeldRecovery(models_available=lambda: self.models_available, power_probe=self.power_probe)

  def tick(self, at, *, started=True, fresh_model=True):
    self.now = at
    for name in self.sm.messages:
      if name != "modelV2" or fresh_model:
        self.sm.logMonoTime[name] = int((at - .001) * 1e9)
    return self.recovery.update(started, self.params, self.sm, self.modeld)

  def approve_restart(self, start=100.):
    self.assertFalse(self.tick(start))
    self.assertFalse(self.tick(start + 1.))
    self.assertTrue(self.tick(start + 1. + self.recovery.STABLE_TIME + .01))

  def test_fallback_retries_after_power_is_stable_without_pcie_gate(self):
    self.assertFalse(self.tick(100.))
    self.assertFalse(self.tick(101.))
    self.assertFalse(self.tick(101. + self.recovery.STABLE_TIME - .01))
    self.assertTrue(self.tick(101. + self.recovery.STABLE_TIME + .01))
    self.assertEqual(self.recovery.attempts, 1)

  def test_power_recovers_after_acc_returns(self):
    self.power_identity = None
    self.assertFalse(self.tick(100.))
    self.assertFalse(self.tick(120.))
    self.power_identity = (1, 2)
    self.assertFalse(self.tick(121.))
    self.assertTrue(self.tick(121. + self.recovery.STABLE_TIME + .01))

  def test_power_loss_restarts_the_stability_interval(self):
    self.assertFalse(self.tick(100.))
    self.assertFalse(self.tick(101.))
    self.power_identity = None
    self.assertFalse(self.tick(103.))
    self.power_identity = (1, 2)
    self.assertFalse(self.tick(104.))
    self.assertFalse(self.tick(104. + self.recovery.STABLE_TIME - .01))
    self.assertTrue(self.tick(104. + self.recovery.STABLE_TIME + .01))

  def test_usb_reenumeration_restarts_the_stability_interval(self):
    self.assertFalse(self.tick(100.))
    self.assertFalse(self.tick(101.))
    self.power_identity = (1, 3)
    self.assertFalse(self.tick(103.))
    self.assertFalse(self.tick(103. + self.recovery.STABLE_TIME - .01))
    self.assertTrue(self.tick(103. + self.recovery.STABLE_TIME + .01))

  def test_big_model_and_missing_model_assets_do_not_restart(self):
    for unavailable in ("big", "assets"):
      with self.subTest(unavailable=unavailable):
        self.new_case()
        self.sm["modelV2"].big = unavailable == "big"
        self.models_available = unavailable != "assets"
        self.assertFalse(self.tick(100.))
        self.assertFalse(self.tick(101.))
        self.assertFalse(self.tick(120.))
        self.assertEqual(self.recovery.attempts, 0)

  def test_loading_process_is_not_interrupted(self):
    self.loading = True
    self.assertFalse(self.tick(100.))
    self.assertFalse(self.tick(120.))
    self.loading = False
    self.assertFalse(self.tick(121.))
    self.assertTrue(self.tick(121. + self.recovery.STABLE_TIME + .01))

  def test_invalid_model_calibration_does_not_block_recovery(self):
    self.sm.valid["modelV2"] = False
    self.approve_restart()

  def test_unseen_or_dead_model_output_does_not_restart(self):
    for field in ("seen", "alive"):
      with self.subTest(field=field):
        self.new_case()
        getattr(self.sm, field)["modelV2"] = False
        self.assertFalse(self.tick(100.))
        self.assertFalse(self.tick(120.))

  def test_previous_pid_model_output_is_ignored(self):
    self.assertFalse(self.tick(100.))
    self.assertFalse(self.tick(101.))
    self.modeld.proc.pid = 43
    self.assertFalse(self.tick(110., fresh_model=False))
    self.assertFalse(self.tick(120., fresh_model=False))
    self.assertFalse(self.tick(121.))
    self.assertTrue(self.tick(121. + self.recovery.STABLE_TIME + .01))

  def test_non_running_or_disabled_process_does_not_restart(self):
    for state in ("disabled", "absent", "dead", "shutting_down"):
      with self.subTest(state=state):
        self.new_case()
        if state == "disabled":
          self.modeld.enabled = False
        elif state == "absent":
          self.modeld.proc = None
        elif state == "dead":
          self.process_alive = False
        else:
          self.modeld.shutting_down = True
        self.assertFalse(self.tick(100.))
        self.assertFalse(self.tick(120.))

  def test_car_and_controls_must_be_current_and_valid(self):
    for service in ("carState", "selfdriveState"):
      for field in ("seen", "alive", "valid"):
        with self.subTest(service=service, field=field):
          self.new_case()
          getattr(self.sm, field)[service] = False
          self.assertFalse(self.tick(100.))
          self.assertFalse(self.tick(101.))
          self.assertFalse(self.tick(120.))

  def test_moving_invalid_can_and_active_controls_do_not_restart(self):
    conditions = (
      ("carState", "canValid", False),
      ("carState", "standstill", False),
      ("carState", "vEgo", .1),
      ("carState", "vEgo", -.1),
      ("carState", "vEgo", math.nan),
      ("selfdriveState", "enabled", True),
      ("selfdriveState", "active", True),
    )
    for service, field, value in conditions:
      with self.subTest(service=service, field=field, value=value):
        self.new_case()
        setattr(self.sm[service], field, value)
        self.assertFalse(self.tick(100.))
        self.assertFalse(self.tick(101.))
        self.assertFalse(self.tick(120.))

  def test_restart_cooldown_and_onroad_budget_survive_pid_changes(self):
    self.approve_restart()
    first_restart = self.now
    self.modeld.proc.pid = 43
    self.assertFalse(self.tick(first_restart + 1.))
    self.assertFalse(self.tick(first_restart + 2.))
    self.assertFalse(self.tick(first_restart + self.recovery.COOLDOWN - .01))
    self.assertTrue(self.tick(first_restart + self.recovery.COOLDOWN + .01))
    self.assertEqual(self.recovery.attempts, self.recovery.MAX_RESTARTS)
    self.modeld.proc.pid = 44
    self.assertFalse(self.tick(self.now + 1.))
    self.assertFalse(self.tick(self.now + 1.))
    self.assertFalse(self.tick(self.now + self.recovery.COOLDOWN + self.recovery.STABLE_TIME + 10.))

  def test_offroad_resets_attempt_budget(self):
    self.approve_restart()
    self.assertFalse(self.tick(150., started=False))
    self.assertEqual(self.recovery.attempts, 0)
    self.modeld.proc.pid = 43
    self.approve_restart(start=200.)

  def test_explicit_reset_clears_attempt_budget_and_stability(self):
    self.approve_restart()
    self.recovery.reset()
    self.assertEqual(self.recovery.attempts, 0)
    self.approve_restart(start=150.)


if __name__ == "__main__":
  unittest.main()
