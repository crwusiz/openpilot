import threading
import time


LOG_FILE = "/data/cluster_debug.log"

_log_lock = threading.Lock()
_log_handle = None


def _close_log_unlocked():
  global _log_handle
  if _log_handle is not None:
    try:
      _log_handle.close()
    except OSError:
      pass
    _log_handle = None


def initialize_log():
  global _log_handle
  try:
    with _log_lock:
      _close_log_unlocked()
      _log_handle = open(LOG_FILE, "w", buffering=1)
      _log_handle.write(f"=== Cluster Session Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
  except OSError:
    _log_handle = None


def flog(msg):
  global _log_handle
  try:
    with _log_lock:
      if _log_handle is None:
        _log_handle = open(LOG_FILE, "a", buffering=1)
      _log_handle.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
  except OSError:
    _close_log_unlocked()


def close_log():
  with _log_lock:
    _close_log_unlocked()
