"""Upload result parsing without loading the device's graphical UI."""
import ast
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[6]


def load_helper(relative, name, namespace):
  path = ROOT / relative
  parsed = ast.parse(path.read_text(encoding="utf-8"))
  helper = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == name)
  exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), namespace)
  return namespace[name]


@pytest.mark.parametrize("output, expected", [
  ("uploaded\n", None),
  ("CLUSTER_LOG_WARNING: Crash log saved for retry\n", "Crash log saved for retry"),
  ("CLUSTER_PI_LOG_WARNING: Pi log saved for retry\n", "Pi log saved for retry"),
  ("CLUSTER_LOG_WARNING: Crash log saved for retry\nCLUSTER_PI_LOG_WARNING: Pi collection failed\n"
   + "CLUSTER_LOG_WARNING: Crash log saved for retry\n", "Crash log saved for retry; Pi collection failed"),
])
def test_both_ui_upload_paths_preserve_crash_and_pi_partial_failures(output, expected):
  dashboard = load_helper("openpilot/selfdrive/addon/dashboard.py", "_get_log_upload_warning", {"Path": Path})
  assert dashboard("/data/openpilot/scripts/log_upload.sh", output) == expected
  assert dashboard("/data/openpilot/scripts/other.sh", output) is None

  runner = Mock(return_value=subprocess.CompletedProcess([], 0, output, ""))
  community = load_helper("openpilot/selfdrive/ui/layouts/settings/community.py", "execute_log_upload",
                          {"subprocess": SimpleNamespace(run=runner)})
  assert community("cluster_debug.log") == (0, expected)
  runner.assert_called_once()


def test_ui_upload_warnings_remain_bounded_and_do_not_turn_command_failures_into_success():
  output = "CLUSTER_LOG_WARNING: " + "x" * 2000
  dashboard = load_helper("openpilot/selfdrive/addon/dashboard.py", "_get_log_upload_warning", {"Path": Path})
  assert len(dashboard("log_upload.sh", output)) == 600
  community = load_helper("openpilot/selfdrive/ui/layouts/settings/community.py", "execute_log_upload",
                          {"subprocess": SimpleNamespace(run=Mock(return_value=subprocess.CompletedProcess([], 1, output, "")))})
  code, warning = community("cluster_debug.log")
  assert code == 1 and len(warning) == 600
