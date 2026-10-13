import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from openpilot.selfdrive.modeld.model_log import ModelLoadLogger


class TestModelLoadLogger(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.path = Path(self.temp.name) / "model_debug.log"
    self.logger = ModelLoadLogger(self.path)
    self.addCleanup(self.logger.close)

  def events(self):
    return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]

  def test_nested_failure_keeps_original_exception_traceback_and_parent(self):
    original = ValueError("GPU initialization failed")
    thread_name = threading.current_thread().name
    with self.logger.stage("big_model"):
      with self.assertRaises(ValueError) as raised:
        with self.logger.stage("device_open"):
          self.assertEqual(self.logger.status(thread_name)["stage_stack"], ["big_model", "device_open"])
          raise original
      self.assertIs(raised.exception, original)
      self.assertEqual(self.logger.status(thread_name)["stage"], "big_model")
    self.assertEqual(self.logger.status(thread_name)["stage"], "idle")
    records = self.events()
    self.assertEqual([record["event"] for record in records], ["stage_start", "stage_start", "stage_failed", "stage_done"])
    self.assertIn("Traceback (most recent call last):", self.path.read_text(encoding="utf-8"))
    self.assertIn("raise original", self.path.read_text(encoding="utf-8"))
    self.assertIn("ValueError: GPU initialization failed", self.path.read_text(encoding="utf-8"))
    self.assertEqual(records[2]["stage"], "device_open")
    self.assertEqual(records[2]["exception_type"], "ValueError")
    self.assertEqual(records[2]["exception"], "GPU initialization failed")
    self.assertGreaterEqual(records[2]["stage_elapsed_s"], 0)

  def test_snapshot_available_while_loader_stalled(self):
    entered, release = threading.Event(), threading.Event()

    def load():
      with self.logger.stage("big_model"):
        with self.logger.stage("device_open"):
          entered.set()
          release.wait(5)

    loader = threading.Thread(target=load, name="big-model-loader")
    loader.start()
    try:
      self.assertTrue(entered.wait(2))
      snapshot = self.logger.status("big-model-loader")
      self.assertEqual(snapshot["stage"], "device_open")
      self.assertEqual(snapshot["stage_stack"], ["big_model", "device_open"])
      self.assertGreaterEqual(snapshot["stage_elapsed_s"], 0)
      json.dumps(snapshot)
    finally:
      release.set()
      loader.join(2)
    self.assertFalse(loader.is_alive())
    self.assertEqual(self.logger.status("big-model-loader")["stage"], "idle")

  def test_new_session_appends_and_each_event_is_flushed(self):
    self.logger.event("first_boot", device=Path("/dev/chestnut"))
    first = self.events()[0]
    self.logger.close()
    second = ModelLoadLogger(self.path)
    try:
      second.event("next_boot")
      records = self.events()
      self.assertEqual([record["event"] for record in records], ["first_boot", "next_boot"])
      self.assertNotEqual(records[0]["session"], records[1]["session"])
      self.assertEqual(first["device"], str(Path("/dev/chestnut")))
      for field in ("timestamp", "pid", "thread", "elapsed_s"):
        self.assertIn(field, first)
    finally:
      second.close()

  def test_initialization_failure_is_reported_once_without_affecting_stages(self):
    error_output = io.StringIO()
    with patch("openpilot.selfdrive.modeld.model_log._ModelLogHandler", side_effect=PermissionError("read-only")), \
         contextlib.redirect_stderr(error_output):
      logger = ModelLoadLogger(self.path)
      logger.event("boot")
      with logger.stage("warmup"):
        logger.event("progress")
      logger.close()
    self.assertEqual(error_output.getvalue().count("logging disabled"), 1)
    self.assertIn("read-only", error_output.getvalue())

  def test_write_failure_is_reported_once_and_does_not_mask_stage_exception(self):
    original = RuntimeError("model failure")
    error_output = io.StringIO()
    with patch.object(self.logger._handler, "emit", side_effect=OSError("disk full")), \
         contextlib.redirect_stderr(error_output):
      with self.assertRaises(RuntimeError) as raised:
        with self.logger.stage("warmup"):
          raise original
      self.logger.event("fallback")
    self.assertIs(raised.exception, original)
    self.assertEqual(error_output.getvalue().count("logging disabled"), 1)
    self.assertIn("disk full", error_output.getvalue())


if __name__ == "__main__":
  unittest.main()
