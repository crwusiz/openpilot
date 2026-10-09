import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import numpy as np


class InterpreterReplaced(BaseException):
  pass


@pytest.fixture
def runtime(monkeypatch):
  path = Path(__file__).parents[2] / "cluster_run.py"
  tree = ast.parse(path.read_text(encoding="utf-8"))
  function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "configure_cluster_runtime")
  process = SimpleNamespace(environ={}, PRIO_PROCESS=0, getpriority=Mock(return_value=0), setpriority=Mock(),
                            execve=Mock(side_effect=InterpreterReplaced))
  interpreter = SimpleNamespace(platform="linux", modules={}, executable="/configured/python")
  drop = Mock()
  logger = Mock()
  logger.get_ctx.return_value = {"daemon": "cluster", "version": "test-version", "dirty": False}
  monkeypatch.setitem(sys.modules, "openpilot.common.realtime", SimpleNamespace(drop_realtime=drop))
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=logger))
  namespace = {"os": process, "sys": interpreter, "Path": Path, "__file__": str(path), "json": json}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
  return SimpleNamespace(configure=namespace["configure_cluster_runtime"], process=process, interpreter=interpreter,
                         drop=drop, path=path, logger=logger)


def test_manager_fork_replaces_only_cluster_child_before_loading_native_pools(runtime):
  runtime.interpreter.modules["numpy"] = object()
  with pytest.raises(InterpreterReplaced):
    runtime.configure()
  executable, arguments, environment = runtime.process.execve.call_args.args
  assert executable == runtime.interpreter.executable
  assert arguments == [executable, str(runtime.path.resolve())]
  assert environment["CLUSTER_NATIVE_BOOTSTRAP"] == "1"
  for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    assert environment[name] == "1"
  runtime.drop.assert_not_called()
  runtime.process.setpriority.assert_not_called()


@pytest.mark.parametrize("nice", [0, 8])
def test_fresh_process_uses_normal_scheduler_without_raising_existing_priority(runtime, nice):
  runtime.process.getpriority.return_value = nice
  runtime.configure()
  runtime.process.execve.assert_not_called()
  runtime.drop.assert_called_once()
  runtime.process.setpriority.assert_called_once_with(0, 0, max(nice, 5))


def test_bootstrap_marker_prevents_reexec_loop(runtime):
  runtime.interpreter.modules["numpy"] = object()
  runtime.process.environ["CLUSTER_NATIVE_BOOTSTRAP"] = "1"
  runtime.configure()
  runtime.process.execve.assert_not_called()
  runtime.drop.assert_called_once()


def test_manager_bootstrap_preserves_context_using_logger_serialization(runtime):
  runtime.interpreter.modules["numpy"] = object()
  context = {"daemon": "cluster", "version": "test-version", "dirty": np.bool_(True)}
  runtime.logger.get_ctx.return_value = context
  with pytest.raises(InterpreterReplaced):
    runtime.configure()
  environment = runtime.process.execve.call_args.args[2]
  assert json.loads(environment["CLUSTER_LOG_CONTEXT"]) == {**context, "dirty": True}
  assert "CLUSTER_LOG_CONTEXT" not in runtime.process.environ
  runtime.logger.bind_global.assert_not_called()


def test_fresh_bootstrap_restores_context_and_removes_environment_copy(runtime):
  context = {"daemon": "cluster", "version": "test-version", "dirty": True}
  runtime.process.environ.update(CLUSTER_NATIVE_BOOTSTRAP="1", CLUSTER_LOG_CONTEXT=json.dumps(context))
  runtime.configure()
  runtime.logger.bind_global.assert_called_once_with(**context)
  assert "CLUSTER_LOG_CONTEXT" not in runtime.process.environ
  runtime.logger.warning.assert_not_called()
  runtime.configure()
  assert runtime.logger.bind_global.call_count == 1


@pytest.mark.parametrize("serialized_context", ['{"private metadata"', '["private metadata"]'])
def test_invalid_inherited_context_is_removed_without_logging_contents(runtime, serialized_context):
  runtime.process.environ.update(CLUSTER_NATIVE_BOOTSTRAP="1", CLUSTER_LOG_CONTEXT=serialized_context)
  runtime.configure()
  runtime.logger.bind_global.assert_not_called()
  runtime.logger.warning.assert_called_once_with("Cannot restore cluster log context: invalid metadata")
  assert "CLUSTER_LOG_CONTEXT" not in runtime.process.environ
  runtime.drop.assert_called_once()
