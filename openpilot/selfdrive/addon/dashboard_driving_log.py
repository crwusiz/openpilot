from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from heapq import nlargest
from io import BytesIO
import json
import os
from pathlib import Path
import re


MAX_SOURCE_BYTES = 2 * 1024**2
MAX_SWAGLOG_FILES = 180
MAX_SWAGLOG_BYTES = 12 * 1024**2
MAX_ENTRIES = 3000
MAX_PRIORITIZED_LAG_ENTRIES = 32
MAX_OUTPUT_BYTES = 1024**2

_TEXT_SOURCES = {
  'NAVI': ('navi_debug.log',),
  'CRUISE': ('cruise_debug.log',),
  'TRAFFIC': ('traffic_debug.log', 'traffic_debug.log.1', 'traffic_debug.log.2'),
}
_TEXT_LINE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) (\[[A-Z]+\] )(.*)$')
_SWAGLOG_NAME = re.compile(r'swaglog\.([0-9]+)')
_LAG_EVENT = 'selfdrived.lagging'
_LAG_FIELDS = frozenset({
  'loop_avg_ms', 'car_state_receive_max_ms', 'processing_max_ms', 'car_state_timeouts',
  'card_lag_ms', 'card_lag_max_ms', 'car_state_publish_gap_max_ms', 'car_state_delivery_max_ms',
  'brake_pressed_at_receive_max', 'gas_pressed_at_receive_max', 'longitudinal_accel_at_receive_max',
  'brake_pressed', 'longitudinal_accel', 'can_valid', 'can_timeout', 'active_events',
})


@dataclass(frozen=True)
class DrivingLogSnapshot:
  text: str
  entry_count: int


def _read_chunks(paths: Sequence[Path], byte_limit: int) -> list[bytes]:
  """Read recent bytes from newest to oldest and keep complete lines only."""
  chunks = []
  seen = set()
  for path in paths:
    if byte_limit <= 0:
      break
    try:
      if not path.is_file():
        continue
      with path.open('rb') as log:
        stat = os.fstat(log.fileno())
        identity = (stat.st_dev, stat.st_ino)
        if stat.st_ino and identity in seen:
          continue  # The active file can move to a rotation name during this read.
        start = max(0, stat.st_size - byte_limit)
        if start:
          log.seek(start - 1)
          starts_with_partial_line = log.read(1) != b'\n'
        else:
          starts_with_partial_line = False
        data = log.read(stat.st_size - start)
        seen.add(identity)
    except OSError:
      continue

    byte_limit -= len(data)
    if starts_with_partial_line:
      data = data.partition(b'\n')[2]
    chunks.append(data[:data.rfind(b'\n') + 1])

  return chunks


def _text_entries(data: bytes, source: str) -> Iterator[tuple[str, str]]:
  timestamp = ''
  lines: list[str] = []
  for raw in BytesIO(data):
    line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
    match = _TEXT_LINE.fullmatch(line)
    if match is None:
      if line.startswith('===') and 'Session Started' in line:
        if lines:
          yield timestamp, '\n'.join(lines)
          lines = []
      elif lines:
        lines.append(line)
      continue

    if lines:
      yield timestamp, '\n'.join(lines)
      lines = []

    timestamp, level, message = match.groups()
    if message.startswith('===') and 'Session Started' in message:
      continue
    try:
      datetime.fromisoformat(timestamp)
    except ValueError:
      continue
    lines = [f'{timestamp} [{source}] {level}{message}']

  if lines:
    yield timestamp, '\n'.join(lines)


def _swaglog_paths(path: Path) -> list[Path]:
  try:
    indexed = [(int(match[1]), file) for file in path.iterdir() if (match := _SWAGLOG_NAME.fullmatch(file.name))]
  except OSError:
    return []
  indexed.sort(reverse=True)
  return [file for _, file in indexed[:MAX_SWAGLOG_FILES]]


def _selfdrived_entries(data: bytes) -> Iterator[tuple[str, str]]:
  for raw in BytesIO(data):
    if b'selfdrived.lagging' not in raw:
      continue
    try:
      record = json.loads(raw)
    except (ValueError, RecursionError):
      continue
    if not isinstance(record, dict):
      continue
    msg = record.get('msg')
    if not isinstance(msg, dict) or msg.get('event$s', msg.get('event')) != _LAG_EVENT:
      continue
    created = record.get('created')
    if type(created) not in (int, float):
      continue
    try:
      timestamp = datetime.fromtimestamp(created).isoformat(sep=' ', timespec='milliseconds')
    except (OverflowError, OSError, ValueError):
      continue

    fields = {name: value for key, value in msg.items() if (name := key.split('$', 1)[0]) in _LAG_FIELDS}
    payload = json.dumps(fields, separators=(',', ':'))
    yield timestamp, f'{timestamp} [SELFDRIVED] [INFO] {_LAG_EVENT} {payload}'


def read_driving_log(log_dir: Path, swaglog_dir: Path) -> DrivingLogSnapshot:
  notes = [
    '# Driving diagnostics timeline (device local time)',
    f'# Text sources: latest {MAX_SOURCE_BYTES:,} bytes each across available files (traffic .log, .1 and .2).',
    f'# Swaglog scan: newest {MAX_SWAGLOG_FILES} rotations, up to {MAX_SWAGLOG_BYTES:,} bytes. Includes selfdrived.lagging only.',
    f'# Output: up to {MAX_ENTRIES:,} entries and {MAX_OUTPUT_BYTES:,} UTF-8 bytes.',
    f'# Latest {MAX_PRIORITIZED_LAG_ENTRIES} scanned lag events take priority and may predate the text log window.',
  ]

  def text_entries():
    for source, filenames in _TEXT_SOURCES.items():
      chunks = _read_chunks([log_dir / name for name in filenames], MAX_SOURCE_BYTES)
      found = False
      for data in reversed(chunks):
        for entry in _text_entries(data, source):
          found = True
          yield entry
      if not found:
        notes.append(f'# {source}: ' + ('no complete entries' if chunks else 'missing or unreadable'))

  def lag_entries():
    chunks = _read_chunks(_swaglog_paths(swaglog_dir), MAX_SWAGLOG_BYTES)
    found = False
    for data in reversed(chunks):
      for entry in _selfdrived_entries(data):
        found = True
        yield entry
    if not found:
      notes.append('# SELFDRIVED: ' + ('no lag entries in scanned files' if chunks else 'swaglog missing or unreadable'))

  def ranked_entries():
    for i, (stamp, text) in enumerate(text_entries()):
      yield False, stamp, False, i, text

    # Give recent lag events room even when debug samples fill the merged log.
    recent_lags = nlargest(MAX_ENTRIES, ((stamp, i, text) for i, (stamp, text) in enumerate(lag_entries())))
    for rank, (stamp, i, text) in enumerate(recent_lags):
      yield rank < MAX_PRIORITIZED_LAG_ENTRIES, stamp, True, i, text

  newest = nlargest(MAX_ENTRIES, ranked_entries())
  header = '\n'.join(notes) + '\n\n'
  body_budget = MAX_OUTPUT_BYTES - len(header.encode('utf-8'))
  available = body_budget
  selected = []
  for _, stamp, is_lag, i, text in newest:
    size = len(text.encode('utf-8')) + 1
    if size > body_budget:
      continue
    if size > available:
      break
    available -= size
    selected.append((stamp, is_lag, i, text))

  return DrivingLogSnapshot(header + ''.join(text + '\n' for _, _, _, text in sorted(selected)), len(selected))
