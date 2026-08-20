import threading
import time


LOG_FILE = "/data/cluster_debug.log"

_log_lock = threading.Lock()


def initialize_log():
  try:
    with _log_lock, open(LOG_FILE, "w") as f:
      f.write(f"=== Cluster Session Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
  except OSError:
    pass


def flog(msg):
  try:
    with _log_lock, open(LOG_FILE, "a") as f:
      f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
  except OSError:
    pass
