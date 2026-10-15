"""Prepare isolated, optional addon dependencies before importing their users.

This module deliberately imports only the standard library. Packages in the
core environment are reused only when their pinned versions also import.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time


class DependencyError(RuntimeError):
  pass


class DependencyInstallCancelled(DependencyError):
  pass


REQUIREMENTS_DIR = Path(__file__).resolve().parent
GROUP_MODULES = {
  "dashboard": ("nicegui", "ansi2html"),
  "cluster-jpeg": ("cv2", "PIL"),
  "cluster-usb": ("usb.core", "usb.util"),
  "cluster-h264": ("libdatachannel",),
}
INSTALL_TIMEOUT = 180
PROBE_TIMEOUT = 20
LOCK_TIMEOUT = INSTALL_TIMEOUT + PROBE_TIMEOUT + 10
MARKER = ".requirements_installed"


def default_deps_root():
  override = os.environ.get("ADDON_DEPS_ROOT")
  if override:
    return Path(override).expanduser()
  if Path("/AGNOS").exists():
    return Path("/data/addon_deps")
  cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
  return cache / "openpilot" / "addon_deps" / (sys.implementation.cache_tag or "python")


def groups_for_cluster(transport, codec="jpeg"):
  transport = str(transport or "network").strip().lower()
  if transport == "usb":
    return ("cluster-jpeg", "cluster-usb")
  if transport != "network":
    raise DependencyError(f"Unsupported cluster display transport: {transport!r}")
  return ("cluster-h264",) if str(codec).strip().lower() == "h264" else ("cluster-jpeg",)


def select_groups(params_root="/data/params/d", blocked=None):
  """Read raw Params without loading compiled core modules before the build."""
  blocked = set((os.environ.get("BLOCK", "") if blocked is None else blocked).split(","))
  blocked = {name.strip() for name in blocked}
  params_root = Path(params_root)

  def read(name, default=""):
    try:
      return (params_root / name).read_text(encoding="utf-8").strip() or default
    except FileNotFoundError:
      return default
    except OSError as error:
      raise DependencyError(f"Cannot read addon setting {name}: {error}") from error

  groups = [] if "dashboard" in blocked else ["dashboard"]
  if "cluster" not in blocked and read("ClusterEnable") == "1":
    groups.extend(groups_for_cluster(read("ClusterDisplayTransport", "network"), read("ClusterNetworkCodec", "jpeg")))
  return tuple(groups)


def _groups(groups):
  result = tuple(dict.fromkeys(groups))
  unknown = set(result) - GROUP_MODULES.keys()
  if unknown:
    raise DependencyError(f"Unknown addon dependency groups: {', '.join(sorted(unknown))}")
  return result


def _requirements(group):
  path = REQUIREMENTS_DIR / f"requirements-{group}.txt"
  try:
    content = path.read_bytes()
  except OSError as error:
    raise DependencyError(f"Cannot read {path}: {error}") from error
  pins = []
  for line in content.decode("utf-8").splitlines():
    requirement = line.split("#", 1)[0].strip()
    if not requirement:
      continue
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", requirement)
    if match is None:
      raise DependencyError(f"Expected an exact package pin in {path}: {requirement!r}")
    pins.append(match.groups())
  if not pins:
    raise DependencyError(f"Empty addon requirements: {path}")
  return path, content, pins


def _python_abi():
  return "|".join((sys.implementation.name, sys.implementation.cache_tag or "", sys.version,
                   sysconfig.get_platform(), sysconfig.get_config_var("SOABI") or ""))


def _fingerprint(content):
  return hashlib.sha256(content + b"\0" + _python_abi().encode()).hexdigest()


_PROBE = """
import importlib, importlib.metadata, json, os, sys
pins, modules, root, overlay = json.loads(sys.argv[1])
root = os.path.realpath(root)
sys.path[:] = [p for p in sys.path if not (os.path.realpath(p) == root or os.path.realpath(p).startswith(root + os.sep))]
if overlay:
  sys.path.insert(0, overlay)
for name, version in pins:
  if importlib.metadata.version(name) != version:
    raise RuntimeError(name + ': pinned version unavailable')
for name in modules:
  importlib.import_module(name)
"""


def _probe(group, pins, root, overlay=None):
  payload = json.dumps((pins, GROUP_MODULES[group], str(root.resolve()), str(overlay.resolve()) if overlay is not None else None))
  try:
    result = subprocess.run([sys.executable, "-c", _PROBE, payload], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=PROBE_TIMEOUT, check=False)
    return result.returncode == 0
  except (OSError, subprocess.TimeoutExpired):
    return False


def _ready_location(group, root):
  _path, content, pins = _requirements(group)
  if _probe(group, pins, root):
    return True, None
  target = root / group
  try:
    valid_marker = (target / MARKER).read_text(encoding="ascii") == _fingerprint(content)
  except (OSError, UnicodeError):
    valid_marker = False
  if valid_marker and _probe(group, pins, root, target):
    return True, target
  return False, None


def groups_ready(groups, *, deps_root=None):
  root = Path(deps_root) if deps_root is not None else default_deps_root()
  return all(_ready_location(group, root)[0] for group in _groups(groups))


@contextmanager
def _group_lock(root, group, cancel_check=None):
  """Use kernel locks so a killed installer cannot leave a stale lock owner."""
  root.mkdir(parents=True, exist_ok=True)
  with (root / f".{group}.lock").open("a+b") as lock:
    if lock.seek(0, os.SEEK_END) == 0:
      lock.write(b"\0")
      lock.flush()
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
      if cancel_check is not None and cancel_check():
        raise DependencyInstallCancelled("Addon dependency installation postponed until offroad")
      try:
        if os.name == "nt":
          import msvcrt
          lock.seek(0)
          msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
          import fcntl
          fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except OSError as error:
        if time.monotonic() >= deadline:
          raise DependencyError(f"Timed out waiting for addon dependency group {group}") from error
        time.sleep(0.05)
    try:
      yield
    finally:
      if os.name == "nt":
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
      else:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _stop_installer(process):
  if process.poll() is not None:
    return
  try:
    if os.name == "posix":
      os.killpg(process.pid, signal.SIGTERM)
    else:
      process.terminate()
  except ProcessLookupError:
    return
  try:
    process.wait(timeout=3)
  except subprocess.TimeoutExpired:
    if os.name == "posix":
      os.killpg(process.pid, signal.SIGKILL)
    else:
      process.kill()
    process.wait(timeout=3)


def _installer_command(requirements, stage):
  try:
    pip_available = subprocess.run([sys.executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10, check=False).returncode == 0
  except (OSError, subprocess.TimeoutExpired):
    pip_available = False
  flags = ["--no-cache-dir", "--only-binary=:all:", "--target", str(stage), "-r", str(requirements)]
  if pip_available:
    return [sys.executable, "-m", "pip", "install", "--timeout", "15", "--retries", "1", *flags]
  uv = shutil.which("uv")
  if uv is not None:
    return [uv, "pip", "install", "--python", sys.executable, *flags]
  raise DependencyError(f"Neither pip nor uv is available for {sys.executable}")


def _run_installer(requirements, stage, cancel_check=None):
  if cancel_check is not None and cancel_check():
    raise DependencyInstallCancelled("Addon dependency installation postponed until offroad")
  command = _installer_command(requirements, stage)
  environment = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", TMPDIR=str(stage.parent),
                     UV_HTTP_TIMEOUT="15", UV_HTTP_RETRIES="1")
  process = None
  previous_handler = None

  def interrupted(signum, _frame):
    raise SystemExit(128 + signum)

  try:
    try:
      previous_handler = signal.signal(signal.SIGTERM, interrupted)
    except ValueError:  # Runtime preparation can run in a dashboard worker.
      pass
    if cancel_check is not None and cancel_check():
      raise DependencyInstallCancelled("Addon dependency installation postponed until offroad")
    process = subprocess.Popen(command, env=environment, start_new_session=os.name == "posix")
    deadline = time.monotonic() + INSTALL_TIMEOUT
    while True:
      if cancel_check is not None and cancel_check():
        raise DependencyInstallCancelled("Addon dependency installation postponed until offroad")
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise DependencyError(f"Addon dependency installation exceeded {INSTALL_TIMEOUT}s")
      try:
        code = process.wait(timeout=min(1, remaining))
        break
      except subprocess.TimeoutExpired:
        continue
    if code:
      raise DependencyError(f"Addon dependency installer exited with status {code}")
  except OSError as error:
    raise DependencyError(f"Cannot start addon dependency installer: {error}") from error
  finally:
    if process is not None:
      _stop_installer(process)
    if previous_handler is not None:
      signal.signal(signal.SIGTERM, previous_handler)


def _publish(stage, target):
  """Keep the previous complete group until its replacement passes probing."""
  backup = target.with_name(f".{target.name}.previous")
  if backup.exists():
    if target.exists():
      shutil.rmtree(backup)
    else:
      backup.replace(target)
  had_previous = target.exists()
  if had_previous:
    target.replace(backup)
  try:
    stage.replace(target)
  except BaseException:
    if had_previous:
      backup.replace(target)
    raise
  if had_previous:
    shutil.rmtree(backup)


def prepare_groups(groups, *, allow_install=True, deps_root=None, cancel_check=None):
  """Return True when ready; raise DependencyError on a preparation failure."""
  root = Path(deps_root) if deps_root is not None else default_deps_root()
  for group in _groups(groups):
    if _ready_location(group, root)[0]:
      continue
    if not allow_install:
      raise DependencyError(f"Addon dependency group {group} is unavailable; prepare it while offroad")
    try:
      with _group_lock(root, group, cancel_check):
        if _ready_location(group, root)[0]:
          continue
        requirements, content, pins = _requirements(group)
        print(f"[addon dependencies] Installing {group}...", flush=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{group}.staging-", dir=root))
        try:
          _run_installer(requirements, stage, cancel_check)
          if cancel_check is not None and cancel_check():
            raise DependencyInstallCancelled("Addon dependency installation postponed until offroad")
          if not _probe(group, pins, root, stage):
            raise DependencyError(f"Installed addon dependency group {group} failed its version/import check")
          (stage / MARKER).write_text(_fingerprint(content), encoding="ascii")
          _publish(stage, root / group)
        finally:
          if stage.exists():
            shutil.rmtree(stage)
    except OSError as error:
      raise DependencyError(f"Cannot prepare addon dependency group {group}: {error}") from error
  return True


def activate_groups(groups, *, deps_root=None):
  """Expose only requested groups, leaving manager/core and legacy data alone."""
  root = Path(deps_root) if deps_root is not None else default_deps_root()
  locations = []
  for group in _groups(groups):
    ready, location = _ready_location(group, root)
    if not ready:
      raise DependencyError(f"Addon dependency group {group} is unavailable")
    if location is not None:
      locations.append(str(location))
  group_paths = {str(root / group) for group in GROUP_MODULES}
  sys.path[:] = [path for path in sys.path if path not in group_paths]
  sys.path[:0] = locations


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("command", choices=("selected",), nargs="?", default="selected")
  parser.add_argument("--params-root", default="/data/params/d")
  parser.add_argument("--deps-root")
  parser.add_argument("--check", "--check-selected", action="store_true", help="Return 1 if selected groups need preparation")
  args = parser.parse_args(argv)
  try:
    groups = select_groups(args.params_root)
    if args.check:
      return 0 if groups_ready(groups, deps_root=args.deps_root) else 1
    prepare_groups(groups, deps_root=args.deps_root)
    return 0
  except DependencyError as error:
    print(f"[addon dependencies] {error}", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
  sys.exit(main())
