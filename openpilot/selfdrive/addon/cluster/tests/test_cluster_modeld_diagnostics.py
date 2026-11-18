import json
import logging
import sys
from types import SimpleNamespace

import pytest

from openpilot.common.logging_extra import SwagFormatter
from openpilot.selfdrive.addon.cluster.cluster_health_guard import ClusterHealthGuard
from openpilot.selfdrive.addon.cluster.cluster_modeld_diagnostics import (
  MAX_MODELD_RECORD_BYTES, MAX_MODELD_TRACEBACK_BYTES, MODELD_FAILURES, parse_modeld_failure,
)


def modeld_record(message="big model failed, fall back to small", **fields):
  return json.dumps({"msg": message, "filename": "modeld.py", "module": "modeld", "ctx": {"daemon": "modeld"},
                     "process": 52780, "created": 1794967714.5, **fields}, ensure_ascii=False)


@pytest.mark.parametrize("message,kind", MODELD_FAILURES.items())
def test_known_failures_preserve_only_fixed_message_origin_and_valid_identity(message, kind):
  parsed = parse_modeld_failure(modeld_record(message, pathname="private path", host="private host", threadName="private thread"))
  assert parsed == {"event": "modeld_failure", "failure_kind": kind, "message": message,
                    "process": 52780, "created": 1794967714.5}


@pytest.mark.parametrize("context", [{}, {"daemon": "modeld"}, {"private": "do not copy"}])
def test_loader_thread_without_thread_local_daemon_is_still_identified_by_source(context):
  event = parse_modeld_failure(modeld_record("big model load failed", ctx=context))
  assert event["failure_kind"] == "load_exception" and "ctx" not in event and "private" not in str(event)


@pytest.mark.parametrize("fields", [
  {"filename": "other.py"}, {"module": "other"}, {"ctx": {"daemon": "manager"}}, {"ctx": "modeld"}, {"ctx": None},
  {"filename": "modeld.py\nprivate"}, {"msg": {"event": "big model load failed"}}, {"msg": "big model load failed later"},
])
def test_unknown_message_or_foreign_origin_is_never_copied(fields):
  assert parse_modeld_failure(modeld_record(**fields)) is None


@pytest.mark.parametrize("record", [None, b"big model load failed", "broken big model", "[] big model", "42", "{}",
                                   '{"msg":"big model load failed", "bad": NaN',
                                   pytest.param("big model" + "x" * MAX_MODELD_RECORD_BYTES, id="oversized")])
def test_invalid_json_and_non_string_records_are_ignored(record):
  assert parse_modeld_failure(record) is None


def test_raw_limit_is_utf8_bytes_and_deep_json_does_not_escape_the_parser():
  oversized = modeld_record(padding="한" * (MAX_MODELD_RECORD_BYTES // 3))
  assert len(oversized) < MAX_MODELD_RECORD_BYTES and len(oversized.encode()) > MAX_MODELD_RECORD_BYTES
  assert parse_modeld_failure(oversized) is None
  deep = '{"msg":"big model load failed", "deep":' + "[" * 2000 + "0" + "]" * 2000 + "}"
  assert parse_modeld_failure(deep) is None
  assert parse_modeld_failure(modeld_record(padding="\ud800")) is None


@pytest.mark.parametrize("field,value", [
  ("created", True), ("created", -1), ("created", float("nan")), ("created", float("inf")),
  ("created", "1794967714"), ("created", 10 ** 100), ("process", True), ("process", 0),
  ("process", -1), ("process", 52780.0), ("process", 1 << 31), ("process", "52780"),
])
def test_unavailable_or_malformed_numeric_metadata_is_omitted(field, value):
  event = parse_modeld_failure(modeld_record(**{field: value}))
  assert event["event"] == "modeld_failure" and field not in event


def test_actual_swag_formatter_exception_record_keeps_the_failure_class_and_traceback():
  try:
    raise RuntimeError("GPU wait timed out")
  except RuntimeError:
    record = logging.LogRecord("swaglog", logging.ERROR, "/data/openpilot/selfdrive/modeld/modeld.py", 442,
                               "big model failed, fall back to small", (), sys.exc_info())
  formatter = SwagFormatter(SimpleNamespace(get_ctx=lambda: {"daemon": "modeld", "private": "do not copy"}))
  event = parse_modeld_failure(formatter.format(record))
  assert event["exception_type"] == "RuntimeError" and "GPU wait timed out" in event["traceback"]
  assert event["traceback"].startswith("Traceback (most recent call last):\n")
  assert not event["traceback_truncated"] and "ctx" not in event


def test_long_chained_traceback_retains_utf8_failure_tail_with_an_explicit_limit():
  trace = "Traceback (most recent call last):\n" + "  old frame 한글\n" * 1500 + "RuntimeError: final GPU failure 한글\n"
  event = parse_modeld_failure(modeld_record(exc_info=trace))
  assert event["exception_type"] == "RuntimeError" and event["traceback_truncated"]
  assert "Earlier traceback omitted" in event["traceback"]
  assert event["traceback"].endswith("RuntimeError: final GPU failure 한글\n")
  assert len(event["traceback"].encode()) <= MAX_MODELD_TRACEBACK_BYTES


@pytest.mark.parametrize("exception", [None, False, {}, [], "private unrelated payload", "RuntimeError: private raw payload"])
def test_unrecognized_exception_payload_is_not_copied(exception):
  event = parse_modeld_failure(modeld_record(exc_info=exception))
  assert not ({"exception_type", "traceback", "traceback_truncated"} & event.keys())


def test_model_failure_diagnostic_does_not_change_guard_or_invent_comm_issue():
  event = parse_modeld_failure(modeld_record())
  guard = ClusterHealthGuard()
  assert guard.update({"diagnostic_events": (event,)}, 10) is None and not guard.degraded
  assert "model_stale" in guard.update({"diagnostic_events": (event,), "model_seen": True, "model_age_ms": 700}, 11)
