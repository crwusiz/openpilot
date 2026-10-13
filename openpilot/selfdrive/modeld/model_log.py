"""Local, best-effort diagnostics for model initialization and fallback."""
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import time
import uuid


class _ModelLogHandler(RotatingFileHandler):
  def handleError(self, record):
    # Let the caller disable logging and report a write failure only once.
    raise sys.exc_info()[1]


class ModelLoadLogger:
  def __init__(self, path: Path = Path("/data/log/model_debug.log")):
    self.session = uuid.uuid4().hex[:12]
    self.started = time.monotonic()
    self._lock = threading.Lock()
    self._stages: dict[str, list[tuple[str, float]]] = {}
    self._handler = None
    self._reported_failure = False
    try:
      path = Path(path)
      path.parent.mkdir(parents=True, exist_ok=True)
      self._handler = _ModelLogHandler(path, mode="a", maxBytes=2 * 1024 * 1024, backupCount=1, encoding="utf-8")
      self._handler.setFormatter(logging.Formatter("%(message)s"))
    except Exception as error:
      self._report_failure(error)

  def _report_failure(self, error):
    with self._lock:
      if self._reported_failure:
        return
      self._reported_failure = True
    try:
      sys.stderr.write(f"model diagnostics logging disabled: {error}\n")
    except Exception:
      pass

  def event(self, name: str, *, exc_info: bool = False, **fields) -> None:
    handler = self._handler
    if handler is None:
      return
    try:
      exception_info = sys.exc_info() if exc_info else None
      data = {**fields, "event": name, "timestamp": datetime.now(UTC).isoformat(),
              "pid": os.getpid(), "session": self.session, "thread": threading.current_thread().name,
              "elapsed_s": round(time.monotonic() - self.started, 3)}
      if exception_info is not None and exception_info[1] is not None:
        data.update(exception_type=type(exception_info[1]).__name__, exception=str(exception_info[1]))
      record = logging.LogRecord("model_load", logging.INFO, "", 0, json.dumps(data, default=str), (),
                                 exception_info)
      handler.handle(record)  # The handler lock also protects rotation and flushes.
    except Exception as error:
      self._handler = None
      self._report_failure(error)
      try:
        handler.close()
      except Exception:
        pass

  @contextmanager
  def stage(self, name: str, **fields):
    thread_name = threading.current_thread().name
    started = time.monotonic()
    with self._lock:
      self._stages.setdefault(thread_name, []).append((name, started))
    self.event("stage_start", **{**fields, "stage": name})
    try:
      yield
    except BaseException:
      self.event("stage_failed", exc_info=True, **{**fields, "stage": name, "stage_elapsed_s": time.monotonic() - started})
      raise
    else:
      self.event("stage_done", **{**fields, "stage": name, "stage_elapsed_s": time.monotonic() - started})
    finally:
      with self._lock:
        self._stages[thread_name].pop()
        if not self._stages[thread_name]:
          del self._stages[thread_name]

  def status(self, thread_name: str) -> dict:
    with self._lock:
      stack = list(self._stages.get(thread_name, ()))
    return {"stage": stack[-1][0] if stack else "idle",
            "stage_elapsed_s": round(time.monotonic() - stack[-1][1], 3) if stack else 0.0,
            "stage_stack": [name for name, _ in stack]}

  def close(self) -> None:
    handler, self._handler = self._handler, None
    if handler is not None:
      try:
        handler.close()
      except Exception as error:
        self._report_failure(error)
