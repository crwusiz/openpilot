"""Extract only known modeld fallback errors from newly received error logs."""

import json
import math
import re


MAX_MODELD_RECORD_BYTES = 64 * 1024
MAX_MODELD_TRACEBACK_BYTES = 2 * 1024
MODELD_FAILURES = {
  "big model load failed": "load_exception",
  "big model load failed, cycling onroad to retry": "load_retry",
  "big model failed, fall back to small": "run_fallback",
}
EXCEPTION_LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_.]{0,127})(?::.*)?")


def _exception_fields(value):
  # SwagFormatter.formatException supplies this exact traceback header. Do
  # not copy arbitrary strings from an unrelated/malformed exc_info field.
  if not isinstance(value, str) or not value.startswith("Traceback (most recent call last):\n"):
    return {}
  encoded = value.encode("utf-8", errors="replace")
  truncated = len(encoded) > MAX_MODELD_TRACEBACK_BYTES
  if truncated:
    heading = b"Traceback (most recent call last):\n[Earlier traceback omitted]\n"
    encoded = heading + encoded[-(MAX_MODELD_TRACEBACK_BYTES - len(heading)):]
  result = {"traceback": encoded.decode("utf-8", errors="ignore"), "traceback_truncated": truncated}
  for line in reversed(value.splitlines()):
    match = EXCEPTION_LINE.fullmatch(line)
    if match and (":" in line or match[1].endswith(("Error", "Exception", "Fault", "Timeout"))):
      result["exception_type"] = match[1]
      break
  return result


def parse_modeld_failure(record):
  """Return bounded evidence, without treating a model fault as display load.

  errorLogMessage contains SwagFormatter JSON, not the file formatter's typed
  fields. The big-model loader logs from another thread, whose thread-local
  daemon may be absent; filename/module remain available in that case.
  """
  if not isinstance(record, str) or len(record) > MAX_MODELD_RECORD_BYTES or "big model" not in record:
    return None
  try:
    if len(record.encode("utf-8")) > MAX_MODELD_RECORD_BYTES:
      return None
    data = json.loads(record)
  except (ValueError, TypeError, RecursionError, UnicodeError):
    return None
  if not isinstance(data, dict) or not isinstance(data.get("msg"), str) or data["msg"] not in MODELD_FAILURES:
    return None
  context = data.get("ctx")
  if (data.get("filename") != "modeld.py" or data.get("module") != "modeld" or not isinstance(context, dict)
      or ("daemon" in context and context["daemon"] != "modeld")):
    return None
  result = {"event": "modeld_failure", "failure_kind": MODELD_FAILURES[data["msg"]], "message": data["msg"]}
  created = data.get("created")
  if type(created) in (int, float) and 0 <= created <= 1e12 and math.isfinite(created):
    result["created"] = created
  process = data.get("process")
  if type(process) is int and 0 < process < 1 << 31:
    result["process"] = process
  result.update(_exception_fields(data.get("exc_info")))
  return result
