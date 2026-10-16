import faulthandler
import os
from pathlib import Path
import threading
import time
import traceback


LOG_FILE = "/data/log/cluster_debug.log"
CRASH_LOG_FILE = "/data/log/cluster_crash.log"
MAX_CRASH_LOG_BYTES = 256 * 1024
MAX_CRASH_RECORD_BYTES = 64 * 1024
MAX_PREVIOUS_CRASH_BYTES = 8 * 1024

_log_lock = threading.Lock()
_log_handle = None
_crash_lock = threading.Lock()
_crash_handle = None
_fault_handler_enabled = False


def _bounded_text(text, limit):
  encoded = text.encode("utf-8", errors="replace")
  if len(encoded) <= limit:
    return text
  heading = encoded.split(b"\n", 1)[0][:min(1024, limit // 4)]
  prefix = heading + b"\n[Earlier traceback lines truncated]\n"
  return prefix.decode("utf-8", errors="ignore") + encoded[-(limit - len(prefix)):].decode("utf-8", errors="ignore")


def _close_crash_log_unlocked():
  global _crash_handle, _fault_handler_enabled
  if _fault_handler_enabled:
    faulthandler.disable()
    _fault_handler_enabled = False
  if _crash_handle is not None:
    try:
      _crash_handle.close()
    except OSError:
      pass
    _crash_handle = None


def _write_crash_record(record):
  """Keep two bounded files; faulthandler also writes to the active descriptor."""
  global _crash_handle, _fault_handler_enabled
  payload = _bounded_text(record, MAX_CRASH_RECORD_BYTES).encode("utf-8", errors="replace")
  try:
    with _crash_lock:
      path = Path(CRASH_LOG_FILE)
      if path.exists() and path.stat().st_size + len(payload) > MAX_CRASH_LOG_BYTES:
        _close_crash_log_unlocked()
        path.replace(path.with_name(path.name + ".1"))
      if _crash_handle is None:
        _crash_handle = path.open("ab", buffering=0)
        try:
          faulthandler.enable(file=_crash_handle, all_threads=True)
          _fault_handler_enabled = True
        except (OSError, RuntimeError):
          pass
      _crash_handle.write(payload)
  except OSError:
    with _crash_lock:
      _close_crash_log_unlocked()


def initialize_crash_log():
  # Standard-library only: capture failures before core/native imports and the
  # process-local native-pool bootstrap. A normal exec closes this descriptor.
  _write_crash_record(
    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [CLUSTER_PROCESS_START] pid={os.getpid()} "
    + f"native_bootstrap={os.environ.get('CLUSTER_NATIVE_BOOTSTRAP', '0')}\n"
  )


def record_crash(error, stage):
  detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
  record = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [CLUSTER_CRASH] stage={stage}\n{detail}"
  _write_crash_record(record)
  # cluster_main may already have closed the session log in its finally block.
  flog(_bounded_text(record, MAX_CRASH_RECORD_BYTES).rstrip())


def close_crash_log():
  with _crash_lock:
    _close_crash_log_unlocked()


def _previous_crash_excerpt():
  records = []
  for filename in (CRASH_LOG_FILE + ".1", CRASH_LOG_FILE):
    try:
      with open(filename, "rb") as handle:
        handle.seek(max(0, os.fstat(handle.fileno()).st_size - MAX_CRASH_LOG_BYTES))
        records.append(handle.read(MAX_CRASH_LOG_BYTES).decode("utf-8", errors="replace"))
    except OSError:
      continue
  history = "".join(records)
  crash_at = max(history.rfind("[CLUSTER_CRASH]"), history.rfind("Fatal Python error:"))
  if crash_at < 0:
    return ""
  crash_at = history.rfind("\n", 0, crash_at) + 1
  next_session = history.find("[CLUSTER_PROCESS_START]", crash_at)
  if next_session >= 0:
    next_session = history.rfind("\n", crash_at, next_session)
  return _bounded_text(history[crash_at:next_session if next_session >= 0 else None].rstrip(), MAX_PREVIOUS_CRASH_BYTES)


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
  previous_crash = _previous_crash_excerpt()
  try:
    with _log_lock:
      _close_log_unlocked()
      _log_handle = open(LOG_FILE, "w", buffering=1)
      _log_handle.write(f"=== Cluster Session Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
      if previous_crash:
        _log_handle.write(f"=== Most recent previous cluster failure ===\n{previous_crash}\n=== End previous failure ===\n")
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
