import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/incremental.sh"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is required")


def run_helper(mode, stage, target):
  return subprocess.run([BASH, str(SCRIPT).replace("\\", "/"), mode, stage.name, target.name],
                        cwd=stage.parent, capture_output=True, text=True, timeout=10, check=False)


@pytest.fixture
def package(tmp_path):
  target, stage = tmp_path / "installed files", tmp_path / "staged files"
  stage.mkdir()
  files = {"hdmi_display.py": b"old code", "assets/fonts/NotoSansKR-Bold.ttf": b"font\x00\r\n"}
  for name, data in files.items():
    path = target / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
  files["hdmi_display.py"] = b"new code"
  files["new_module.py"] = b"new module"
  (stage / "SHA256SUMS").write_text("".join(
    f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items()
  ), newline="\n")
  return stage, target, files


def test_hash_comparison_reuses_only_identical_files_and_hydrates_full_package(package):
  stage, target, files = package
  result = run_helper("plan", stage, target)
  assert result.returncode == 0, result.stderr
  assert result.stdout.splitlines() == ["assets/fonts/NotoSansKR-Bold.ttf"]
  assert list(stage.iterdir()) == [stage / "SHA256SUMS"]
  for name in ("hdmi_display.py", "new_module.py"):
    (stage / name).write_bytes(files[name])
  result = run_helper("hydrate", stage, target)
  assert result.returncode == 0, result.stderr
  assert all((stage / name).read_bytes() == data for name, data in files.items())
  assert (target / "hdmi_display.py").read_bytes() == b"old code"
  assert not (target / "new_module.py").exists()


def test_hydration_rejects_live_changes_after_comparison(package):
  stage, target, files = package
  assert run_helper("plan", stage, target).returncode == 0
  for name in ("hdmi_display.py", "new_module.py"):
    (stage / name).write_bytes(files[name])
  (target / "assets/fonts/NotoSansKR-Bold.ttf").write_bytes(b"changed after comparison")
  result = run_helper("hydrate", stage, target)
  assert result.returncode != 0 and "changed during staging" in result.stderr


@pytest.mark.parametrize("entry", ["../outside.py", "scripts/../../outside.sh", "assets/fonts/unknown.ttf\ninvalid"])
def test_invalid_manifest_paths_cannot_escape_staging(package, entry):
  stage, target, _ = package
  (stage / "SHA256SUMS").write_text("0" * 64 + "  " + entry + "\n", newline="\n")
  assert run_helper("plan", stage, target).returncode != 0


def test_installed_symlink_is_not_reused(package):
  stage, target, _ = package
  path = target / "hdmi_display.py"
  path.unlink()
  try:
    path.symlink_to(stage / "SHA256SUMS")
  except OSError:
    pytest.skip("Symlinks unavailable")
  assert run_helper("plan", stage, target).returncode != 0
