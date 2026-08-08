#!/usr/bin/env python3
import hashlib
import subprocess
import sys
import time
import traceback
import locale
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

VENDOR_ROOT = Path(__file__).resolve().parent / "cluster" / ".vendor" / "turing-smart-screen-python-main"
if str(VENDOR_ROOT) not in sys.path:
  sys.path.insert(0, str(VENDOR_ROOT))

from openpilot.common.swaglog import cloudlog
from openpilot.common.realtime import set_core_affinity

REQUIREMENTS_FILE = Path(__file__).resolve().parent / "requirements.txt"
REQUIREMENTS_MARKER = Path(__file__).resolve().parent / ".requirements_installed"
CLUSTER_CORES = [0, 1, 2]


def ensure_requirements() -> None:
  if not REQUIREMENTS_FILE.exists():
    cloudlog.warning(f"requirements.txt not found at {REQUIREMENTS_FILE}, skipping dependency install.")
    return

  req_hash = hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()
  if REQUIREMENTS_MARKER.exists() and REQUIREMENTS_MARKER.read_text().strip() == req_hash:
    return

  cloudlog.info(f"Installing cluster addon requirements from {REQUIREMENTS_FILE}...")
  try:
    subprocess.run(
      [sys.executable, "-m", "pip", "install", "--break-system-packages", "-r", str(REQUIREMENTS_FILE)],
      check=True,
      capture_output=True,
      text=True,
    )
    REQUIREMENTS_MARKER.write_text(req_hash)
    cloudlog.info("Cluster addon requirements installed successfully.")
  except subprocess.CalledProcessError as e:
    cloudlog.error(f"Failed to install cluster addon requirements: {e.stderr}")


def main():
  try:
    # Keep cluster rendering, camera conversion, and USB output off the
    # cores reserved for driving and model processes.
    set_core_affinity(CLUSTER_CORES)
  except OSError as e:
    cloudlog.warning(f"Failed to set cluster CPU affinity to {CLUSTER_CORES}: {e}")

  cloudlog.info("Starting Cluster (Turing 9.2 inch Display) process...")
  ensure_requirements()
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
