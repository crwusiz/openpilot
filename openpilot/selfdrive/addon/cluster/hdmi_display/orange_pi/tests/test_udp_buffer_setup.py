"""Run the real UDP setup transaction against sandboxed proc/unit files."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "udp_buffer_setup.sh"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is needed for the receiver setup test")


@pytest.fixture
def udp_setup(tmp_path):
  maximum = tmp_path / "rmem_max"
  maximum.write_text("212992\n", encoding="ascii")
  default = tmp_path / "rmem_default"
  default.write_text("212992\n", encoding="ascii")
  unit = tmp_path / "unit dropins" / "udp-buffer.conf"
  script = tmp_path / "udp_buffer_setup.sh"
  script.write_text(SCRIPT.read_text(encoding="utf-8")
                    .replace("MAX_FILE=/proc/sys/net/core/rmem_max", 'MAX_FILE="$TEST_UDP_MAX"')
                    .replace("UNIT_CONF=/etc/systemd/system/cluster-hdmi.service.d/udp-buffer.conf", 'UNIT_CONF="$TEST_UDP_CONF"'),
                    encoding="utf-8", newline="\n")
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  for name, body in {
    "id": 'printf "%s\\n" "${TEST_UID:-0}"',
    "install": '''if [[ ${TEST_WINDOWS_MODES:-} == 1 ]]; then
  arguments=()
  for argument in "$@"; do
    if [[ "$argument" == 700 ]]; then arguments+=(755); else arguments+=("$argument"); fi
  done
  exec /usr/bin/install "${arguments[@]}"
fi
exec /usr/bin/install "$@"''',
    "mv": '''/usr/bin/mv "$@" || exit $?
if [[ ${TEST_INTERRUPT:-} == 1 && "${!#}" == "$TEST_UDP_CONF" ]]; then kill -TERM "$PPID"; fi''',
  }.items():
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
  def run(*args, **extra):
    env = dict(os.environ, TEST_UDP_MAX=maximum.as_posix(), TEST_UDP_CONF=unit.as_posix(),
               TEST_WINDOWS_MODES="1" if os.name == "nt" else "0", **extra)
    return subprocess.run([BASH, "-c", 'export PATH="$PWD/bin:/usr/bin:/bin"; exec bash udp_buffer_setup.sh "$@"', "udp-test", *args],
                          cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
  return run, maximum, unit, default


@pytest.mark.parametrize("initial,expected", [(212992, 1048576), (1048576, 1048576), (8388608, 8388608)])
def test_install_sets_only_ceiling_and_persists_receiver_boot_guard(udp_setup, initial, expected):
  run, maximum, unit, default = udp_setup
  maximum.write_text(f"{initial}\n", encoding="ascii")
  result = run("install")
  assert result.returncode == 0, result.stderr
  assert maximum.read_text() == f"{expected}\n" and default.read_text() == "212992\n"
  guard = unit.read_text()
  assert "ExecStartPre=-+/bin/bash /opt/cluster-receiver/scripts/udp_buffer_setup.sh apply" in guard
  assert "rmem_default" not in guard and "CAP_NET_ADMIN" not in guard
  # Repeated install must preserve bytes and higher values.
  result = run("install")
  assert result.returncode == 0, result.stderr
  assert unit.read_text() == guard and maximum.read_text() == f"{expected}\n"
  # Simulate a new boot: the persistent service guard restores the floor.
  maximum.write_text("212992\n", encoding="ascii")
  assert run("apply").returncode == 0
  assert maximum.read_text() == "1048576\n" and unit.read_text() == guard


def test_apply_preserves_higher_runtime_cap_without_writing_configuration(udp_setup):
  run, maximum, unit, _default = udp_setup
  maximum.write_text("4194304\n", encoding="ascii")
  result = run("apply")
  assert result.returncode == 0, result.stderr
  assert maximum.read_text() == "4194304\n" and not unit.exists()


def test_snapshot_restore_and_reverse_rollback_track_runtime_and_dropin(udp_setup):
  run, maximum, unit, _default = udp_setup
  assert run("snapshot", "before apply").returncode == 0
  assert run("install", "before apply").returncode == 0
  guard = unit.read_bytes()
  assert run("snapshot", "before rollback").returncode == 0
  result = run("restore", "before apply", "before rollback")
  assert result.returncode == 0, result.stderr
  assert not unit.exists() and maximum.read_text() == "212992\n"
  # This snapshot must recover a failed/manual rollback as well as redo it.
  result = run("restore", "before rollback")
  assert result.returncode == 0, result.stderr
  assert unit.read_bytes() == guard and maximum.read_text() == "1048576\n"


@pytest.mark.parametrize("change", ["higher_cap", "lower_cap", "edited_config", "deleted_config"])
def test_restore_preserves_external_runtime_or_configuration_edits(udp_setup, change):
  run, maximum, unit, _default = udp_setup
  assert run("snapshot", "previous").returncode == 0
  assert run("install", "previous").returncode == 0
  if change == "higher_cap":
    maximum.write_text("8388608\n", encoding="ascii")
  elif change == "lower_cap":
    maximum.write_text("524288\n", encoding="ascii")
  elif change == "edited_config":
    unit.write_text("# user edited receiver configuration\n", encoding="ascii")
  else:
    unit.unlink()
  result = run("restore", "previous")
  assert result.returncode == 0, result.stderr
  if change in ("higher_cap", "lower_cap"):
    assert maximum.read_text() == ("8388608\n" if change == "higher_cap" else "524288\n")
    assert not unit.exists()
  else:
    assert maximum.read_text() == "1048576\n"
    assert unit.exists() == (change == "edited_config")
    if unit.exists():
      assert unit.read_text() == "# user edited receiver configuration\n"


def test_install_rejects_external_dropin_without_overwriting_or_changing_runtime(udp_setup):
  run, maximum, unit, _default = udp_setup
  unit.parent.mkdir()
  unit.write_text("[Service]\nUser=custom\n", encoding="ascii")
  result = run("install")
  assert result.returncode != 0
  assert unit.read_text() == "[Service]\nUser=custom\n" and maximum.read_text() == "212992\n"


def test_signal_after_config_rename_restores_partial_install(udp_setup):
  run, maximum, unit, _default = udp_setup
  result = run("install", TEST_INTERRUPT="1")
  assert result.returncode != 0
  assert not unit.exists() and maximum.read_text() == "212992\n"


@pytest.mark.parametrize("value", ["0", "-1", "not a number", "2147483648"])
def test_invalid_kernel_value_and_non_root_are_non_mutating(udp_setup, value):
  run, maximum, unit, _default = udp_setup
  maximum.write_text(value + "\n", encoding="ascii")
  result = run("install")
  assert result.returncode != 0 and not unit.exists()
  assert maximum.read_text() == value + "\n"
  assert run("apply", TEST_UID="1000").returncode != 0


def test_unapplied_and_legacy_snapshots_are_noops(udp_setup):
  run, maximum, unit, _default = udp_setup
  assert run("restore", "old release without snapshot").returncode == 0
  assert run("snapshot", "never applied").returncode == 0
  maximum.write_text("4194304\n", encoding="ascii")
  assert run("restore", "never applied").returncode == 0
  assert maximum.read_text() == "4194304\n" and not unit.exists()
