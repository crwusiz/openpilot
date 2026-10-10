"""Exercise modeld's actual loading block without importing native camera/messaging modules."""
import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def model_loading_code():
  # Compile the production statements directly; only the surrounding camera setup
  # and infinite inference loop need native dependencies and are omitted here.
  path = Path(__file__).resolve().parents[1] / "modeld.py"
  source = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  main = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "main")
  timeout = next(node for node in source.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "BIG_MODEL_TIMEOUT" for target in node.targets))
  start = next(i for i, node in enumerate(main.body) if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "st" for target in node.targets))
  stop = next(i for i, node in enumerate(main.body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Name) and node.value.func.id == "config_realtime_process")
  main.body = main.body[start:stop] + [ast.Return(value=ast.Tuple(
    elts=[ast.Name(id="model", ctx=ast.Load()), ast.Name(id="small_model", ctx=ast.Load())], ctx=ast.Load()))]
  return compile(ast.fix_missing_locations(ast.Module(body=[timeout, main], type_ignores=[])), str(path), "exec")


class TestModelLoading(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    # Windows cannot import modeld's visionipc/cereal extensions, but tinygrad's
    # actual process-global Context is available and is the state at issue.
    tinygrad_root = Path(__file__).resolve().parents[4] / "tinygrad_repo"
    with patch.object(sys, "path", [str(tinygrad_root), *sys.path]):
      from tinygrad.helpers import ALLOW_DEVICE_USAGE, Context, DEV
    cls.allow_device_usage = ALLOW_DEVICE_USAGE
    cls.device = DEV
    cls.context = Context
    cls.code = model_loading_code()

  def setUp(self):
    self.elapsed = 0.0
    self.init_seconds = 0.0
    self.warmup_seconds = 0.0
    self.init_error = None
    self.warmup_error = None
    self.wait_error = None
    self.events = []
    self.models = []
    self.errors = []
    self.params = {}

  def check_device_access(self):
    self.assertIs(threading.current_thread(), threading.main_thread())
    self.assertTrue(self.allow_device_usage)
    self.assertEqual(repr(self.device), "QCOM")

  def wait_for_chestnut(self):
    self.check_device_access()
    self.events.append("wait")
    if self.wait_error is not None:
      raise self.wait_error

  def make_model(self, width, height, chestnut):
    self.check_device_access()
    self.assertEqual((width, height), (1928, 1208))
    self.events.append("big init" if chestnut else "small init")
    if chestnut:
      with self.context(ALLOW_DEVICE_USAGE=0, DEV="USB+AMD:LLVM"):
        self.elapsed += self.init_seconds
        if self.init_error is not None:
          raise self.init_error
      self.events.append("big init done")
    model = SimpleNamespace(chestnut=chestnut, warmup=self.warmup)
    self.models.append(model)
    return model

  def warmup(self):
    self.check_device_access()
    self.events.append("warmup")
    with self.context(ALLOW_DEVICE_USAGE=0, DEV="USB+AMD:LLVM"):
      self.elapsed += self.warmup_seconds
      if self.warmup_error is not None:
        raise self.warmup_error
    self.events.append("warmup done")

  def load(self, chestnut=True):
    namespace = {
      "CHESTNUT": chestnut,
      "ModelState": self.make_model,
      "wait_for_chestnut": self.wait_for_chestnut,
      "time": SimpleNamespace(monotonic=lambda: self.elapsed),
      "cloudlog": SimpleNamespace(warning=lambda msg: None, exception=lambda msg: self.errors.append(sys.exc_info()[1])),
      "params": SimpleNamespace(put_bool=lambda key, value: self.params.update({key: value})),
      "vipc_client_main": SimpleNamespace(width=1928, height=1208),
      # Retain the dependency when evaluating an older implementation so tests
      # fail on its concurrent behavior instead of failing on a missing import.
      "threading": threading,
    }
    exec(self.code, namespace)
    with self.context(ALLOW_DEVICE_USAGE=1, DEV="QCOM"):
      result = namespace["main"]()
      self.check_device_access()
    self.assertFalse(self.params["ChestnutLoading"])
    return result

  def assert_fallback(self, model, small_model):
    self.assertIs(model, small_model)
    self.assertFalse(model.chestnut)
    self.assertFalse(self.params["ChestnutActive"])
    self.assertEqual(len(self.errors), 1)
    self.assertEqual(self.events[-1], "small init")

  def test_chestnut_disabled(self):
    model, small_model = self.load(chestnut=False)
    self.assertIs(model, small_model)
    self.assertEqual(self.events, ["small init"])
    self.assertEqual(self.errors, [])

  def test_big_model_success_keeps_small_model_for_runtime_fallback(self):
    model, small_model = self.load()
    self.assertTrue(model.chestnut)
    self.assertFalse(small_model.chestnut)
    self.assertIsNot(model, small_model)
    self.assertTrue(self.params["ChestnutActive"])
    self.assertEqual(self.events, ["wait", "big init", "big init done", "warmup", "warmup done", "small init"])
    self.assertEqual(self.errors, [])

  def test_chestnut_enumeration_failure(self):
    self.wait_error = TimeoutError("chestnut did not enumerate")
    self.assert_fallback(*self.load())
    self.assertEqual(self.events, ["wait", "small init"])
    self.assertIs(self.errors[0], self.wait_error)

  def test_constructor_failure_restores_context_before_fallback(self):
    self.init_error = RuntimeError("model initialization failed")
    self.assert_fallback(*self.load())
    self.assertEqual(self.events, ["wait", "big init", "small init"])
    self.assertIs(self.errors[0], self.init_error)

  def test_warmup_failure_restores_context_before_fallback(self):
    self.warmup_error = RuntimeError("model warmup failed")
    self.assert_fallback(*self.load())
    self.assertEqual(self.events, ["wait", "big init", "big init done", "warmup", "small init"])
    self.assertIs(self.errors[0], self.warmup_error)

  def test_constructor_timeout_finishes_stage_before_fallback(self):
    self.init_seconds = 61.0
    self.assert_fallback(*self.load())
    self.assertEqual(self.events, ["wait", "big init", "big init done", "small init"])
    self.assertIsInstance(self.errors[0], TimeoutError)
    self.assertIn("initialization", str(self.errors[0]))

  def test_warmup_timeout_uses_total_elapsed_time_and_finishes_stage(self):
    self.init_seconds = 30.0
    self.warmup_seconds = 31.0
    self.assert_fallback(*self.load())
    self.assertEqual(self.events, ["wait", "big init", "big init done", "warmup", "warmup done", "small init"])
    self.assertIsInstance(self.errors[0], TimeoutError)
    self.assertIn("warmup", str(self.errors[0]))

  def test_model_ready_at_deadline_is_accepted(self):
    self.init_seconds = 30.0
    self.warmup_seconds = 30.0
    model, small_model = self.load()
    self.assertTrue(model.chestnut)
    self.assertFalse(small_model.chestnut)
    self.assertEqual(self.errors, [])


if __name__ == "__main__":
  unittest.main()
