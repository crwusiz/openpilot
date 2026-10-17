import ast
import __future__
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.system.manager import cluster_process


@pytest.fixture
def manager(monkeypatch):
  # Run the production ensure_running loop without importing device/cereal
  # bindings or creating real driving processes on the desktop test host.
  path = Path(__file__).parents[1] / "process.py"
  tree = ast.parse(path.read_text(encoding="utf-8"))
  function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "ensure_running")
  namespace = {"prepare_cluster_start": cluster_process.prepare_cluster_start}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec",
               flags=__future__.annotations.compiler_flag), namespace)
  report, messages = Mock(), Mock()
  monkeypatch.setattr(cluster_process, "record_process_exit", report)
  monkeypatch.setattr(cluster_process, "flog", messages)
  values = {"ClusterDisplayTransport": "usb"}
  params = SimpleNamespace(get=values.get)

  class Managed:
    def __init__(self, name):
      self.name = name
      self.enabled = True
      self.proc = None
      self.shutting_down = False
      self.should_run = Mock(return_value=True)
      self.starts = 0

    def start(self):
      if self.shutting_down:
        self.stop()
        self.shutting_down = False
      if self.proc is None:
        self.starts += 1
        self.proc = SimpleNamespace(pid=100 + self.starts, exitcode=None)

    def stop(self, block=True):
      self.proc = None

  process = Managed("cluster")
  other = Managed("modeld")

  def tick():
    return namespace["ensure_running"]([process, other], False, params, None)

  return SimpleNamespace(process=process, other=other, tick=tick, values=values, report=report, messages=messages)


def test_normal_mode_exit_restarts_cluster_instead_of_leaving_dead_process_reference(manager):
  manager.tick()
  manager.process.proc.exitcode = 0
  manager.values["ClusterDisplayTransport"] = "network"
  manager.tick()
  assert manager.process.starts == 2
  assert manager.process.proc.exitcode is None
  manager.report.assert_called_once_with(101, 0, False)
  assert "selection_changed" in manager.messages.call_args.args[0]
  assert manager.other.starts == 1


@pytest.mark.parametrize("exit_code", [1, -6, -9, -11])
def test_unexpected_exit_is_reported_once_without_a_hardware_crash_restart_loop(manager, exit_code):
  manager.tick()
  manager.process.proc.exitcode = exit_code
  for _ in range(3):
    manager.tick()
    cluster_process.observe_cluster_exit(manager.process)
  manager.report.assert_called_once_with(101, exit_code, False)
  assert manager.process.starts == 1
  assert manager.process.proc.exitcode == exit_code


def test_selecting_network_after_failed_usb_restarts_and_allows_another_pid_report(manager):
  manager.tick()
  manager.process.proc.exitcode = 1
  manager.tick()
  manager.values["ClusterDisplayTransport"] = "network"
  manager.tick()
  assert manager.process.starts == 2
  manager.process.proc.exitcode = -6
  manager.tick()
  assert manager.report.call_count == 2
  manager.report.assert_called_with(102, -6, False)


def test_normal_exit_can_restart_same_selection_but_disabled_cluster_stays_stopped(manager):
  manager.tick()
  manager.process.proc.exitcode = 0
  manager.tick()
  assert manager.process.starts == 2
  manager.process.should_run.return_value = False
  manager.tick()
  assert manager.process.proc is None
  assert manager.process.starts == 2


def test_other_process_death_behavior_is_unchanged(manager):
  manager.tick()
  manager.other.proc.exitcode = -6
  for _ in range(3):
    manager.tick()
  assert manager.other.starts == 1
  manager.report.assert_not_called()


def test_switch_during_async_stop_records_the_new_child_selection(manager):
  manager.tick()
  manager.process.shutting_down = True
  manager.values["ClusterDisplayTransport"] = "network"
  manager.tick()
  assert manager.process.starts == 2
  manager.process.proc.exitcode = 1
  manager.tick()
  assert manager.process.starts == 2
