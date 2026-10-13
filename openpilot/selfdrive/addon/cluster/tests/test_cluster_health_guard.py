import json

import pytest

from openpilot.selfdrive.addon.cluster.cluster_health_guard import ClusterHealthGuard, parse_comm_issue


def test_input_outage_limits_display_work_and_restores_after_stable_recovery():
  guard = ClusterHealthGuard()
  healthy = {'onroad_started': True, 'model_seen': True, 'model_age_ms': 10.0}
  assert guard.update(healthy, 0.0) is None and guard.allow_render(0.0)
  broken = {**healthy, 'model_age_ms': 1200.0}
  assert 'model_stale' in guard.update(broken, 1.0)
  assert guard.degraded and guard.allow_render(1.0)
  assert not guard.allow_render(1.05)
  assert guard.allow_render(1.2)
  assert guard.update(broken, 2.0) is None
  assert guard.update(healthy, 3.0) is None
  assert guard.update(broken, 3.5) is None
  assert guard.update(healthy, 4.0) is None
  assert guard.update(healthy, 4.9) is None
  assert 'active=0' in guard.update(healthy, 5.0)
  assert not guard.degraded
  assert guard.allow_render(5.0) and guard.allow_render(5.05)


@pytest.mark.parametrize('health, reason', [
  ({'device_motion_seen': True, 'device_motion_age_ms': 700.0}, 'motion_stale'),
  ({'device_motion_seen': True, 'device_motion_inputs_ok': False}, 'motion_inputs_invalid'),
  ({'drive_fault_alert': 'commIssue/noEntry'}, 'commIssue'),
  ({'drive_fault_alert': 'commIssueAvgFreq/softDisable'}, 'commIssueAvgFreq'),
  ({'drive_fault_alert': 'modeldLagging/permanent'}, 'modeldLagging'),
])
def test_actual_driving_alerts_and_motion_faults_activate_guard(health, reason):
  guard = ClusterHealthGuard()
  assert reason in guard.update(health, 10.0)
  assert guard.degraded


def test_brief_controller_comm_issue_between_render_samples_gets_a_recovery_window():
  guard = ClusterHealthGuard()
  health = {'diagnostic_events': ({'event': 'commIssue', 'not_alive': ['longitudinalPlan']},)}
  assert 'commIssue' in guard.update(health, 10.0)
  assert guard.degraded
  assert guard.update({}, 10.05) is None
  assert guard.update({}, 10.9) is None
  assert 'active=0' in guard.update({}, 11.1)


@pytest.mark.parametrize('health', [
  {}, {'model_seen': False, 'model_age_ms': 10000.0}, {'model_seen': True, 'model_age_ms': None},
  {'model_seen': True, 'model_age_ms': float('nan')}, {'model_seen': True, 'model_age_ms': 500.0},
  {'device_motion_seen': False, 'device_motion_inputs_ok': False},
  {'onroad_started': False, 'model_seen': True, 'model_age_ms': 10000.0},
  {'drive_fault_alert': 'preLaneChangeLeft/warning'},
])
def test_unseen_offroad_and_normal_alerts_do_not_trigger_guard(health):
  guard = ClusterHealthGuard()
  assert guard.update(health, 10.0) is None and not guard.degraded


def test_comm_issue_preserves_actual_failing_service_lists_without_raw_metadata():
  record = json.dumps({
    'msg': {'event': 'commIssue', 'invalid': ['vehicleParameters'], 'not_alive': ['modelV2', 'deviceMotion'], 'not_freq_ok': []},
    'created': 1770000000.5, 'process': 456, 'ctx': {'private': 'do not copy'}, 'exc_info': 'do not copy',
  })
  assert parse_comm_issue(record) == {
    'event': 'commIssue', 'invalid': ['vehicleParameters'], 'not_alive': ['modelV2', 'deviceMotion'], 'not_freq_ok': [],
    'created': 1770000000.5, 'process': 456,
  }


@pytest.mark.parametrize('record', [
  'broken commIssue', json.dumps({'msg': 'commIssue'}), json.dumps({'msg': {'event': 'anotherEvent'}}),
  json.dumps({'msg': {'event': 'commIssue', 'not_alive': 'modelV2'}}), 'commIssue' + 'x' * 16384,
])
def test_malformed_unrelated_and_large_error_logs_are_ignored(record):
  assert parse_comm_issue(record) is None
