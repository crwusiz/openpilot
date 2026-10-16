"""Exercise boot-file edits without touching the host's fan or boot partition."""
import os
from pathlib import Path
import shutil
import struct
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fan_setup.sh"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is needed to check the fan installer")


@pytest.fixture
def fan_setup(tmp_path):
  boot = tmp_path / "boot with spaces"
  boot.mkdir()
  tree = tmp_path / "device tree"
  node = tree / "soc@3000000" / "pwm-fan"
  node.mkdir(parents=True)
  (node / "compatible").write_bytes(b"pwm-fan\x00")
  (node / "status").write_bytes(b"okay\x00")
  (node / "cooling-levels").write_bytes(struct.pack(">5I", 0, 50, 102, 170, 255))
  (boot / "orangepiEnv.txt").write_text("# original boot options\nrootdev=UUID=original\nuser_overlays=usb-host other\n",
                                      encoding="utf-8", newline="\n")
  script = tmp_path / "fan_setup.sh"
  script.write_text(SCRIPT.read_text(encoding="utf-8").replace("BOOT=/boot", 'BOOT="$TEST_BOOT_DIR"')
                    .replace("DEVICE_TREE=/sys/firmware/devicetree/base", 'DEVICE_TREE="$TEST_DEVICE_TREE"'), encoding="utf-8", newline="\n")
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  for name, body in {
    "id": "printf '0\\n'",
    "uname": "printf '%s\\n' \"${TEST_KERNEL:-6.6.98-sun60iw2}\"",
    "dtc": '''[[ ${TEST_DTC_FAIL:-} != 1 ]] || exit 1
output=''
while [[ $# -gt 1 ]]; do
  if [[ $1 == -o ]]; then output=$2; shift; fi
  shift
done
cp -- "$1" "$output"
''',
    "apt-get": "printf '%s\\n' \"$*\" >> apt.log; exit 1",
    "sync": '''if [[ ${TEST_SYNC_FAIL:-} == 1 ]]; then printf 'Injected sync failure\\n' >&2; exit 1; fi''',
    "install": '''if [[ ${TEST_WINDOWS_MODES:-} == 1 ]]; then
  arguments=()
  for argument in "$@"; do
    if [[ $argument == 700 ]]; then arguments+=(755); else arguments+=("$argument"); fi
  done
  exec /usr/bin/install "${arguments[@]}"
fi
exec /usr/bin/install "$@"''',
  }.items():
    mock = bin_dir / name
    mock.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    mock.chmod(0o755)

  def run(*args, **extra_env):
    environment = dict(os.environ, TEST_BOOT_DIR=boot.as_posix(), TEST_DEVICE_TREE=tree.as_posix(),
                       TEST_WINDOWS_MODES="1" if os.name == "nt" else "0", **extra_env)
    return subprocess.run([BASH, "-c", 'export PATH="$PWD/bin:/usr/bin:/bin"; exec bash fan_setup.sh "$@"', "fan-test", *args],
                          cwd=tmp_path, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)

  return run, boot, node


def test_real_soc_path_and_minimum_preserve_native_thermal_curve(fan_setup):
  run, boot, node = fan_setup
  original = (node / "cooling-levels").read_bytes()
  result = run("install")
  assert result.returncode == 0, result.stderr
  overlay = (boot / "overlay-user" / "cluster-fan-minimum.dtbo").read_text()
  assert 'target-path = "/soc@3000000/pwm-fan"' in overlay
  assert "cooling-levels = <153 153 153 170 255>" in overlay
  assert (node / "cooling-levels").read_bytes() == original  # Applies at the next boot, no live governor writes.
  assert (boot / "orangepiEnv.txt").read_text() == (
    "# original boot options\nrootdev=UUID=original\nuser_overlays=usb-host other cluster-fan-minimum\n")
  assert "Reboot required" in result.stdout
  assert not (boot.parent / "apt.log").exists()


@pytest.mark.parametrize("options", ["", "user_overlays=\n", "user_overlays=cluster-fan-minimum usb-host cluster-fan-minimum\n"])
def test_repeat_install_keeps_one_last_overlay_and_preserves_boot_options(fan_setup, options):
  run, boot, _ = fan_setup
  (boot / "orangepiEnv.txt").write_text("verbosity=1\n" + options, encoding="utf-8", newline="\n")
  first = run("install")
  assert first.returncode == 0, first.stderr
  first_config = (boot / "orangepiEnv.txt").read_bytes()
  second = run("install")
  assert second.returncode == 0, second.stderr
  assert (boot / "orangepiEnv.txt").read_bytes() == first_config
  assert first_config.count(b"cluster-fan-minimum") == 1
  assert first_config.startswith(b"verbosity=1\n")
  assert first_config.endswith(b"cluster-fan-minimum\n")


@pytest.mark.parametrize("levels", [(0, 200, 220, 255), (153, 153, 153, 170, 255)])
def test_existing_higher_cooling_steps_are_retained(fan_setup, levels):
  run, boot, node = fan_setup
  (node / "cooling-levels").write_bytes(struct.pack(">" + "I" * len(levels), *levels))
  result = run("install")
  assert result.returncode == 0, result.stderr
  expected = " ".join(str(max(153, value)) for value in levels)
  assert f"cooling-levels = <{expected}>" in (boot / "overlay-user" / "cluster-fan-minimum.dtbo").read_text()


@pytest.mark.parametrize("levels", [(0, 170, 102, 255), (0, 256), (0, 170), (255,)])
def test_invalid_curves_cannot_change_boot_configuration(fan_setup, levels):
  run, boot, node = fan_setup
  original = (boot / "orangepiEnv.txt").read_bytes()
  (node / "cooling-levels").write_bytes(struct.pack(">" + "I" * len(levels), *levels))
  result = run("install")
  assert result.returncode != 0
  assert (boot / "orangepiEnv.txt").read_bytes() == original
  assert not (boot / "overlay-user").exists()


@pytest.mark.parametrize("options", ["user_overlays=one\nuser_overlays=two\n", "user_overlays=unsafe;value\n", "user_overlays=*\n"])
def test_ambiguous_boot_syntax_is_preserved_and_reported(fan_setup, options):
  run, boot, _ = fan_setup
  (boot / "orangepiEnv.txt").write_text(options, encoding="utf-8", newline="\n")
  result = run("install")
  assert result.returncode != 0
  assert (boot / "orangepiEnv.txt").read_text() == options
  assert not (boot / "overlay-user").exists()


@pytest.mark.parametrize("failure", ["TEST_DTC_FAIL", "TEST_SYNC_FAIL"])
@pytest.mark.parametrize("existing_overlay", [False, True])
def test_compile_or_install_failure_restores_previous_boot_files(fan_setup, failure, existing_overlay):
  run, boot, _ = fan_setup
  overlay = boot / "overlay-user" / "cluster-fan-minimum.dtbo"
  if existing_overlay:
    overlay.parent.mkdir()
    overlay.write_bytes(b"previous compiled overlay")
  original = (boot / "orangepiEnv.txt").read_bytes()
  result = run("install", **{failure: "1"})
  assert result.returncode != 0
  if failure == "TEST_SYNC_FAIL":
    assert "Injected sync failure" in result.stderr
  assert (boot / "orangepiEnv.txt").read_bytes() == original
  if existing_overlay:
    assert overlay.read_bytes() == b"previous compiled overlay"
  else:
    assert not overlay.exists()


@pytest.mark.parametrize("had_boot_config", [False, True])
def test_saved_snapshot_restores_presence_and_bytes(fan_setup, had_boot_config):
  run, boot, _ = fan_setup
  env_file = boot / "orangepiEnv.txt"
  overlay = boot / "overlay-user" / "cluster-fan-minimum.dtbo"
  previous = env_file.read_bytes()
  if had_boot_config:
    overlay.parent.mkdir()
    overlay.write_bytes(b"saved overlay\x00")
  else:
    env_file.unlink()
  snapshot = run("snapshot", "saved fan state")
  assert snapshot.returncode == 0, snapshot.stderr
  overlay.parent.mkdir(exist_ok=True)
  overlay.write_bytes(b"new overlay")
  env_file.write_text("new boot configuration\n", encoding="utf-8")
  restored = run("restore", "saved fan state")
  assert restored.returncode == 0, restored.stderr
  assert env_file.exists() == had_boot_config
  assert overlay.exists() == had_boot_config
  if had_boot_config:
    assert env_file.read_bytes() == previous
    assert overlay.read_bytes() == b"saved overlay\x00"


def test_legacy_snapshot_is_a_noop_and_other_boards_are_untouched(fan_setup):
  run, boot, _ = fan_setup
  original = (boot / "orangepiEnv.txt").read_bytes()
  assert run("restore", "missing old snapshot").returncode == 0
  result = run("install", TEST_KERNEL="6.6-other-board")
  assert result.returncode == 0
  assert "Unsupported board" in result.stderr
  assert (boot / "orangepiEnv.txt").read_bytes() == original
  assert not (boot / "overlay-user").exists()
