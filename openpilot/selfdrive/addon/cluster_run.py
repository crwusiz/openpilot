#!/usr/bin/env python3
import json
import locale
import os
import sys
from pathlib import Path

BASEDIR = Path(__file__).resolve().parents[3]
if str(BASEDIR) not in sys.path:
  sys.path.insert(0, str(BASEDIR))

ADDON_PYTHONPATH = os.environ.get("ADDON_PYTHONPATH")
if ADDON_PYTHONPATH and ADDON_PYTHONPATH not in sys.path:
  sys.path.insert(0, ADDON_PYTHONPATH)

VENDOR_ROOT = Path(__file__).resolve().parent / "cluster" / "usb_display" / ".vendor" / "turing-smart-screen-python-main"
if str(VENDOR_ROOT) not in sys.path:
  sys.path.insert(0, str(VENDOR_ROOT))

CLUSTER_CORES = [0, 1, 2]


def configure_cluster_runtime() -> None:
  # These libraries are loaded by cluster.main after this function. Limit only
  # this auxiliary process, before native worker pools are initialized.
  for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
  if sys.platform == "linux" and "numpy" in sys.modules and os.environ.get("CLUSTER_NATIVE_BOOTSTRAP") != "1":
    # manager forks after importing NumPy. Replace only its cluster child, with
    # the same PID, so inherited native pools see the limits before loading.
    from openpilot.common.logging_extra import json_robust_dumps
    from openpilot.common.swaglog import cloudlog
    environment = dict(os.environ, CLUSTER_NATIVE_BOOTSTRAP="1",
                       CLUSTER_LOG_CONTEXT=json_robust_dumps(cloudlog.get_ctx()))
    os.execve(sys.executable, [sys.executable, str(Path(__file__).resolve())], environment)
  serialized_context = os.environ.pop("CLUSTER_LOG_CONTEXT", None)
  # swaglog -> logging_extra already imports NumPy, so even these common
  # modules must wait until after the process-local environment is prepared.
  from openpilot.common.realtime import drop_realtime
  from openpilot.common.swaglog import cloudlog
  if serialized_context is not None:
    try:
      context = json.loads(serialized_context)
      if not isinstance(context, dict):
        raise ValueError("Log context must be an object")
      cloudlog.bind_global(**context)
    except (TypeError, ValueError):
      cloudlog.warning("Cannot restore cluster log context: invalid metadata")
  if sys.platform == "linux":
    try:
      drop_realtime()
      current = os.getpriority(os.PRIO_PROCESS, 0)
      os.setpriority(os.PRIO_PROCESS, 0, max(current, 5))
    except OSError as e:
      cloudlog.warning(f"Failed to lower cluster scheduling priority: {e}")


def configure_cluster_locale() -> None:
  """Use a deterministic locale before loading the cluster and USB vendor code."""
  for candidate in ("C.UTF-8", "C"):
    try:
      locale.setlocale(locale.LC_ALL, candidate)
    except locale.Error:
      continue
    os.environ["LC_ALL"] = candidate
    os.environ["LC_CTYPE"] = candidate
    os.environ["LANG"] = candidate
    return


def main() -> None:
  configure_cluster_locale()
  configure_cluster_runtime()
  from openpilot.common.realtime import set_core_affinity
  from openpilot.common.swaglog import cloudlog
  from setproctitle import setproctitle

  setproctitle("openpilot.selfdrive.addon.cluster_run")
  cloudlog.bind(daemon="cluster")

  try:
    set_core_affinity(CLUSTER_CORES)
  except Exception as e:
    cloudlog.warning(f"Failed to set cluster CPU affinity to {CLUSTER_CORES}: {e}")

  cloudlog.info("Starting cluster display process...")

  try:
    from openpilot.selfdrive.addon.cluster.main import cluster_main

    cloudlog.info("Cluster main loop initialized and running.")
    cluster_main()

  except KeyboardInterrupt:
    cloudlog.info("Cluster process interrupted by user.")
  except Exception:
    cloudlog.exception("Cluster crashed")


if __name__ == "__main__":
  main()
