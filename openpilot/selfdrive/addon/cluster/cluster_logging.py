import faulthandler
import os
from pathlib import Path
import signal
import sys
import threading
import time
import traceback


LOG_FILE = "/data/log/cluster_debug.log"
CRASH_LOG_FILE = "/data/log/cluster_crash.log"
MAX_CRASH_LOG_BYTES = 256 * 1024
MAX_CRASH_RECORD_BYTES = 64 * 1024
MAX_CONSOLE_CRASH_BYTES = 8 * 1024

_log_lock = threading.Lock()
_log_handle = None
_crash_lock = threading.Lock()
_crash_handle = None
_fault_handler_enabled = False

CONSOLE_TAGS = frozenset({
  "[CLUSTER_START]", "[CLUSTER_MODE]", "[CLUSTER_MAIN]", "[CLUSTER_EXIT]", "[CLUSTER_RESTART]",
  "[CLUSTER_PROCESS_START]", "[CLUSTER_USB_H264_STAGE]", "[CLUSTER_USB_H264_ENCODER]",
})


def console_log(message):
  """Flush diagnostics even when disk logging or swaglog is unavailable."""
  message = str(message).rstrip() + "\n"
  try:
    sys.stderr.write(message)
    sys.stderr.flush()
  except (OSError, ValueError, UnicodeError, AttributeError):
    try:
      os.write(2, message.encode("utf-8", errors="replace"))
    except OSError:
      pass


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
  record = (f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [CLUSTER_PROCESS_START] pid={os.getpid()} "
            + f"native_bootstrap={os.environ.get('CLUSTER_NATIVE_BOOTSTRAP', '0')}\n")
  console_log(record)
  _write_crash_record(record)


def record_crash(error, stage):
  detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
  record = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [CLUSTER_CRASH] stage={stage} pid={os.getpid()}\n{detail}"
  console_log(_bounded_text(record, MAX_CRASH_RECORD_BYTES))
  _write_crash_record(record)
  # cluster_main may already have closed the session log in its finally block.
  # Keep performance logs compact; the separate crash upload retains the stack.
  description = " ".join(_bounded_text(str(error), 1024).splitlines())
  flog(f"[CLUSTER_CRASH] stage={stage} pid={os.getpid()} | {type(error).__name__}: {description}"
       + f" | crash_log={CRASH_LOG_FILE}")


def _failure_for_pid(pid):
  history = ""
  for filename in (CRASH_LOG_FILE + ".1", CRASH_LOG_FILE):
    try:
      with open(filename, "rb") as handle:
        handle.seek(max(0, os.fstat(handle.fileno()).st_size - MAX_CRASH_LOG_BYTES))
        history += handle.read(MAX_CRASH_LOG_BYTES).decode("utf-8", errors="replace")
    except OSError:
      continue
  marker = f"[CLUSTER_PROCESS_START] pid={pid} "
  start = history.rfind(marker)
  if start < 0:
    return ""
  session = history[start + len(marker):].split("[CLUSTER_PROCESS_START]", 1)[0]
  failure = max(session.rfind("[CLUSTER_CRASH]"), session.rfind("Fatal Python error:"))
  return _bounded_text(session[failure:].rstrip(), MAX_CONSOLE_CRASH_BYTES) if failure >= 0 else ""


def record_process_exit(pid, exit_code, expected_stop=False):
  """Parent-side reporting also covers abort/segfault/SIGKILL without Python."""
  if exit_code < 0:
    try:
      reason = signal.Signals(-exit_code).name
    except ValueError:
      reason = f"signal_{-exit_code}"
  else:
    reason = "normal_exit" if exit_code == 0 else "error_exit"
  summary = (f"[CLUSTER_PROCESS_EXIT] pid={pid} exit_code={exit_code} reason={reason} "
             + f"manager_stop={int(expected_stop)} logs={LOG_FILE},{CRASH_LOG_FILE}")
  console_log(summary)
  abnormal = exit_code != 0 and not (expected_stop and exit_code in (-signal.SIGINT, -signal.SIGTERM))
  record = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {summary}\n"
  if abnormal:
    record += f"[CLUSTER_CRASH] stage=process exit | pid={pid} exit_code={exit_code} reason={reason}\n"
    failure = _failure_for_pid(pid)
    # Python exceptions were already printed by the child. Native fatal stacks
    # went directly to its persistent faulthandler descriptor; print them now.
    if "Fatal Python error:" in failure:
      console_log(failure)
  # Do not call _write_crash_record in the manager: that installs faulthandler
  # for the *current* process. Its persistent handler belongs only to the child.
  # The stack is already in the crash file. Append only exit metadata to each
  # log, independently, so a missing path does not hide the reason elsewhere.
  for filename, lock in ((CRASH_LOG_FILE, _crash_lock), (LOG_FILE, _log_lock)):
    try:
      path = Path(filename)
      # Native stack output can end mid-line; separate the manager's record.
      payload = (("\n" if filename == CRASH_LOG_FILE else "") + record).encode("utf-8", errors="replace")
      with lock:
        if filename == CRASH_LOG_FILE and path.exists() and path.stat().st_size + len(payload) > MAX_CRASH_LOG_BYTES:
          path.replace(path.with_name(path.name + ".1"))
        with path.open("ab", buffering=0) as handle:
          handle.write(payload)
    except OSError:
      pass


def close_crash_log():
  with _crash_lock:
    _close_crash_log_unlocked()


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
  tag = msg.partition("]")[0] + "]"
  if tag in CONSOLE_TAGS or (tag.startswith("[CLUSTER") and tag.endswith(("_ERROR]", "_WARN]", "_SUCCESS]"))):
    console_log(f"[{time.strftime('%H:%M:%S')}] {msg}")
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
