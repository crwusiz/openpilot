import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def loader(monkeypatch):
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=Mock()))
  spec = importlib.util.spec_from_file_location("model_loader_under_test", Path(__file__).parents[1] / "model_loader.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class RestartRequested(Exception):
  pass


def test_success_waits_for_loader_to_finish(loader):
  model = object()
  finished = threading.Event()

  def load():
    finished.set()
    return model

  restart = Mock(side_effect=RestartRequested)
  assert loader.load_big_model(load, 1, restart) is model
  assert finished.is_set()
  restart.assert_not_called()


def test_failed_load_requires_clean_restart(loader):
  load = Mock(side_effect=RuntimeError("USB device hang"))
  restart = Mock(side_effect=RestartRequested)
  with pytest.raises(RestartRequested):
    loader.load_big_model(load, 1, restart)
  restart.assert_called_once_with("big model load failed")


def test_timeout_cannot_fall_through_while_loader_is_alive(loader):
  entered, release, finished = threading.Event(), threading.Event(), threading.Event()

  def load():
    entered.set()
    try:
      release.wait(5)
      return object()
    finally:
      finished.set()

  def restart(reason):
    assert entered.wait(1)
    assert not finished.is_set()
    assert "timed out" in reason
    raise RestartRequested

  try:
    with pytest.raises(RestartRequested):
      loader.load_big_model(load, 0.01, restart)
  finally:
    release.set()
    assert finished.wait(1)


@pytest.mark.parametrize("demo", [False, True])
def test_restart_uses_fresh_interpreter_and_process_local_fallback(loader, monkeypatch, demo):
  monkeypatch.delenv(loader.FORCE_SMALL_MODEL_ENV, raising=False)
  monkeypatch.setenv("MODEL_LOADER_TEST", "preserved")
  execve = Mock(side_effect=RestartRequested)
  monkeypatch.setattr(loader.os, "execve", execve)
  params = Mock()
  with pytest.raises(RestartRequested):
    loader.restart_with_small_model(params, "USB timeout", demo)

  executable, argv, env = execve.call_args.args
  assert executable == sys.executable
  assert argv == [sys.executable, "-m", "openpilot.selfdrive.modeld.modeld"] + (["--demo"] if demo else [])
  assert env[loader.FORCE_SMALL_MODEL_ENV] == "1"
  assert env["MODEL_LOADER_TEST"] == "preserved"
  assert loader.FORCE_SMALL_MODEL_ENV not in os.environ
  params.put_bool.assert_any_call("ChestnutActive", False, block=True)
  params.put_bool.assert_any_call("ChestnutLoading", False, block=True)
