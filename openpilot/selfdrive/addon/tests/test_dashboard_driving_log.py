import json
from datetime import datetime

import pytest

from openpilot.common.logging_extra import SwagLogFileFormatter
from openpilot.selfdrive.addon import dashboard_driving_log


def write_log(root, name, content):
  (root / name).write_text(content, encoding='utf-8')


def swag_record(created, message):
  # logmessaged passes SwagFormatter's JSON to this disk formatter.
  return SwagLogFileFormatter(None).format(json.dumps({'created': created, 'level': 'INFO', 'msg': message}))


def test_driving_log_merges_local_timestamps_and_typed_lag_event(tmp_path):
  second = '2026-11-25 15:17:25'
  local_time = datetime.fromisoformat(second).timestamp()
  write_log(tmp_path, 'navi_debug.log', f'{second}.450 [INFO] school zone ENTER\n  camera distance: 80\n')
  write_log(tmp_path, 'cruise_debug.log', f'{second}.100 [DEBUG] SET target=60\n')
  write_log(tmp_path, 'traffic_debug.log', f'{second}.600 [INFO] current traffic update\n')
  write_log(tmp_path, 'traffic_debug.log.1', f'{second}.300 [INFO] previous traffic update\n')
  write_log(tmp_path, 'traffic_debug.log.2', f'{second}.050 [INFO] oldest traffic update\n')
  swaglog_dir = tmp_path / 'swaglog'
  swaglog_dir.mkdir()
  record = swag_record(local_time + .375, {
    'event': 'selfdrived.lagging',
    'loop_avg_ms': 12.5,
    'car_state_timeouts': 2,
    'card_lag_max_ms': 18.5,
    'car_state_publish_gap_max_ms': 30.0,
    'car_state_delivery_max_ms': None,
    'brake_pressed_at_receive_max': True,
    'gas_pressed_at_receive_max': False,
    'longitudinal_accel_at_receive_max': -2.5,
    'brake_pressed': True,
    'active_events': ['selfdrivedLagging'],
  })
  write_log(swaglog_dir, 'swaglog.0000000000', record + '\n')

  snapshot = dashboard_driving_log.read_driving_log(tmp_path, swaglog_dir)

  assert snapshot.entry_count == 6
  expected_order = [
    '[TRAFFIC] [INFO] oldest traffic update',
    '[CRUISE] [DEBUG] SET target=60',
    '[TRAFFIC] [INFO] previous traffic update',
    '[SELFDRIVED] [INFO] selfdrived.lagging',
    '[NAVI] [INFO] school zone ENTER',
    '  camera distance: 80',
    '[TRAFFIC] [INFO] current traffic update',
  ]
  offsets = [snapshot.text.index(message) for message in expected_order]
  assert offsets == sorted(offsets)
  lag_line = next(line for line in snapshot.text.splitlines() if '[SELFDRIVED]' in line)
  assert f'{second}.375' in lag_line
  payload = json.loads(lag_line.split('selfdrived.lagging ', 1)[1])
  assert payload['loop_avg_ms'] == 12.5
  assert payload['car_state_timeouts'] == 2
  assert payload['card_lag_max_ms'] == 18.5
  assert payload['car_state_publish_gap_max_ms'] == 30.0
  assert payload['car_state_delivery_max_ms'] is None
  assert payload['brake_pressed_at_receive_max'] is True
  assert payload['gas_pressed_at_receive_max'] is False
  assert payload['longitudinal_accel_at_receive_max'] == -2.5
  assert payload['brake_pressed'] is True
  assert payload['active_events'] == ['selfdrivedLagging']


def test_driving_log_skips_unrelated_and_malformed_messages(tmp_path):
  write_log(tmp_path, 'navi_debug.log', '\n'.join((
    'session banner outside log format',
    'orphan continuation',
    '2026-11-25 15:17:25.100 [INFO] complete navi entry',
    '2026-11-25 15:17:25.200 [INFO] incomplete live navi entry',
  )))
  write_log(tmp_path, 'traffic_debug.log.1', '2026-13-25 15:17:25.100 [INFO] invalid date\n')
  (tmp_path / 'cruise_debug.log').mkdir()  # Unreadable as a log file.
  created = datetime.fromisoformat('2026-11-25 15:17:25.300').timestamp()
  records = [
    'bad JSON mentioning selfdrived.lagging',
    swag_record(created, 'string mentions selfdrived.lagging'),
    swag_record(created, {'event': 'selfdrived.lagging_extra', 'unexpected': 'wrong event name'}),
    swag_record(created, {'event': 'commIssue', 'unexpected': 'other fault'}),
    swag_record(float('nan'), {'event': 'selfdrived.lagging', 'unexpected': 'non-finite date'}),
    swag_record(float('inf'), {'event': 'selfdrived.lagging', 'unexpected': 'infinite date'}),
    swag_record(1e100, {'event': 'selfdrived.lagging', 'unexpected': 'out of range date'}),
    swag_record(created, {'event': 'selfdrived.lagging', 'loop_avg_ms': 13.4}),
  ]
  write_log(tmp_path, 'swaglog.0000000001', '\n'.join(records) + '\n')
  write_log(tmp_path, 'swaglog.0000000002',
            swag_record(created, {'event': 'selfdrived.lagging', 'unexpected': 'incomplete live swaglog entry'}))

  snapshot = dashboard_driving_log.read_driving_log(tmp_path, tmp_path)

  assert snapshot.entry_count == 2
  assert '[NAVI] [INFO] complete navi entry' in snapshot.text
  assert '[SELFDRIVED] [INFO] selfdrived.lagging' in snapshot.text
  assert '13.4' in snapshot.text
  for excluded in ('banner', 'orphan', 'incomplete', 'invalid date', 'unexpected', 'wrong event', 'commIssue', 'bad JSON'):
    assert excluded not in snapshot.text


def test_driving_log_tail_and_entry_limits_keep_most_recent_records(tmp_path, monkeypatch):
  older_entry = '2026-11-25 15:17:25.100 [INFO] outside file tail\n'
  newest_entries = '\n'.join((
    '2026-11-25 15:17:25.200 [INFO] oldest retained navi record',
    '2026-11-25 15:17:25.400 [INFO] newest navi record',
    '',
  ))
  write_log(tmp_path, 'navi_debug.log', older_entry + newest_entries)
  write_log(tmp_path, 'cruise_debug.log', '2026-11-25 15:17:25.300 [INFO] latest cruise record\n')
  monkeypatch.setattr(dashboard_driving_log, 'MAX_SOURCE_BYTES', len(newest_entries.encode('utf-8')) + 5)

  all_retained = dashboard_driving_log.read_driving_log(tmp_path, tmp_path)

  assert all_retained.entry_count == 3
  assert 'outside file tail' not in all_retained.text

  monkeypatch.setattr(dashboard_driving_log, 'MAX_ENTRIES', 2)
  latest = dashboard_driving_log.read_driving_log(tmp_path, tmp_path)

  assert latest.entry_count == 2
  assert 'oldest retained navi record' not in latest.text
  assert latest.text.index('latest cruise record') < latest.text.index('newest navi record')


@pytest.mark.parametrize('limit', ['entries', 'bytes'])
def test_driving_log_keeps_lag_events_when_debug_records_fill_the_export(tmp_path, monkeypatch, limit):
  timestamp = '2026-11-26 19:30:05'
  created = datetime.fromisoformat(timestamp).timestamp()
  write_log(tmp_path, 'navi_debug.log', ''.join(
    f'{timestamp}.{ms:03d} [DEBUG] sample {ms} {"x" * 300}\n' for ms in (250, 500, 750)
  ))
  write_log(tmp_path, 'swaglog.0000000000', '\n'.join(
    swag_record(created - offset, {'event': 'selfdrived.lagging', 'loop_avg_ms': loop_ms})
    for offset, loop_ms in ((4, 11.5), (2, 12.5))
  ) + '\n')
  monkeypatch.setattr(dashboard_driving_log, 'MAX_PRIORITIZED_LAG_ENTRIES', 1)
  if limit == 'entries':
    monkeypatch.setattr(dashboard_driving_log, 'MAX_ENTRIES', 2)
  else:
    monkeypatch.setattr(dashboard_driving_log, 'MAX_OUTPUT_BYTES', 1000)

  snapshot = dashboard_driving_log.read_driving_log(tmp_path, tmp_path)

  assert snapshot.entry_count == 2
  assert len(snapshot.text.encode('utf-8')) <= dashboard_driving_log.MAX_OUTPUT_BYTES
  assert '11.5' not in snapshot.text
  assert 'sample 500' not in snapshot.text
  assert snapshot.text.index('[SELFDRIVED]') < snapshot.text.index('sample 750')
  assert '12.5' in snapshot.text


@pytest.mark.parametrize('create_dirs', [False, True])
def test_driving_log_can_be_empty(tmp_path, create_dirs):
  log_dir = tmp_path / 'logs'
  swaglog_dir = tmp_path / 'swaglog'
  if create_dirs:
    log_dir.mkdir()
    swaglog_dir.mkdir()

  snapshot = dashboard_driving_log.read_driving_log(log_dir, swaglog_dir)

  assert snapshot.entry_count == 0
  assert '# NAVI: missing or unreadable' in snapshot.text
  assert '# SELFDRIVED: swaglog missing or unreadable' in snapshot.text
  assert all(not line or line.startswith('# ') for line in snapshot.text.splitlines())
