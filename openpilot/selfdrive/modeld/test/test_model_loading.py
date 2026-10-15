"""Exercise the production loading block without native camera/messaging modules."""
import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODELD_PATH = Path(__file__).resolve().parents[1] / "modeld.py"


def model_loading_code():
  source = ast.parse(MODELD_PATH.read_text(encoding="utf-8"), filename=str(MODELD_PATH))
  main = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "main")
  timeout = next(node for node in source.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "BIG_MODEL_TIMEOUT" for target in node.targets))
  start = next(i for i, node in enumerate(main.body) if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "st" for target in node.targets))
  stop = next(i for i, node in enumerate(main.body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Name) and node.value.func.id == "config_realtime_process")
  main.body = main.body[start:stop] + [ast.Return(value=ast.Tuple(
    elts=[ast.Name(id="model", ctx=ast.Load()), ast.Name(id="small_model", ctx=ast.Load())], ctx=ast.Load()))]
  return compile(ast.fix_missing_locations(ast.Module(body=[timeout, main], type_ignores=[])), str(MODELD_PATH), "exec")


class TestModelLoading(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    # Use tinygrad's real process-global Context, including its exception cleanup.
    tinygrad_root = MODELD_PATH.parents[3] / "tinygrad_repo"
    with patch.object(sys, "path", [str(tinygrad_root), *sys.path]):
      from tinygrad.helpers import ALLOW_DEVICE_USAGE, Context, DEV
    cls.allow_device_usage, cls.device, cls.context = ALLOW_DEVICE_USAGE, DEV, Context
    cls.code = model_loading_code()

  def setUp(self):
    self.elapsed = 0
    self.durations = {}
    self.stage_errors = {}
    self.calls = []
    self.errors = []
    self.error_messages = []
    self.params = {}
    self.param_reads = []
    self.param_writes = []

  def check_device_access(self):
    # Any background loader fails here: no worker should overlap QCOM fallback.
    self.assertIs(threading.current_thread(), threading.main_thread())
    self.assertTrue(self.allow_device_usage, 'usage of device QCOM disallowed')
    self.assertEqual(str(self.device), "QCOM")

  def stage(self, name):
    self.check_device_access()
    self.calls.append(name)
    # Compilation temporarily disallows device access and changes the backend.
    with self.context(ALLOW_DEVICE_USAGE=0, DEV="USB+AMD:LLVM"):
      self.elapsed += self.durations.get(name, 0)
      if name in self.stage_errors:
        raise self.stage_errors[name]
    self.check_device_access()

  def make_model(self, width, height, chestnut):
    self.assertEqual((width, height), (1928, 1208))
    if chestnut:
      self.stage("big init")
    else:
      self.check_device_access()
      self.calls.append("small init")
    return SimpleNamespace(chestnut=chestnut, warmup=lambda: self.stage("warmup"))

  def get_bool(self, key):
    self.param_reads.append(key)
    return self.params.get(key, False)

  def put_bool(self, key, value):
    self.param_writes.append((key, value))
    self.params[key] = value

  def record_exception(self, message):
    self.errors.append(sys.exc_info()[1])
    self.error_messages.append(message)

  def load(self, chestnut=True):
    self.params["ChestnutLoading"] = chestnut
    namespace = {
      "CHESTNUT": chestnut, "ModelState": self.make_model,
      "wait_for_chestnut": lambda: self.stage("wait"),
      "time": SimpleNamespace(monotonic=lambda: self.elapsed),
      "cloudlog": SimpleNamespace(warning=lambda message: None, error=lambda message: None, exception=self.record_exception),
      "params": SimpleNamespace(get_bool=self.get_bool, put_bool=self.put_bool),
      "vipc_client_main": SimpleNamespace(width=1928, height=1208),
      "threading": threading,
    }
    exec(self.code, namespace)
    with self.context(ALLOW_DEVICE_USAGE=1, DEV="QCOM"):
      result = namespace["main"]()
      self.check_device_access()
    self.assertFalse(self.params["ChestnutLoading"])
    return result

  def assert_fallback(self, stage, error):
    self.stage_errors[stage] = error
    model, small_model = self.load()
    self.assertIs(model, small_model)
    self.assertFalse(model.chestnut)
    self.assertEqual(self.calls[-1], "small init")
    self.assertEqual(self.errors, [error])
    self.assertTrue(self.params["ChestnutRetry"])
    self.assertTrue(self.params["OnroadCycleRequested"])
    self.assertEqual(self.param_reads, ["ChestnutRetry"])

  def test_chestnut_disabled(self):
    model, small_model = self.load(chestnut=False)
    self.assertIs(model, small_model)
    self.assertEqual(self.calls, ["small init"])
    self.assertEqual(self.errors, [])
    self.assertEqual(self.param_writes, [("ChestnutLoading", False)])
    self.assertEqual(self.param_reads, [])

  def test_big_success_keeps_small_backup_and_runs_on_main_thread(self):
    model, small_model = self.load()
    self.assertTrue(model.chestnut)
    self.assertFalse(small_model.chestnut)
    self.assertIsNot(model, small_model)
    self.assertEqual(self.calls, ["wait", "big init", "warmup", "small init"])
    self.assertTrue(self.params["ChestnutActive"])
    self.assertEqual(self.errors, [])
    self.assertEqual(self.param_reads, [])
    self.assertNotIn("OnroadCycleRequested", self.params)

  def test_usb_failure_requests_one_retry(self):
    self.assert_fallback("wait", TimeoutError("chestnut did not enumerate"))
    self.assertEqual(self.calls, ["wait", "small init"])
    self.assertIn("USB enumeration", self.error_messages[0])

  def test_initialization_failure_restores_context_before_small(self):
    self.assert_fallback("big init", RuntimeError("compiler failure"))
    self.assertEqual(self.calls, ["wait", "big init", "small init"])
    self.assertIn("initialization", self.error_messages[0])

  def test_warmup_failure_restores_context_before_small(self):
    self.assert_fallback("warmup", RuntimeError("GPU reset"))
    self.assertEqual(self.calls, ["wait", "big init", "warmup", "small init"])
    self.assertIn("warmup", self.error_messages[0])

  def test_second_failure_does_not_request_another_cycle(self):
    self.params["ChestnutRetry"] = True
    self.stage_errors["big init"] = RuntimeError("GPU unavailable")
    model, small_model = self.load()
    self.assertIs(model, small_model)
    self.assertFalse(self.params["ChestnutActive"])
    self.assertNotIn("OnroadCycleRequested", self.params)
    self.assertEqual(self.param_reads, ["ChestnutRetry"])
    self.assertNotIn(("ChestnutRetry", True), self.param_writes)

  def test_initialization_over_deadline_finishes_then_skips_warmup(self):
    self.durations["big init"] = 61
    model, small_model = self.load()
    self.assertIs(model, small_model)
    self.assertEqual(self.elapsed, 61)
    self.assertEqual(self.calls, ["wait", "big init", "small init"])
    self.assertIsInstance(self.errors[0], TimeoutError)
    self.assertIn("initialization", str(self.errors[0]))
    self.assertTrue(self.params["OnroadCycleRequested"])

  def test_warmup_timeout_uses_combined_duration_and_restores_context(self):
    self.durations = {"big init": 30, "warmup": 31}
    model, small_model = self.load()
    self.assertIs(model, small_model)
    self.assertEqual(self.elapsed, 61)
    self.assertEqual(self.calls, ["wait", "big init", "warmup", "small init"])
    self.assertIsInstance(self.errors[0], TimeoutError)
    self.assertIn("warmup", str(self.errors[0]))
    self.assertTrue(self.params["OnroadCycleRequested"])

  def test_exactly_sixty_seconds_succeeds(self):
    self.durations = {"big init": 30, "warmup": 30}
    model, small_model = self.load()
    self.assertTrue(model.chestnut)
    self.assertFalse(small_model.chestnut)
    self.assertTrue(self.params["ChestnutActive"])
    self.assertEqual(self.errors, [])


if __name__ == "__main__":
  unittest.main()
