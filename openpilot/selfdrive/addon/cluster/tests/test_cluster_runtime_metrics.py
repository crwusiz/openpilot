import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.cluster_runtime_metrics import ClusterRuntimeMetrics, read_process_resources


@pytest.fixture
def cluster_models(monkeypatch):
  messages = {}
  updated = {}

  class SubMaster:
    def __init__(self, services):
      self.services = services
      self.updated = updated

    def __getitem__(self, service):
      return messages[service]

  dependencies = {
    "openpilot.cereal": SimpleNamespace(log=SimpleNamespace(), messaging=SimpleNamespace(SubMaster=SubMaster)),
    "opendbc.car": SimpleNamespace(structs=SimpleNamespace(CarState=SimpleNamespace(GearShifter=SimpleNamespace(reverse=1)))),
    "openpilot.common.constants": SimpleNamespace(UnitConverter=lambda: SimpleNamespace()),
    "openpilot.common.swaglog": SimpleNamespace(cloudlog=Mock()),
    "openpilot.common.params": SimpleNamespace(Params=lambda: SimpleNamespace(get=lambda _key: b"1")),
    "openpilot.common.transformations.camera": SimpleNamespace(DEVICE_CAMERAS={}, view_frame_from_device_frame=np.eye(3)),
    "openpilot.common.transformations.orientation": SimpleNamespace(rot_from_euler=Mock()),
    "openpilot.selfdrive.controls.radard": SimpleNamespace(RADAR_TO_CAMERA=1.52),
  }
  for name, dependency in dependencies.items():
    monkeypatch.setitem(sys.modules, name, dependency)
  spec = importlib.util.spec_from_file_location("cluster_models_health_under_test", Path(__file__).parents[1] / "cluster_models.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  # Exercise real initialization/subscriptions without starting target polling.
  module.threading = SimpleNamespace(RLock=threading.RLock, Thread=lambda **_kwargs: Mock())
  clock = SimpleNamespace(now=100.0)
  module.time = SimpleNamespace(monotonic=lambda: clock.now)
  models = module.ClusterModels()
  return SimpleNamespace(models=models, messages=messages, updated=updated, clock=clock)


def test_health_does_not_report_unseen_locationd_defaults_as_errors(cluster_models):
  models = cluster_models.models
  assert 'deviceMotion' in models.sm.services
  snapshot = models.get_health_data()
  assert not snapshot['model_seen']
  assert not snapshot['device_motion_seen']
  assert snapshot['device_motion_inputs_ok'] is None
  assert snapshot['device_motion_input_error_total'] == 0
  assert snapshot['model_drop_perc'] is None
  assert snapshot['onroad_started'] is None
  assert not snapshot['car_state_seen']
  assert snapshot['model_age_ms'] is snapshot['device_motion_age_ms'] is snapshot['car_state_age_ms'] is None
  snapshot['device_motion_input_error_total'] = 100
  assert models.get_health_data()['device_motion_input_error_total'] == 0


def test_health_latches_transient_model_drop_and_locationd_errors(cluster_models):
  state = cluster_models
  state.updated.update(modelV2=True, deviceMotion=True)
  state.messages['modelV2'] = SimpleNamespace(frameDropPerc=4.5, modelExecutionTime=0.032)
  state.messages['deviceMotion'] = SimpleNamespace(inputsOK=False, posenetOK=False)
  state.models._update_health_data()
  # Errors recover before the busy render thread samples them.
  state.messages['modelV2'] = SimpleNamespace(frameDropPerc=0.2, modelExecutionTime=0.028)
  state.messages['deviceMotion'] = SimpleNamespace(inputsOK=True, posenetOK=True)
  state.models._update_health_data()
  snapshot = state.models.get_health_data()
  assert snapshot['model_drop_perc'] == 0.2
  assert snapshot['model_drop_peak_perc'] == 4.5
  assert snapshot['model_exec_ms'] == 28.0
  assert snapshot['model_lagging_update_total'] == 1
  assert snapshot['device_motion_inputs_ok']
  assert snapshot['device_motion_posenet_ok']
  assert snapshot['device_motion_input_error_total'] == snapshot['device_motion_posenet_error_total'] == 1
  # Consuming a diagnostic peak does not reset distinct-message counters.
  assert state.models.get_health_data()['model_drop_peak_perc'] == 0.2
  assert state.models.get_health_data()['device_motion_input_error_total'] == 1


def test_health_counts_updated_messages_instead_of_polling_cycles(cluster_models):
  state = cluster_models
  state.updated['deviceMotion'] = True
  state.messages['deviceMotion'] = SimpleNamespace(inputsOK=False, posenetOK=True)
  state.models._update_health_data()
  state.updated['deviceMotion'] = False
  for _ in range(10):
    state.models._update_health_data()
  assert state.models.get_health_data()['device_motion_input_error_total'] == 1


def test_health_uses_selfdrived_strict_drop_threshold(cluster_models):
  state = cluster_models
  state.updated['modelV2'] = True
  for dropped in (0.0, 2.0, 2.01):
    state.messages['modelV2'] = SimpleNamespace(frameDropPerc=dropped, modelExecutionTime=0.030)
    state.models._update_health_data()
  assert state.models.get_health_data()['model_lagging_update_total'] == 1


def test_health_snapshots_device_state_and_ignores_nonfinite_values(cluster_models):
  state = cluster_models
  state.updated['deviceState'] = True
  state.messages['deviceState'] = SimpleNamespace(
    cpuUsagePercent=[32, 89, 57], cpuTempC=[68.5, float('nan'), 72.0], memoryUsagePercent=61, thermalStatus='ok',
  )
  state.models._update_health_data()
  snapshot = state.models.get_health_data()
  assert snapshot['device_state_seen']
  assert snapshot['device_cpu_max_pct'] == 89.0
  assert snapshot['cpu_temp_max_c'] == 72.0
  assert snapshot['device_memory_pct'] == 61.0
  assert snapshot['thermal_status'] == 'ok'


def test_health_missing_and_nonfinite_model_values_remain_unknown(cluster_models):
  state = cluster_models
  state.updated['modelV2'] = True
  state.messages['modelV2'] = SimpleNamespace(frameDropPerc=float('nan'), modelExecutionTime=float('inf'))
  state.models._update_health_data()
  snapshot = state.models.get_health_data()
  assert snapshot['model_drop_perc'] is None
  assert snapshot['model_exec_ms'] is None
  assert snapshot['model_lagging_update_total'] == 0


@pytest.mark.parametrize('started', [True, False, None])
def test_health_observes_onroad_and_car_state_with_missing_model_inputs(cluster_models, started):
  state = cluster_models
  state.updated.update(deviceState=True, carState=True)
  state.messages['deviceState'] = SimpleNamespace() if started is None else SimpleNamespace(started=started)
  state.models._update_health_data()
  state.updated.update(deviceState=False, carState=False)
  state.clock.now += 2.5
  state.models._update_health_data()
  snapshot = state.models.get_health_data()
  assert snapshot['onroad_started'] is started
  assert snapshot['car_state_seen']
  assert snapshot['car_state_age_ms'] == 2500.0
  assert not snapshot['model_seen']
  assert not snapshot['device_motion_seen']
  assert snapshot['model_age_ms'] is snapshot['device_motion_age_ms'] is None
  assert snapshot['model_lagging_update_total'] == snapshot['device_motion_input_error_total'] == 0


def test_health_receive_ages_grow_when_stale_and_refresh_only_updated_service(cluster_models):
  state = cluster_models
  state.updated.update(modelV2=True, deviceMotion=True, carState=True)
  state.messages['modelV2'] = SimpleNamespace(frameDropPerc=0.0, modelExecutionTime=0.030)
  state.messages['deviceMotion'] = SimpleNamespace(inputsOK=True, posenetOK=True)
  state.models._update_health_data()
  snapshot = state.models.get_health_data()
  for field in ('model_age_ms', 'device_motion_age_ms', 'car_state_age_ms'):
    assert snapshot[field] == 0.0

  state.updated.update(modelV2=False, deviceMotion=False, carState=False)
  state.clock.now += 2.25
  state.models._update_health_data()
  snapshot = state.models.get_health_data()
  for field in ('model_age_ms', 'device_motion_age_ms', 'car_state_age_ms'):
    assert snapshot[field] == 2250.0
  state.clock.now += 0.75
  assert state.models.get_health_data()['model_age_ms'] == 3000.0

  state.updated['modelV2'] = True
  state.models._update_health_data()
  snapshot = state.models.get_health_data()
  assert snapshot['model_age_ms'] == 0.0
  assert snapshot['device_motion_age_ms'] == snapshot['car_state_age_ms'] == 3000.0
  assert snapshot['model_seen'] and snapshot['device_motion_seen'] and snapshot['car_state_seen']


def test_process_resources_reads_current_rss_and_native_threads(tmp_path):
  status = tmp_path / 'status'
  status.write_text('Name:\tcluster\nVmRSS:\t20480 kB\nThreads:\t8\n', encoding='ascii')
  assert read_process_resources(status) == (20.0, 8)


def test_process_resources_missing_or_malformed_file_is_safe(tmp_path):
  assert read_process_resources(tmp_path / 'missing') == (None, None)
  status = tmp_path / 'status'
  status.write_text('VmRSS:\tnot-a-number kB\nThreads:\tunknown\n', encoding='ascii')
  assert read_process_resources(status) == (None, None)


@pytest.fixture
def metrics_state():
  clock = SimpleNamespace(wall=0.0, cpu=0.0)
  resources = Mock(return_value=(20.0, 8))
  metrics = ClusterRuntimeMetrics(clock=lambda: clock.wall, cpu_clock=lambda: clock.cpu, resources=resources)
  return SimpleNamespace(clock=clock, resources=resources, metrics=metrics)


def test_metrics_measures_interval_cpu_across_cores_without_polling_proc_each_frame(metrics_state):
  state = metrics_state
  for tick in range(100):
    state.clock.wall = tick / 10
    assert state.metrics.sample({}) is None
  state.resources.assert_not_called()
  state.clock.wall, state.clock.cpu = 10.0, 25.0
  line = state.metrics.sample({})
  assert 'cpu_pct=250.0' in line
  assert 'rss_mb=20.0' in line
  assert 'threads=8' in line
  state.resources.assert_called_once()
  state.clock.wall, state.clock.cpu = 20.0, 30.0
  assert 'cpu_pct=50.0' in state.metrics.sample({})


def test_metrics_preserves_brief_health_peaks_and_uses_cumulative_error_deltas(metrics_state):
  state = metrics_state
  state.metrics.sample({
    'model_drop_perc': 4.5, 'model_drop_peak_perc': 6.0,
    'model_lagging_update_total': 3, 'device_motion_input_error_total': 2,
  })
  state.clock.wall = 10.0
  line = state.metrics.sample({
    'model_drop_perc': 0.2, 'model_drop_peak_perc': 0.2,
    'model_lagging_update_total': 3, 'device_motion_input_error_total': 2,
    'device_motion_seen': True, 'device_motion_inputs_ok': True,
  })
  assert 'model_drop_perc=0.2' in line
  assert 'model_drop_max=6.0' in line
  assert 'model_lagging_updates=3' in line
  assert 'locationd_input_error_updates=2' in line
  assert 'inputs_ok=1' in line
  state.clock.wall = 20.0
  line = state.metrics.sample({
    'model_drop_perc': 0.1, 'model_drop_peak_perc': 0.3,
    'model_lagging_update_total': 4, 'device_motion_input_error_total': 2,
  })
  assert 'model_drop_max=0.3' in line
  assert 'model_lagging_updates=1' in line
  assert 'locationd_input_error_updates=0' in line


def test_metrics_unknown_platform_and_unseen_health_do_not_imply_faults(metrics_state):
  state = metrics_state
  state.resources.return_value = (None, None)
  state.clock.wall = 10.0
  line = state.metrics.sample({'device_motion_seen': False, 'device_motion_inputs_ok': None})
  assert 'rss_mb=n/a' in line
  assert 'threads=n/a' in line
  assert 'device_motion_seen=0' in line
  assert 'inputs_ok=n/a' in line
  assert 'locationd_input_error_updates=0' in line
  assert 'onroad_started=n/a' in line
  assert 'car_state_seen=n/a' in line
  assert 'model_age_ms=n/a' in line
  assert 'device_motion_age_ms=n/a' in line
  assert 'car_state_age_ms=n/a' in line


def test_metrics_reports_onroad_missing_inputs_and_later_stale_ages(cluster_models, metrics_state):
  state = cluster_models
  state.updated.update(deviceState=True, carState=True)
  state.messages['deviceState'] = SimpleNamespace(started=True)
  state.models._update_health_data()
  metrics_state.clock.wall = 10.0
  line = metrics_state.metrics.sample(state.models.get_health_data())
  assert 'onroad_started=1' in line
  assert 'car_state_seen=1' in line
  assert 'car_state_age_ms=0.0' in line
  assert 'model_seen=0' in line
  assert 'model_age_ms=n/a' in line
  assert 'device_motion_seen=0' in line
  assert 'device_motion_age_ms=n/a' in line

  state.updated.update(deviceState=True, modelV2=True, deviceMotion=True, carState=False)
  state.messages['deviceState'] = SimpleNamespace(started=False)
  state.messages['modelV2'] = SimpleNamespace(frameDropPerc=0.0, modelExecutionTime=0.028)
  state.messages['deviceMotion'] = SimpleNamespace(inputsOK=True, posenetOK=True)
  state.models._update_health_data()
  state.clock.now += 3.5
  metrics_state.clock.wall = 20.0
  line = metrics_state.metrics.sample(state.models.get_health_data())
  assert 'onroad_started=0' in line
  assert 'model_seen=1' in line
  assert 'device_motion_seen=1' in line
  for field in ('car_state_age_ms', 'model_age_ms', 'device_motion_age_ms'):
    assert f'{field}=3500.0' in line
  assert 'model_lagging_updates=0' in line
  assert 'locationd_input_error_updates=0' in line
