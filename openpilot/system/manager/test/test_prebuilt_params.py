import builtins
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[4]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")


@pytest.fixture
def params_check(monkeypatch, tmp_path):
  spec = importlib.util.spec_from_file_location("check_params_under_test", REPO / "openpilot/system/manager/check_params.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module, "BASEDIR", str(tmp_path))

  header = tmp_path / "openpilot/common/params_keys.h"
  header.parent.mkdir(parents=True)
  header.write_text('''inline static std::unordered_map<std::string, ParamKeyAttributes> keys = {
    {"CarParams", {PERSISTENT, BYTES}},
    //{"CommentedOut", {PERSISTENT, JSON}},
    {"Notifications", {PERSISTENT, JSON}},
};
''', encoding="utf-8")
  registry = SimpleNamespace(keys=[b"CarParams", b"Notifications"])
  params = ModuleType("openpilot.common.params")
  params.Params = lambda: SimpleNamespace(all_keys=lambda: registry.keys)
  monkeypatch.setitem(sys.modules, "openpilot.common.params", params)
  return module, registry, header


def test_matching_registry_ignores_commented_keys(params_check, capsys):
  module, _, _ = params_check
  assert module.params_are_current()
  assert capsys.readouterr().err == ""


def test_missing_notifications_invalidates_prebuilt(params_check, capsys):
  module, registry, _ = params_check
  registry.keys = [b"CarParams"]
  assert not module.params_are_current()
  assert "Notifications" in capsys.readouterr().err


def test_obsolete_binary_key_invalidates_prebuilt(params_check, capsys):
  module, registry, _ = params_check
  registry.keys.append(b"ObsoleteKey")
  assert not module.params_are_current()
  assert "ObsoleteKey" in capsys.readouterr().err


def test_missing_header_invalidates_prebuilt(params_check, capsys):
  module, _, header = params_check
  header.unlink()
  assert not module.params_are_current()
  assert capsys.readouterr().err


@pytest.mark.parametrize("error", [ModuleNotFoundError("missing params library"), OSError("cannot load params library")])
def test_unavailable_native_library_invalidates_prebuilt(params_check, monkeypatch, capsys, error):
  module, _, _ = params_check
  real_import = builtins.__import__

  def failing_import(name, *args, **kwargs):
    if name == "openpilot.common.params":
      raise error
    return real_import(name, *args, **kwargs)

  monkeypatch.setattr(builtins, "__import__", failing_import)
  assert not module.params_are_current()
  assert str(error) in capsys.readouterr().err


@pytest.mark.skipif(not BASH, reason="Bash is required to check prebuilt startup recovery")
@pytest.mark.parametrize("prebuilt,check_status,build_status,expected_status,expected_calls", [
  (True, 0, 0, 0, ["CHECK ./check_params.py", "MANAGER"]),
  (True, 1, 0, 0, ["CHECK ./check_params.py", "BUILD", "MANAGER"]),
  (False, 1, 0, 0, ["BUILD", "MANAGER"]),
  (True, 1, 7, 1, ["CHECK ./check_params.py", "BUILD"]),
  (False, 1, 7, 1, ["BUILD"]),
])
def test_launcher_recovers_stale_prebuilt_and_stops_on_failed_build(tmp_path, prebuilt, check_status, build_status,
                                                                 expected_status, expected_calls):
  source = (REPO / "launch_chffrplus.sh").read_text(encoding="utf-8")
  fragment = source.split("# start manager", 1)[1].split("./manager.py", 1)[0] + "./manager.py\n"
  checkout = tmp_path / "checkout with spaces"
  manager_dir = checkout / "openpilot/system/manager"
  manager_dir.mkdir(parents=True)
  if prebuilt:
    (checkout / "prebuilt").touch()
  for filename, command in (("build.py", 'printf "BUILD\\n" >> "$MOCK_LOG"\nexit "$BUILD_STATUS"'),
                            ("manager.py", 'printf "MANAGER\\n" >> "$MOCK_LOG"')):
    script = manager_dir / filename
    script.write_text("#!/usr/bin/env bash\n" + command + "\n", encoding="utf-8", newline="\n")
    script.chmod(0o755)
  harness = tmp_path / "launch.sh"
  harness.write_text('''DIR="$PWD/checkout with spaces"
cd "$DIR" || exit 1
python3() {
  printf 'CHECK %s\\n' "$*" >> "$MOCK_LOG"
  return "$CHECK_STATUS"
}
''' + fragment, encoding="utf-8", newline="\n")
  log = tmp_path / "calls.log"
  result = subprocess.run([BASH, str(harness)], cwd=tmp_path, capture_output=True, text=True, timeout=15,
                          env=dict(os.environ, MOCK_LOG=str(log), CHECK_STATUS=str(check_status), BUILD_STATUS=str(build_status)))
  assert result.returncode == expected_status, result.stderr
  assert log.read_text().splitlines() == expected_calls
  assert (checkout / "prebuilt").exists() == (prebuilt and check_status == 0)
