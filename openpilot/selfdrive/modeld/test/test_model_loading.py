"""Exercise modeld's actual loading block without importing native camera/messaging modules."""
import ast
from collections.abc import Callable
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODELD_PATH = Path(__file__).resolve().parents[1] / "modeld.py"


def model_loading_code():
  # Compile the production statements directly; only the surrounding camera setup
  # and infinite inference loop need native dependencies and are omitted here.
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


def model_state_code():
  source = ast.parse(MODELD_PATH.read_text(encoding="utf-8"), filename=str(MODELD_PATH))
  model_state = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "ModelState")
  return compile(ast.Module(body=[model_state], type_ignores=[]), str(MODELD_PATH), "exec")


class TestModelLoading(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    # Windows cannot import modeld's visionipc/cereal extensions, but tinygrad's
    # actual process-global Context is available and must survive loader failures.
    tinygrad_root = Path(__file__).resolve().parents[4] / "tinygrad_repo"
    with patch.object(sys, "path", [str(tinygrad_root), *sys.path]):
      from tinygrad.helpers import ALLOW_DEVICE_USAGE, Context, DEV
    cls.allow_device_usage = ALLOW_DEVICE_USAGE
    cls.device = DEV
    cls.context = Context
    logger_spec = importlib.util.spec_from_file_location("model_load_test_logger", MODELD_PATH.with_name("model_log.py"))
    logger_module = importlib.util.module_from_spec(logger_spec)
    logger_spec.loader.exec_module(logger_module)
    cls.logger_type = logger_module.ModelLoadLogger
    cls.code = model_loading_code()
    cls.model_state_code = model_state_code()

  def setUp(self):
    self.tempdir = tempfile.TemporaryDirectory()
    self.addCleanup(self.tempdir.cleanup)
    self.log_path = Path(self.tempdir.name) / "model_debug.log"
    self.model_log = self.logger_type(self.log_path)
    self.addCleanup(self.model_log.close)
    self.init_error = None
    self.warmup_error = None
    self.wait_error = None
    self.block_stage = None
    self.stage_entered = threading.Event()
    self.release_loader = threading.Event()
    self.calls = []
    self.models = []
    self.errors = []
    self.params = {}
    self.loaders = []
    self.join_timeouts = []

  def check_device_access(self):
    self.assertTrue(self.allow_device_usage)
    self.assertEqual(str(self.device), "QCOM")

  def record_call(self, name):
    self.check_device_access()
    self.calls.append((name, threading.current_thread().name))

  def pause_stage(self, name):
    if self.block_stage == name:
      self.stage_entered.set()
      if not self.release_loader.wait(5):
        raise TimeoutError(f"test did not release {name}")

  def wait_for_chestnut(self):
    self.record_call("wait")
    self.pause_stage("chestnut_wait")
    if self.wait_error is not None:
      raise self.wait_error

  def make_model(self, width, height, chestnut, model_log):
    self.record_call("big init" if chestnut else "small init")
    self.assertEqual((width, height), (1928, 1208))
    self.assertIs(model_log, self.model_log)
    if chestnut:
      self.pause_stage("big_model_init")
      with self.context(ALLOW_DEVICE_USAGE=0, DEV="USB+AMD:LLVM"):
        if self.init_error is not None:
          raise self.init_error
      self.calls.append(("big init done", threading.current_thread().name))
    model = SimpleNamespace(chestnut=chestnut, warmup=self.warmup)
    self.models.append(model)
    return model

  def warmup(self):
    self.record_call("warmup")
    self.pause_stage("big_model_warmup")
    with self.context(ALLOW_DEVICE_USAGE=0, DEV="USB+AMD:LLVM"):
      if self.warmup_error is not None:
        raise self.warmup_error
    self.calls.append(("warmup done", threading.current_thread().name))

  def load(self, chestnut=True):
    test = self

    class LoaderThread(threading.Thread):
      def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        test.loaders.append(self)

      def join(self, timeout=None):
        test.join_timeouts.append(timeout)
        test.assertEqual(timeout, 60)
        if test.block_stage is not None:
          # The worker is genuinely alive inside the chosen logged stage, but
          # the production deadline elapses deterministically without a 60s wait.
          test.assertTrue(test.stage_entered.wait(2), "loader did not reach the blocked stage")
          return super().join(0)
        return super().join(2)

    namespace = {
      "CHESTNUT": chestnut,
      "ModelState": self.make_model,
      "wait_for_chestnut": self.wait_for_chestnut,
      "time": time,
      "cloudlog": SimpleNamespace(warning=lambda msg: None, exception=lambda msg: self.errors.append(sys.exc_info()[1])),
      "params": SimpleNamespace(put_bool=lambda key, value: self.params.update({key: value})),
      "vipc_client_main": SimpleNamespace(width=1928, height=1208),
      "threading": SimpleNamespace(Thread=LoaderThread, Event=threading.Event, Lock=threading.Lock),
      "model_log": self.model_log,
      "log_model_environment": lambda logger, event: logger.event(event, simulated_environment=True),
    }
    exec(self.code, namespace)
    with self.context(ALLOW_DEVICE_USAGE=1, DEV="QCOM"):
      try:
        result = namespace["main"]()
      finally:
        self.release_loader.set()
        for loader in self.loaders:
          threading.Thread.join(loader, 2)
          self.assertFalse(loader.is_alive(), "test leaked a loader thread")
      self.check_device_access()
    self.assertFalse(self.params["ChestnutLoading"])
    return result

  def logged_events(self, event=None):
    records = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]
    return records if event is None else [record for record in records if record["event"] == event]

  def assert_selection(self, selected_model, reason, loader_alive=False, stage="idle"):
    records = self.logged_events("big_model_selection")
    self.assertEqual(len(records), 1)
    selection = records[0]
    self.assertEqual(selection["selected_model"], selected_model)
    self.assertEqual(selection["reason"], reason)
    self.assertIs(selection["loader_alive"], loader_alive)
    self.assertEqual(selection["stage"], stage)
    self.assertEqual(selection["timeout_s"], 60)
    self.assertEqual(selection["stage_stack"], [stage] if loader_alive else [])
    self.assertEqual(self.join_timeouts, [60])

  def assert_ready(self, selected_model):
    records = self.logged_events("models_ready")
    self.assertEqual(len(records), 1)
    self.assertEqual(records[0]["selected_model"], selected_model)
    self.assertGreaterEqual(records[0]["elapsed_load_s"], 0)

  def assert_fallback(self, model, small_model, error, failed_stage):
    self.assertIs(model, small_model)
    self.assertFalse(model.chestnut)
    self.assertFalse(self.params["ChestnutActive"])
    self.assertEqual(self.errors, [error])
    self.assertEqual(self.calls[-1], ("small init", threading.current_thread().name))
    self.assert_selection("small", "load_failed")
    self.assert_ready("small")
    failed = self.logged_events("stage_failed")
    self.assertEqual([record["stage"] for record in failed], [failed_stage])
    self.assertEqual(failed[0]["thread"], "big-model-loader")
    self.assertEqual(failed[0]["exception_type"], type(error).__name__)
    self.assertEqual(failed[0]["exception"], str(error))
    self.assertEqual(len(self.logged_events("big_model_load_failed")), 1)
    self.assertEqual(self.logged_events("big_model_loaded"), [])
    contents = self.log_path.read_text(encoding="utf-8")
    self.assertIn("Traceback (most recent call last):", contents)
    self.assertIn(f"{type(error).__name__}: {error}", contents)

  def test_chestnut_disabled(self):
    model, small_model = self.load(chestnut=False)
    self.assertIs(model, small_model)
    self.assertEqual(self.calls, [("small init", threading.current_thread().name)])
    self.assertEqual(self.errors, [])
    self.assertEqual(self.loaders, [])
    self.assertEqual(self.logged_events("big_model_selection"), [])
    self.assert_ready("small")

  def test_big_model_success_keeps_small_model_for_runtime_fallback(self):
    model, small_model = self.load()
    self.assertTrue(model.chestnut)
    self.assertFalse(small_model.chestnut)
    self.assertIsNot(model, small_model)
    self.assertTrue(self.params["ChestnutActive"])
    self.assertEqual(self.calls, [(name, "big-model-loader") for name in
                                 ("wait", "big init", "big init done", "warmup", "warmup done")]
                     + [("small init", threading.current_thread().name)])
    self.assertEqual(self.errors, [])
    self.assert_selection("big", "ready")
    self.assert_ready("big")
    self.assertFalse(self.logged_events("big_model_loaded")[0]["completed_after_selection"])
    self.assertTrue(self.loaders[0].daemon)

  def test_chestnut_enumeration_failure_records_stage_and_traceback(self):
    self.wait_error = TimeoutError("chestnut did not enumerate")
    self.assert_fallback(*self.load(), self.wait_error, "chestnut_wait")

  def test_constructor_failure_restores_context_before_fallback(self):
    self.init_error = RuntimeError("model initialization failed")
    self.assert_fallback(*self.load(), self.init_error, "big_model_init")

  def test_warmup_failure_restores_context_before_fallback(self):
    self.warmup_error = RuntimeError("model warmup failed")
    self.assert_fallback(*self.load(), self.warmup_error, "big_model_warmup")

  def assert_timeout(self, blocked_stage):
    self.block_stage = blocked_stage
    model, small_model = self.load()
    self.assertIs(model, small_model)
    self.assertFalse(model.chestnut)
    self.assertFalse(self.params["ChestnutActive"])
    self.assertEqual(self.errors, [])
    self.assert_selection("small", "timeout", loader_alive=True, stage=blocked_stage)
    self.assert_ready("small")
    loaded = self.logged_events("big_model_loaded")
    self.assertEqual(len(loaded), 1)
    self.assertTrue(loaded[0]["completed_after_selection"])
    self.assertEqual(loaded[0]["thread"], "big-model-loader")
    self.assertTrue(any(candidate.chestnut for candidate in self.models))
    records = self.logged_events()
    self.assertLess(next(i for i, record in enumerate(records) if record["event"] == "models_ready"),
                    next(i for i, record in enumerate(records) if record["event"] == "big_model_loaded"))

  def test_chestnut_wait_timeout_records_active_stage_and_retains_small(self):
    self.assert_timeout("chestnut_wait")

  def test_constructor_timeout_records_active_stage_and_retains_small(self):
    self.assert_timeout("big_model_init")

  def test_warmup_timeout_records_active_stage_and_retains_small(self):
    self.assert_timeout("big_model_warmup")

  def test_actual_constructor_logs_context_and_preserves_load_oob_exception(self):
    error = RuntimeError("serialized model load sentinel")
    calls = []
    model_path = Path("test_big_model.pkl")

    def load_oob(path, chestnut):
      calls.append((path, chestnut))
      raise error

    namespace = {"np": SimpleNamespace(ndarray=object), "ModelLoadLogger": self.logger_type,
                 "nullcontext": nullcontext, "Callable": Callable, "VisionBuf": object,
                 "DEV": self.device, "ALLOW_DEVICE_USAGE": self.allow_device_usage,
                 "modeld_pkl_path": lambda chestnut: model_path, "load_oob": load_oob}
    exec(self.model_state_code, namespace)
    with self.context(ALLOW_DEVICE_USAGE=1, DEV="QCOM"):
      with self.assertRaises(RuntimeError) as caught:
        namespace["ModelState"](1928, 1208, True, self.model_log)
      self.assertIs(caught.exception, error)
      self.check_device_access()
    self.assertEqual(calls, [(model_path, True)])
    starts = self.logged_events("stage_start")
    self.assertEqual(len(starts), 1)
    self.assertEqual(starts[0]["stage"], "load_oob")
    self.assertEqual(starts[0]["context"], {"DEV": "QCOM", "ALLOW_DEVICE_USAGE": 1})
    self.assertEqual(starts[0]["model"], "big")
    self.assertEqual(starts[0]["path"], str(model_path))
    self.assertEqual(self.logged_events("stage_failed")[0]["stage"], "load_oob")
    self.assertIn(f"RuntimeError: {error}", self.log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
  unittest.main()
