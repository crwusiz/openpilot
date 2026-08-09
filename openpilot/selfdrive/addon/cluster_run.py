#!/usr/bin/env python3
import locale
import os
import sys
import time
import traceback
from pathlib import Path

try:
    locale.setlocale(locale.LC_ALL, 'ko_KR.UTF-8')
except Exception:
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except Exception:
        try:
            locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
        except Exception:
            pass

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


def main():
  try:
    set_core_affinity(CLUSTER_CORES)
  except OSError as e:
    cloudlog.warning(f"Failed to set cluster CPU affinity to {CLUSTER_CORES}: {e}")

  cloudlog.info("Starting Cluster (Turing 9.2 inch Display) process...")
  time.sleep(2)

  try:
    from openpilot.selfdrive.addon.cluster.main import cluster_main

    cloudlog.info("Cluster main loop initialized and running.")
    cluster_main()

  except KeyboardInterrupt:
    cloudlog.info("Cluster process interrupted by user.")
  except Exception as e:
    cloudlog.error(f"Cluster crashed: {e}")
    traceback.print_exc()


if __name__ == "__main__":
  main()
