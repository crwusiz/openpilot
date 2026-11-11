import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display import network_bitrate as budget


class Params:
  def __init__(self, value=None, *, deferred=False):
    self.value, self.deferred = value, deferred
    self.writes = []

  def get(self, key):
    assert key == budget.BITRATE_PARAM
    return self.value

  def put(self, key, value, *, block):
    assert key == budget.BITRATE_PARAM and block is False
    self.writes.append(value)
    if not self.deferred:
      self.value = value


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
  monkeypatch.delenv("STREAM_BITRATE", raising=False)
  monkeypatch.setattr(budget, "flog", Mock())


def report(sequence, fraction=0, ssrc=1):
  return SimpleNamespace(ssrc=ssrc, highest_seq_no=sequence, fraction_lost=fraction)


def test_five_second_tcp_stall_reduces_hardware_budget_before_reconnect_deadline():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  # The Nov 9 failure has queued TCP state and ongoing video. Keep one write
  # outstanding; reducing encoder load needs no accumulated scene/frame queue.
  for sample in range(10):
    controller.sample(sample / 2, report(sample * 400), 1600, drain_age_s=sample / 2)
  assert params.writes == [3_750_000, 2_812_500]
  assert controller.current == 2_812_500
  assert not controller.disabled


def test_short_tcp_pauses_do_not_reduce_quality_or_require_receiver_feedback():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  for sample in range(50):
    controller.sample(sample / 2, None, 1800 if sample % 2 else 0)
  assert params.writes == []


def test_high_loss_reduces_immediately_and_cannot_repeat_on_one_cached_report():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  bad = report(1000, 142)  # 55.47%, observed in the afternoon report.
  for second in range(40):
    controller.sample(second, bad, 0)
  assert params.writes == [3_750_000]
  assert controller.healthy_since is None


def test_moderate_loss_requires_two_new_reports_and_recovery_respects_ceiling():
  params = Params(4_000_000)
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(100, 15), 0)
  controller.sample(0.5, report(100, 15), 0)
  assert params.writes == []
  controller.sample(1, report(200, 15), 0)
  assert params.writes == [3_000_000]
  for second in range(2, 64):
    controller.sample(second, report(200 + second * 400), 0)
  assert params.writes == [3_000_000, 3_500_000, 4_000_000]


def test_out_of_order_reports_do_not_count_as_new_loss_and_extended_sequence_wrap_is_valid():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0xFFFF_FFF0), 0)
  controller.sample(1, report(0xFFFF_FF00, 142), 0)
  assert params.writes == []
  controller.sample(2, report(100, 142), 0)
  assert params.writes == [3_750_000]


@pytest.mark.parametrize("previous,current,fresh,bits,wraps", [
  (65530, 10, True, 16, 1),
  (10, 65530, False, 16, 0),
  (100, 100, False, 16, 0),
  (0, 32768, False, 16, 0),  # Half the serial range is ambiguous.
  (65530, 65546, True, 32, 0),
  (65546, 65530, False, 32, 0),
  (0xFFFF_FFF0, 100, True, 32, 1),
  (100, 0xFFFF_FFF0, False, 16, 0),
])
def test_raw_and_extended_rr_sequences_keep_wraps_and_backwards_reports_distinct(previous, current, fresh, bits, wraps):
  controller = budget.ClusterBitrateController(Params())
  controller.sample(0, report(previous), 0)
  controller.sample(1, report(current), 0)
  assert controller.report_at == (1 if fresh else 0)
  assert controller.report_samples == (2 if fresh else 1)
  assert controller.report_sequence_bits == bits and controller.report_wraps == wraps


@pytest.mark.parametrize("sequence", [-1, 1 << 32, True, "10"])
def test_invalid_sequence_cannot_refresh_a_stale_receiver_report(sequence):
  controller = budget.ClusterBitrateController(Params())
  controller.sample(0, report(65530), 0)
  controller.sample(4, report(sequence), 0)
  assert controller.report_at == 0 and controller.report_samples == 1
  assert controller.recovery_blocked == "no_report" and not controller.disabled


def test_cached_report_after_wrap_never_becomes_repeated_loss_or_a_fresh_recovery_sample():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(65530), 0)
  controller.sample(1, report(100, 40), 0)
  for second in range(2, 40):
    controller.sample(second, report(100, 40), 0)
  assert params.writes == [3_750_000]
  assert controller.report_at == 1 and controller.report_samples == 2 and controller.report_wraps == 1
  assert controller.recovery_blocked == "no_report"
  controller.reset_connection()
  assert controller.report_samples == controller.report_wraps == 0 and controller.report_sequence_bits is None


def test_new_ssrc_does_not_inherit_previous_sources_recovery_window():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(65500, 40), 0)
  for second in range(1, 20):
    controller.sample(second, report((65500 + second * 400) & 0xFFFF), 0)
  controller.sample(20, report(100, ssrc=2), 0)
  assert params.writes == [3_750_000] and controller.healthy_since == 20
  for second in range(21, 41):
    controller.sample(second, report(100 + (second - 20) * 400, ssrc=2), 0)
  assert params.writes == [3_750_000, 4_250_000]


def test_aiortc_wire_reports_keep_loss_control_and_quality_recovery_after_16_bit_wrap(monkeypatch):
  from aiortc.rtp import RtcpReceiverInfo, RtcpRrPacket, RtpPacket
  from aiortc.rtcrtpreceiver import StreamStatistics

  # Load the production pure RTCP decoder without importing the native WebRTC
  # binding through teleoprtc.__init__ on a non-C4 host.
  spec = importlib.util.spec_from_file_location("cluster_bitrate_wire_decoder",
                                               Path(__file__).resolve().parents[6] / "teleoprtc_repo/teleoprtc/decoder.py")
  decoder = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, spec.name, decoder)
  spec.loader.exec_module(decoder)
  statistics = StreamStatistics(90000)
  params = Params()
  controller = budget.ClusterBitrateController(params)

  def receive(sequence):
    statistics.add(RtpPacket(sequence_number=sequence & 0xFFFF, timestamp=0, ssrc=123, payload=b"x"))

  def feedback():
    # This is the RR construction used by aiortc's receiver. Its highest
    # sequence field is max_seq, even though the wire field has 32 bits.
    packet = RtcpRrPacket(ssrc=456, reports=[RtcpReceiverInfo(
      ssrc=123, fraction_lost=statistics.fraction_lost, packets_lost=statistics.packets_lost,
      highest_sequence=statistics.max_seq, jitter=statistics.jitter, lsr=0, dlsr=0,
    )])
    return decoder._decode_receiver_reports(bytes(packet))[0]

  receive(65500)
  receive(65510)  # Initial real packet loss reduces the requested bitrate.
  controller.sample(0, feedback(), 0)
  assert params.writes == [3_750_000]
  sequence = 65510
  for second in range(1, 201):
    for _ in range(400):  # Roughly the packet cadence of the live camera.
      sequence += 1
      receive(sequence)
    controller.sample(second, feedback(), 0)
    assert controller.report_at == second
  assert statistics.cycles == 2 * 65536
  assert params.writes == [3_750_000, 4_250_000, 4_750_000, 5_000_000]
  assert controller.current == 5_000_000 and not controller.disabled
  fields = controller.status_fields(200)
  assert "bitrate_rr_seq_bits=16" in fields and "bitrate_rr_wraps=2" in fields
  assert "bitrate_rr_samples=201" in fields and "bitrate_rr_age_ms=0.0" in fields
  # Loss feedback after two wraps still works; do not merely refresh the age.
  sequence += 200
  receive(sequence)
  controller.sample(201, feedback(), 0)
  assert params.writes[-1] == 3_750_000


@pytest.mark.parametrize("ceiling", [1_000_000, 1_500_000, 5_000_000])
def test_persistent_pressure_has_a_floor_and_never_raises_a_lower_existing_cap(ceiling):
  params = Params(ceiling)
  controller = budget.ClusterBitrateController(params)
  for second in range(60):
    controller.sample(second, report(second, 142), 1600)
  assert controller.current == min(ceiling, budget.MIN_BITRATE)
  assert all(min(ceiling, budget.MIN_BITRATE) <= value <= ceiling for value in params.writes)


@pytest.mark.parametrize("interrupt", ["no_report", "repeat_report", "loss", "degraded"])
def test_recovery_requires_continuous_fresh_healthy_reports(interrupt):
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 40), 0)
  for second in range(1, 20):
    controller.sample(second, report(second), 0)
  # Interrupt just before the required 20 healthy seconds have elapsed.
  for second in range(20, 27):
    controller.sample(second, None if interrupt == "no_report" else report(19 if interrupt == "repeat_report" else second,
                                                                        4 if interrupt == "loss" else 0),
                      0, degraded=interrupt == "degraded")
  assert params.writes == [3_750_000]
  for second in range(27, 47):
    controller.sample(second, report(second), 0)
  assert params.writes == [3_750_000]
  controller.sample(47, report(47), 0)
  assert params.writes == [3_750_000, 4_250_000]


@pytest.mark.parametrize("measured", [False, True])
def test_polling_different_short_writes_cannot_manufacture_a_continuous_tcp_stall(measured):
  params = Params()
  controller = budget.ClusterBitrateController(params)
  for tick in range(100):
    controller.sample(tick / 2, report(tick * 400), 1600, drain_age_s=0.01 if measured else None)
  assert params.writes == []
  assert controller.current == 5_000_000


def test_transient_writes_hold_but_do_not_erase_healthy_quality_recovery():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 40), 0)
  for second in range(1, 23):
    # A short TCP write is often in progress when the periodic poll runs.
    # Reports at 21/22 arrive during writes: wait, never raise into that write.
    pending = second % 3 != 1 or second in (21, 22)
    controller.sample(second, report(second * 400), 1600 if pending else 0, drain_age_s=0.01 if pending else None)
  assert params.writes == [3_750_000]
  controller.sample(23, report(23 * 400), 0)
  assert params.writes == [3_750_000, 4_250_000]


def test_one_slow_drain_resets_recovery_and_reduces_only_after_its_actual_age_limit():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 40), 0)
  for second in range(1, 20):
    controller.sample(second, report(second * 400), 0)
  controller.sample(20, report(20 * 400), 1600, drain_age_s=0.3)
  assert controller.healthy_since is None and params.writes == [3_750_000]
  controller.sample(20.5, report(21 * 400), 1600, drain_age_s=0.8)
  assert params.writes == [3_750_000]
  controller.sample(21, report(22 * 400), 1600, drain_age_s=1.3)
  assert params.writes == [3_750_000, 2_812_500]
  fields = controller.status_fields(21)
  assert "bitrate_target_bps=2812500" in fields and "bitrate_blocked=tcp_backpressure" in fields
  assert "state_drain_age_ms=1300.0" in fields and "bitrate_rr_age_ms=0.0" in fields


def test_reconnect_keeps_reduced_rate_and_requires_new_recovery_history():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(100, 40), 0)
  controller.sample(1, report(200), 0)
  controller.reset_connection()
  controller.sample(100, None, 0)
  assert controller.current == 3_750_000 and params.writes == [3_750_000]
  controller.sample(101, report(100), 0)  # A new SSRC may reuse sequence values.
  assert controller.healthy_since == 101
  controller.close()
  controller.close()
  assert params.writes == [3_750_000, 5_000_000]


@pytest.mark.parametrize("external", [None, 500_000, 4_000_000])
def test_dashboard_or_other_writer_takes_ownership_without_parameter_fighting(external):
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(100, 40), 0)
  controller.sample(0.5, report(200), 0)  # Observe our previous write.
  params.value = external
  controller.sample(1, report(300, 142), 1600)
  assert controller.disabled
  params.value = 3_750_000  # A later external change still must not be restored.
  controller.close()
  assert params.writes == [3_750_000]


@pytest.mark.parametrize("external", [None, 700_000])
def test_shutdown_preserves_external_changes_even_before_next_sample(external):
  params = Params(4_000_000)
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(100, 40), 0)
  controller.sample(0.5, report(200), 0)
  params.value = external
  controller.close()
  assert params.writes == [3_000_000]


def test_pending_async_write_is_not_an_external_change_and_shutdown_restores_last():
  params = Params(deferred=True)
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(100, 40), 0)
  controller.sample(2, report(200, 40), 1600)
  assert not controller.disabled and params.writes == [3_750_000]
  params.value = 3_750_000
  controller.sample(3, report(300, 40), 1600)
  assert params.writes == [3_750_000, 2_812_500]
  controller.close()  # The second write has not been persisted yet.
  assert params.writes == [3_750_000, 2_812_500, 5_000_000]


def test_unconfirmed_write_does_not_queue_unbounded_updates():
  params = Params(deferred=True)
  controller = budget.ClusterBitrateController(params)
  for second in range(40):
    controller.sample(second, report(second, 142), 1600)
  assert controller.disabled and params.writes == [3_750_000]
  controller.close()
  assert params.writes == [3_750_000, 5_000_000]


@pytest.mark.parametrize("existing", [0, -1, True, "5000000", 100_000_001])
def test_unknown_parameter_values_disable_auxiliary_tuning(existing):
  params = Params(existing)
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 142), 1600)
  controller.close()
  assert controller.disabled and params.writes == []


@pytest.mark.parametrize("stage", ["read", "write"])
def test_parameter_failure_does_not_raise_into_the_video_session(stage):
  params = Params()
  controller = budget.ClusterBitrateController(params)
  setattr(params, "get" if stage == "read" else "put", Mock(side_effect=RuntimeError("unavailable")))
  controller.sample(0, report(0, 142), 1600)
  controller.close()
  assert controller.disabled


@pytest.mark.parametrize("value,expected", [("7000000", 7_000_000), ("bad", 5_000_000), ("0", 5_000_000), ("100000001", 5_000_000)])
def test_restore_uses_the_hardware_stream_environment_default(monkeypatch, value, expected):
  monkeypatch.setenv("STREAM_BITRATE", value)
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 142), 0)
  controller.close()
  assert params.writes[-1] == expected
