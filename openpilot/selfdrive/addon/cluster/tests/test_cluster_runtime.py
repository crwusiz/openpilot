import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import textwrap
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


@pytest.fixture
def launcher(monkeypatch):
  path = Path(__file__).parents[2] / "cluster_run.py"
  spec = importlib.util.spec_from_file_location("cluster_launcher_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  logger = Mock()
  diagnostics = SimpleNamespace(close_crash_log=Mock(), close_log=Mock(), initialize_crash_log=Mock(), record_crash=Mock())
  runner = Mock()
  monkeypatch.setattr(module, "configure_cluster_runtime", Mock())
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.cluster_logging", diagnostics)
  monkeypatch.setitem(sys.modules, "openpilot.common.realtime", SimpleNamespace(set_core_affinity=Mock()))
  monkeypatch.setitem(sys.modules, "openpilot.common.swaglog", SimpleNamespace(cloudlog=logger))
  monkeypatch.setitem(sys.modules, "setproctitle", SimpleNamespace(setproctitle=Mock()))
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.main", SimpleNamespace(cluster_main=runner))
  return SimpleNamespace(module=module, diagnostics=diagnostics, logger=logger, runner=runner)


def test_launcher_captures_bootstrap_failure_before_cloudlog_is_available(launcher):
  error = RuntimeError("bootstrap failed")
  launcher.module.configure_cluster_runtime.side_effect = error
  with pytest.raises(RuntimeError, match="bootstrap failed"):
    launcher.module.main()
  launcher.diagnostics.initialize_crash_log.assert_called_once()
  launcher.diagnostics.record_crash.assert_called_once_with(error, "runtime bootstrap")
  launcher.diagnostics.close_crash_log.assert_not_called()
  launcher.diagnostics.close_log.assert_called_once()
  launcher.runner.assert_not_called()


def test_launcher_preserves_cluster_import_failure_and_nonzero_exit(launcher, monkeypatch):
  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.main", None)
  with pytest.raises(ModuleNotFoundError):
    launcher.module.main()
  error, stage = launcher.diagnostics.record_crash.call_args.args
  assert isinstance(error, ModuleNotFoundError)
  assert stage == "cluster imports"
  launcher.logger.exception.assert_called_once_with("Cluster crashed")


def test_launcher_preserves_runtime_failure_after_backend_cleanup(launcher):
  error = RuntimeError("backend failed")
  launcher.runner.side_effect = error
  with pytest.raises(RuntimeError, match="backend failed"):
    launcher.module.main()
  launcher.diagnostics.record_crash.assert_called_once_with(error, "cluster startup or main loop")
  launcher.diagnostics.close_crash_log.assert_not_called()


def test_launcher_normal_exit_does_not_record_a_crash(launcher):
  launcher.module.main()
  launcher.diagnostics.record_crash.assert_not_called()
  launcher.diagnostics.close_crash_log.assert_not_called()


LATE_EXIT_SCRIPT = textwrap.dedent("""
import atexit
import faulthandler
import os
import sys
from types import SimpleNamespace
from openpilot.selfdrive.addon import cluster_run
from openpilot.selfdrive.addon.cluster import cluster_logging

if len(sys.argv) > 4:
    try:
        os.fstat(int(sys.argv[4]))
    except OSError:
        print('bootstrap_descriptor_closed=1', flush=True)
    else:
        raise AssertionError('Old crash descriptor survived exec')

cluster_logging.CRASH_LOG_FILE, cluster_logging.LOG_FILE, mode = sys.argv[1:4]
logger = SimpleNamespace(info=lambda *_a, **_k: None, bind=lambda *_a, **_k: None, exception=lambda *_a, **_k: None)
sys.modules['openpilot.common.realtime'] = SimpleNamespace(set_core_affinity=lambda *_a: None)
sys.modules['openpilot.common.swaglog'] = SimpleNamespace(cloudlog=logger)
sys.modules['setproctitle'] = SimpleNamespace(setproctitle=lambda *_a: None)

def fail():
    raise RuntimeError('subprocess lifecycle test')

cluster_run.configure_cluster_runtime = fail if mode == 'bootstrap_failure' else lambda: None
sys.modules['openpilot.selfdrive.addon.cluster.main'] = None if mode == 'import_failure' else SimpleNamespace(
    cluster_main=fail if mode == 'backend_failure' else lambda: None)

def late_exit():
    assert faulthandler.is_enabled()
    handle = cluster_logging._crash_handle
    assert handle is not None and not handle.closed
    assert not os.get_inheritable(handle.fileno())
    os.write(handle.fileno(), b'[CLUSTER_LATE_EXIT_TEST] descriptor_alive=1\\n')
    print('late_handler=1 descriptor_alive=1', flush=True)
    if mode == 'abort':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.abort()

atexit.register(late_exit)
try:
    cluster_run.main()
    if mode == 'repeat':
        descriptor = cluster_logging._crash_handle.fileno()
        cluster_run.main()
        assert cluster_logging._crash_handle.fileno() == descriptor
except Exception:
    sys.exit(17)
""")


@pytest.mark.parametrize("mode,exit_code,stage", [
  ("normal", 0, None), ("repeat", 0, None), ("bootstrap_failure", 17, "runtime bootstrap"),
  ("import_failure", 17, "cluster imports"), ("backend_failure", 17, "cluster startup or main loop"),
])
def test_actual_launcher_keeps_crash_descriptor_through_atexit(tmp_path, mode, exit_code, stage):
  crash_path, debug_path = tmp_path / "cluster_crash.log", tmp_path / "cluster_debug.log"
  result = subprocess.run([sys.executable, "-B", "-c", LATE_EXIT_SCRIPT, str(crash_path), str(debug_path), mode],
                          cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=10)
  assert result.returncode == exit_code, result.stderr
  assert "late_handler=1 descriptor_alive=1" in result.stdout, result.stderr
  crash = crash_path.read_text()
  assert "[CLUSTER_LATE_EXIT_TEST] descriptor_alive=1" in crash
  assert crash.count("[CLUSTER_PROCESS_START]") == (2 if mode == "repeat" else 1)
  assert ("[CLUSTER_CRASH]" in crash) is (stage is not None)
  if stage is not None:
    assert f"stage={stage}" in crash


@pytest.mark.skipif(sys.platform != "linux", reason="Native abort regression uses Linux signals without a crash dialog")
def test_native_abort_after_launcher_returns_retains_late_exit_stack(tmp_path):
  crash_path, debug_path = tmp_path / "cluster_crash.log", tmp_path / "cluster_debug.log"
  result = subprocess.run([sys.executable, "-B", "-c", LATE_EXIT_SCRIPT, str(crash_path), str(debug_path), "abort"],
                          cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=10)
  assert result.returncode == -6, result.stderr
  crash = crash_path.read_text()
  assert "Fatal Python error: Aborted" in crash
  assert "late_exit" in crash and "[CLUSTER_LATE_EXIT_TEST] descriptor_alive=1" in crash


@pytest.mark.skipif(sys.platform != "linux", reason="Native bootstrap uses POSIX process replacement")
def test_actual_native_bootstrap_closes_old_descriptor_and_installs_a_new_handler(tmp_path):
  crash_path, debug_path = tmp_path / "cluster_crash.log", tmp_path / "cluster_debug.log"
  script = textwrap.dedent("""
import os
import sys
from openpilot.selfdrive.addon import cluster_run
from openpilot.selfdrive.addon.cluster import cluster_logging

cluster_logging.CRASH_LOG_FILE, cluster_logging.LOG_FILE = sys.argv[1:3]
def exec_bootstrap():
    descriptor = cluster_logging._crash_handle.fileno()
    assert not os.get_inheritable(descriptor)
    os.execve(sys.executable, [sys.executable, '-B', '-c', sys.argv[3], sys.argv[1], sys.argv[2], 'normal', str(descriptor)],
              dict(os.environ, CLUSTER_NATIVE_BOOTSTRAP='1'))
cluster_run.configure_cluster_runtime = exec_bootstrap
cluster_run.main()
""")
  result = subprocess.run([sys.executable, "-B", "-c", script, str(crash_path), str(debug_path), LATE_EXIT_SCRIPT],
                          cwd=Path(__file__).resolve().parents[5], capture_output=True, text=True, timeout=10)
  assert result.returncode == 0, result.stderr
  assert "bootstrap_descriptor_closed=1" in result.stdout
  assert "late_handler=1 descriptor_alive=1" in result.stdout
  crash = crash_path.read_text()
  assert crash.count("[CLUSTER_PROCESS_START]") == 2
  assert "native_bootstrap=1" in crash and "[CLUSTER_LATE_EXIT_TEST] descriptor_alive=1" in crash
