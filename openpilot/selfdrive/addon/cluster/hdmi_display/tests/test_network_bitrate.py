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


def test_only_sustained_overlapping_video_and_state_pressure_can_reduce_before_rtcp_loss():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  cached = report(100)
  controller.sample(0, cached, 0)
  controller.sample(0.3, cached, 2400, drain_age_s=0.3, send_pressure_age_s=0.05)
  controller.sample(0.6, cached, 0, drain_age_s=0.3, send_pressure_age_s=0.3)
  controller.sample(0.9, cached, 2400, drain_age_s=0.05, send_pressure_age_s=0.6)
  controller.sample(1.2, cached, 2400, drain_age_s=0.7)
  assert params.writes == []

  controller.sample(1.5, cached, 2400, drain_age_s=0.4, send_pressure_age_s=0.35)
  assert params.writes == [3_750_000]
  assert controller.recovery_blocked == "video_tcp_backpressure"
  assert "reason=video_tcp_backpressure" in budget.flog.call_args.args[0]
  assert "video_send_pressure_age_ms=350.0" in controller.status_fields(1.5)
  controller.sample(2, report(200, 40), 2400, drain_age_s=0.6, send_pressure_age_s=0.5)
  assert params.writes == [3_750_000]  # The late RR cannot immediately cause a second reduction.


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


@pytest.mark.parametrize("interrupt", ["no_report", "repeat_report", "loss", "degraded", "video_pressure"])
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
                      0, send_pressure_age_s=0.3 if interrupt == "video_pressure" else None, degraded=interrupt == "degraded")
  assert params.writes == [3_750_000]
  if interrupt == "video_pressure":
    assert controller.recovery_blocked == "video_send_pressure"
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


def _healthy(controller, start, end):
  for second in range(start, end + 1):
    controller.sample(second, report(second * 400), 0)


def _failed_probe():
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 40), 0)
  _healthy(controller, 1, 21)
  assert params.writes == [3_750_000, 4_250_000]
  controller.sample(24, report(24 * 400, 40), 0)
  assert controller.current == 3_187_500 and controller.recovery_wait_s == 40
  return controller, params


@pytest.mark.parametrize("cause", ["rtcp_loss", "tcp_backpressure", "video_tcp_backpressure"])
def test_repeated_failed_probes_wait_longer_without_delaying_congestion_reductions(cause):
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 40), 0)
  _healthy(controller, 1, 21)
  probe_at = 21
  for failures, wait in enumerate((40, 80, 120, 120), 1):
    failed_at, previous = probe_at + 3, controller.current
    controller.sample(failed_at, report(failed_at * 400, 40 if cause == "rtcp_loss" else 0),
                      0 if cause == "rtcp_loss" else 1600,
                      drain_age_s={"tcp_backpressure": 1.0, "video_tcp_backpressure": 0.5}.get(cause),
                      send_pressure_age_s=0.35 if cause == "video_tcp_backpressure" else None)
    assert controller.current == max(budget.MIN_BITRATE, previous * 3 // 4)
    assert controller.recovery_wait_s == wait and controller.probe_failures == failures
    assert controller.probe_at is None
    writes = len(params.writes)
    _healthy(controller, failed_at + 1, failed_at + wait)
    assert len(params.writes) == writes  # Stable at the lower rate does not erase the penalty.
    probe_at = failed_at + wait + 1
    controller.sample(probe_at, report(probe_at * 400), 0)
    assert len(params.writes) == writes + 1 and controller.probe_at == probe_at
    assert params.writes[-1] == min(controller.ceiling, params.writes[-2] + 500_000)


def test_one_probe_is_penalized_once_and_cached_bad_reports_cannot_repeat_reductions():
  controller, params = _failed_probe()
  controller.sample(26, report(26 * 400, 40), 0)  # Actual additional down, without another up.
  assert params.writes[-1] == 2_390_625
  for second in range(27, 40):
    controller.sample(second, report(26 * 400, 40), 0)
  assert len(params.writes) == 4 and controller.probe_failures == 1
  assert controller.recovery_wait_s == 40


@pytest.mark.parametrize("delay,failures", [(30.0, 1), (30.001, 0)])
def test_only_congestion_within_the_probe_window_increases_recovery_wait(delay, failures):
  params = Params()
  controller = budget.ClusterBitrateController(params)
  controller.sample(0, report(0, 40), 0)
  _healthy(controller, 1, 21)
  for second in range(22, 51):
    controller.sample(second, None, 0)
  controller.sample(21 + delay, report(30_000, 40), 0)
  assert controller.current == 3_187_500  # Congestion is reduced on either side of the boundary.
  assert controller.probe_failures == failures
  assert controller.recovery_wait_s == (40 if failures else 20)
  assert controller.probe_at is None


def test_replaced_media_source_does_not_charge_its_loss_to_the_previous_probe():
  controller, params = _failed_probe()
  _healthy(controller, 25, 65)
  assert controller.probe_at == 65
  controller.sample(67, report(100, 40, ssrc=2), 0)  # Retain the existing two-second down interval.
  assert params.writes[-1] == 3_687_500 * 3 // 4
  assert controller.probe_at is None and controller.healthy_since is None
  assert controller.probe_failures == 1 and controller.recovery_wait_s == 40


def test_backoff_relaxes_only_after_the_raised_probe_has_thirty_healthy_seconds():
  controller, params = _failed_probe()
  _healthy(controller, 25, 64)
  assert controller.recovery_wait_s == 40 and len(params.writes) == 3
  controller.sample(65, report(65 * 400), 0)
  assert controller.current == 3_687_500 and controller.probe_at == 65
  _healthy(controller, 66, 95)
  assert len(params.writes) == 4 and controller.recovery_wait_s == 40
  controller.sample(96, report(96 * 400), 0)
  assert controller.recovery_wait_s == 20 and controller.probe_failures == 1
  assert controller.current == 4_187_500 and controller.probe_at == 96
  _healthy(controller, 97, 117)
  assert controller.current == 4_687_500  # Normal twenty-second increases have resumed.
  _healthy(controller, 118, 138)
  assert controller.current == controller.ceiling == 5_000_000


@pytest.mark.parametrize("interrupt", ["loss", "degraded", "slow_drain", "no_report", "cached_report"])
def test_probe_validation_requires_uninterrupted_fresh_health(interrupt):
  controller, params = _failed_probe()
  _healthy(controller, 25, 94)  # Up at65, then29 healthy fresh reports.
  assert controller.probe_at == 65 and controller.recovery_wait_s == 40
  for second in range(95, 101):
    current_report = (None if interrupt == "no_report" else
                      report((94 if interrupt == "cached_report" else second) * 400, 4 if interrupt == "loss" else 0))
    controller.sample(second, current_report, 1600 if interrupt == "slow_drain" else 0,
                      drain_age_s=0.3 if interrupt == "slow_drain" else None, degraded=interrupt == "degraded")
  assert len(params.writes) == 4 and controller.recovery_wait_s == 40
  _healthy(controller, 101, 130)
  assert len(params.writes) == 4 and controller.recovery_wait_s == 40
  controller.sample(131, report(131 * 400), 0)
  assert controller.recovery_wait_s == 20 and len(params.writes) == 5


def test_reconnect_keeps_backoff_but_requires_new_reports_and_clears_probe_ownership():
  controller, params = _failed_probe()
  _healthy(controller, 25, 65)
  assert controller.probe_at == 65
  controller.reset_connection()
  assert controller.probe_at is None and controller.healthy_since is None
  assert controller.recovery_wait_s == 40 and controller.probe_failures == 1
  controller.sample(100, None, 0)
  _healthy(controller, 101, 140)
  assert len(params.writes) == 4 and controller.recovery_wait_s == 40
  controller.sample(141, report(141 * 400), 0)
  assert controller.current == 4_187_500
  fields = controller.status_fields(142)
  assert "bitrate_recovery_wait_s=40.0" in fields and "bitrate_probe_failures=1" in fields
  assert "bitrate_probe_age_s=1.0" in fields


def test_external_parameter_owner_is_preserved_even_after_failed_probes():
  controller, params = _failed_probe()
  params.value = 777_777
  controller.sample(25, report(25 * 400, 40), 0)
  controller.close()
  assert controller.disabled and len(params.writes) == 3
  assert params.value == 777_777 and controller.probe_failures == 1


def test_backoff_reduces_overshooting_probes_in_a_synthetic_nine_hundred_second_link():
  class FixedRecoveryController(budget.ClusterBitrateController):
    def _publish(self, value, now, reason):
      super()._publish(value, now, reason)
      # Baseline: the same existing loss/down/quality rules, with the old
      # fixed20s recovery and no memory of a failed higher-rate probe.
      self.probe_at = None
      self.recovery_wait_s = budget.RECOVERY_SECONDS

  def replay(controller_type):
    params = Params()
    controller = controller_type(params)
    for second in range(900):
      # This hypothetical link reports congestion above2.5Mbps; it is a
      # deterministic controller experiment, not a decoder/FPS simulation.
      controller.sample(second, report(second * 400, 40 if controller.current > 2_500_000 else 0), 0)
    previous, increases, decreases = controller.ceiling, 0, 0
    for value in params.writes:
      increases += value > previous
      decreases += value < previous
      previous = value
    assert budget.MIN_BITRATE <= controller.current <= controller.ceiling
    return increases, decreases

  fixed, adaptive = replay(FixedRecoveryController), replay(budget.ClusterBitrateController)
  assert adaptive[0] < fixed[0] and adaptive[1] < fixed[1]
