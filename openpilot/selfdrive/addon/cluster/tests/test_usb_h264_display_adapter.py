import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.cluster_display_pipeline import ClusterDisplayPipeline
from openpilot.selfdrive.addon.cluster.usb_display import h264_display as adapter_module
from openpilot.selfdrive.addon.cluster.usb_display.h264_encoder import PreparedH264Frame


def prepared_frame(created_at=10.0, data=b"SPS/PPS/IDR"):
  return PreparedH264Frame(data, created_at, 0.012, 0.003, created_at + 0.012, int(created_at * 1_000_000))


@pytest.fixture
def state(monkeypatch):
  clock = SimpleNamespace(now=10.0)
  logs = []
  monkeypatch.setattr(adapter_module, "flog", logs.append)
  monkeypatch.setattr(adapter_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
  encoder = Mock()
  transport = SimpleNamespace(connected=True, open=Mock(), close=Mock(), send_access_unit=Mock(return_value=True))

  def close_transport():
    transport.connected = False

  transport.close.side_effect = close_transport
  adapter = adapter_module.H264DisplayAdapter(SimpleNamespace(usb_max_frame_age=0.25), encoder=encoder, transport=transport)
  return SimpleNamespace(adapter=adapter, encoder=encoder, transport=transport, clock=clock, logs=logs)


@pytest.mark.parametrize("opened", [True, False])
def test_open_and_connected_follow_transport_result(state, opened):
  def open_transport():
    state.transport.connected = opened
    return opened

  state.transport.open.side_effect = open_transport
  assert state.adapter.open() is opened
  assert state.adapter.connected is opened
  state.encoder.open.assert_not_called()


def test_encoder_failure_is_latched_and_preserves_original_cause(state):
  failure = RuntimeError("hardware drain failed")
  state.encoder.encode.side_effect = failure
  with pytest.raises(RuntimeError, match="hardware drain failed") as original:
    state.adapter.prepare_frame("frame", 10.0)
  assert original.value is failure

  with pytest.raises(RuntimeError, match="select USB JPEG") as check:
    state.adapter.check_encoder()
  assert check.value.__cause__ is failure

  with pytest.raises(RuntimeError, match="select USB JPEG") as retry:
    state.adapter.prepare_frame("next frame", 10.05)
  assert retry.value.__cause__ is failure
  state.encoder.encode.assert_called_once_with("frame", 10.0)
  state.transport.send_access_unit.assert_not_called()


def test_pending_hardware_output_produces_no_placeholder_frame(state):
  state.encoder.encode.return_value = None
  assert state.adapter.prepare_frame("image", 9.99) is None
  state.encoder.encode.assert_called_once_with("image", 9.99)
  state.transport.send_access_unit.assert_not_called()
  state.adapter.check_encoder()


def test_delayed_hardware_output_keeps_its_original_render_timestamp(state):
  previous = prepared_frame(created_at=9.7)
  state.encoder.encode.return_value = previous

  output = state.adapter.prepare_frame("new input", created_at=10.0)

  assert output is previous
  assert output.created_at == 9.7
  assert state.adapter.is_prepared_stale(output)
  state.encoder.encode.assert_called_once_with("new input", 10.0)


@pytest.mark.parametrize("created_at,stale", [(9.749, True), (9.75, False), (9.8, False)])
def test_staleness_uses_render_time_and_exact_age_budget(state, created_at, stale):
  assert state.adapter.is_prepared_stale(prepared_frame(created_at)) is stale


@pytest.mark.parametrize("result", [True, False, None])
def test_submission_forwards_access_unit_and_render_timestamp(state, result):
  frame = prepared_frame(created_at=9.99, data=b"\x00\x00\x01\x65independent")
  state.transport.send_access_unit.return_value = result

  assert state.adapter.send_prepared(frame) is result

  state.transport.send_access_unit.assert_called_once_with(frame.data, created_at=9.99, keyframe=True)


@pytest.mark.parametrize("encoder_close_fails", [False, True])
def test_final_close_releases_transport_after_encoder_even_on_encoder_failure(state, encoder_close_fails):
  operations = Mock()
  operations.attach_mock(state.encoder.close, "encoder")
  operations.attach_mock(state.transport.close, "transport")
  if encoder_close_fails:
    state.encoder.close.side_effect = RuntimeError("native close failed")
    with pytest.raises(RuntimeError, match="native close failed"):
      state.adapter.close()
  else:
    state.adapter.close()

  assert [call[0] for call in operations.mock_calls] == ["encoder", "transport"]
  assert not state.adapter.connected


def test_sender_exception_recovery_does_not_close_an_encoder_busy_on_next_frame(state):
  send_started = threading.Event()
  allow_send_failure = threading.Event()
  encode_started = threading.Event()
  release_encoder = threading.Event()
  connection_reset = threading.Event()
  original_close = state.transport.close.side_effect

  def encode(image, created_at):
    if image == "second":
      encode_started.set()
      assert release_encoder.wait(timeout=2.0)
    return prepared_frame(created_at)

  def fail_send(_data, **_kwargs):
    send_started.set()
    assert allow_send_failure.wait(timeout=2.0)
    raise RuntimeError("unexpected USB transport failure")

  def reset_transport():
    original_close()
    connection_reset.set()

  state.encoder.encode.side_effect = encode
  state.transport.send_access_unit.side_effect = fail_send
  state.transport.close.side_effect = reset_transport
  pipeline = ClusterDisplayPipeline(state.adapter, prepare_frame=state.adapter.prepare_frame)
  pipeline.start()
  try:
    pipeline.push("first", created_at=10.0)
    assert send_started.wait(timeout=1.0)
    pipeline.push("second", created_at=10.0)
    assert encode_started.wait(timeout=1.0)
    allow_send_failure.set()
    assert connection_reset.wait(timeout=1.0)

    # Recovery runs on the sender while encode() owns the native handle.
    # The final close is only legal after pipeline.close() has joined workers.
    assert not release_encoder.is_set()
    state.encoder.close.assert_not_called()
    assert not state.adapter.connected
    assert pipeline.get_stats()["send_failures"] == 1
  finally:
    allow_send_failure.set()
    release_encoder.set()
    assert pipeline.close()
    state.adapter.close()
  state.encoder.close.assert_called_once_with()
  assert state.transport.close.call_count == 2
