#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

BASEDIR = Path(__file__).resolve().parents[3]
if str(BASEDIR) not in sys.path:
  sys.path.insert(0, str(BASEDIR))


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


def main() -> None:
  # Keep startup diagnostics available before imports that load native libraries.
  from openpilot.selfdrive.addon.cluster.cluster_logging import (
    close_crash_log,
    close_log,
    initialize_crash_log,
    record_crash,
  )

  initialize_crash_log()
  cloudlog = None
  stage = "runtime bootstrap"
  try:
    configure_cluster_runtime()
    stage = "core imports"
    from setproctitle import setproctitle

    from openpilot.common.realtime import set_core_affinity
    from openpilot.common.swaglog import cloudlog

    setproctitle("openpilot.selfdrive.addon.cluster_run")
    cloudlog.bind(daemon="cluster")
    set_core_affinity([0, 1, 2])

    cloudlog.info("Starting cluster display process...")
    stage = "cluster imports"
    from openpilot.selfdrive.addon.cluster.main import cluster_main

    stage = "cluster startup or main loop"
    cloudlog.info("Initializing cluster main loop.")
    cluster_main()
  except KeyboardInterrupt:
    if cloudlog is not None:
      cloudlog.info("Cluster process interrupted by user.")
  except Exception as error:
    record_crash(error, stage)
    if cloudlog is not None:
      cloudlog.exception("Cluster crashed")
    raise
  finally:
    close_log()
    close_crash_log()


if __name__ == "__main__":
  main()
