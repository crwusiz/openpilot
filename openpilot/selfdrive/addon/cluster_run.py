#!/usr/bin/env python3
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

VENDOR_ROOT = Path(__file__).resolve().parent / "cluster" / ".vendor" / "turing-smart-screen-python-main"
if str(VENDOR_ROOT) not in sys.path:
  sys.path.insert(0, str(VENDOR_ROOT))

from openpilot.common.swaglog import cloudlog
from openpilot.common.realtime import set_core_affinity

CLUSTER_CORES = [0, 1, 2]


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

  try:
    set_core_affinity(CLUSTER_CORES)
  except Exception as e:
    cloudlog.warning(f"Failed to set cluster CPU affinity to {CLUSTER_CORES}: {e}")

  cloudlog.info("Starting Cluster (Turing 9.2 inch Display) process...")

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
