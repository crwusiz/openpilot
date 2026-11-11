"""Reduce the existing livestream's load when the cluster link stays congested."""

import os

from openpilot.selfdrive.addon.cluster.cluster_logging import flog


BITRATE_PARAM = "LivestreamEncoderBitrate"
MIN_BITRATE = 1_500_000
SAMPLE_INTERVAL = 0.5
DOWN_INTERVAL = 2.0
RECOVERY_SECONDS = 20.0
REPORT_MAX_AGE = 3.0
TCP_PRESSURE_SECONDS = 1.0
RECOVERY_DRAIN_LIMIT = 0.25


def default_bitrate():
  try:
    value = int(os.environ.get("STREAM_BITRATE", "5000000"))
    return value if 0 < value <= 100_000_000 else 5_000_000
  except ValueError:
    return 5_000_000


class ClusterBitrateController:
  """One owner-loop controller per server, retained across ICE reconnects.

  The hardware stream is shared with dashboard WebRTC. An external parameter
  change relinquishes ownership for the rest of this server's lifetime. Never
  remove the parameter on release: encoderd ignores an absent value and would
  otherwise keep encoding at the last reduced rate.
  """

  def __init__(self, params):
    self.params = params
    self.disabled = params is None
    self.ceiling = self.current = default_bitrate()
    self.expected = self.pending = None
    self.pending_at = None
    self.changed = False
    self.last_change = None
    self.reset_connection()
    if not self.disabled:
      try:
        self.expected = params.get(BITRATE_PARAM)
        if self.expected is not None:
          if type(self.expected) is not int or not 0 < self.expected <= 100_000_000:
            raise ValueError("Invalid existing bitrate")
          self.ceiling = self.current = self.expected
      except (OSError, RuntimeError, ValueError, TypeError) as error:
        self._disable(type(error).__name__)

  def reset_connection(self):
    self.last_report = self.report_at = self.healthy_since = None
    self.loss_samples = 0
    self.report_sequence_bits = None
    self.report_samples = self.report_wraps = 0
    self.drain_age_s = 0.0
    self.recovery_blocked = "no_report"

  def _disable(self, reason):
    self.disabled = True
    self.recovery_blocked = reason
    flog(f"[CLUSTER_H264_BITRATE] action=yield | reason={reason}")

  def _owns_parameter(self, now):
    value = self.params.get(BITRATE_PARAM)
    if self.pending is not None and value == self.pending:
      self.expected, self.pending = self.pending, None
    if value != self.expected:
      self.changed = False
      self._disable("external_parameter_change")
      return False
    if self.pending is not None:
      if now - self.pending_at >= 5.0:
        self._disable("parameter_write_unconfirmed")
      return False
    return True

  def _publish(self, value, now, reason):
    self.params.put(BITRATE_PARAM, value, block=False)
    before, self.current = self.current, value
    self.pending, self.pending_at = value, now
    self.changed, self.last_change = True, now
    self.healthy_since = None
    self.loss_samples = 0
    flog(f"[CLUSTER_H264_BITRATE] action=adapt | from_bps={before} | to_bps={value} | "
         + f"ceiling_bps={self.ceiling} | reason={reason}")

  def sample(self, now, report, queued_bytes, *, drain_age_s=None, degraded=False):
    if self.disabled:
      return
    try:
      if not self._owns_parameter(now):
        return
      # A 0.5s poll can hit a different short 20Hz write on every sample.
      # Only the age of this exact pending drain proves sustained pressure.
      self.drain_age_s = drain_age_s if type(drain_age_s) in (int, float) and 0 <= drain_age_s <= 60 else 0.0
      tcp_pressure = queued_bytes > 0 and self.drain_age_s >= TCP_PRESSURE_SECONDS

      # aiortc puts its 16-bit max_seq in the RR's 32-bit sequence field.
      # Accept its wrap as well as genuine extended 32-bit reports. Cached
      # and backwards reports still cannot establish freshness or recovery.
      fresh, loss = False, None
      if report is not None:
        signature = (report.ssrc, report.highest_seq_no)
        fraction = report.fraction_lost
        if (type(signature[0]) is int and 0 <= signature[0] < 1 << 32
            and type(signature[1]) is int and 0 <= signature[1] < 1 << 32
            and type(fraction) is int and 0 <= fraction <= 255):
          same_source = self.last_report is not None and signature[0] == self.last_report[0]
          previous = self.last_report[1] if same_source else None
          bits = 16 if signature[1] <= 0xFFFF and (previous is None or previous <= 0xFFFF) else 32
          advancing = previous is None or 0 < (signature[1] - previous) % (1 << bits) < (1 << (bits - 1))
          if advancing:
            if self.last_report is not None and not same_source:
              self.healthy_since = None
              self.loss_samples = 0
            fresh, loss = True, fraction / 256
            self.report_wraps += int(previous is not None and signature[1] < previous)
            self.report_samples += 1
            self.report_sequence_bits = bits
            self.last_report, self.report_at = signature, now
            self.loss_samples = self.loss_samples + 1 if loss >= 0.05 else 0

      reason = "tcp_backpressure" if tcp_pressure else "rtcp_loss" if fresh and (loss >= 0.1 or self.loss_samples >= 2) else None
      if reason is not None:
        self.healthy_since = None
        self.recovery_blocked = reason
        if self.last_change is None or now - self.last_change >= DOWN_INTERVAL:
          target = max(min(MIN_BITRATE, self.ceiling), self.current * 3 // 4)
          if target < self.current:
            self._publish(target, now, reason)
        return

      blocked = ("driving_guard" if degraded else "tcp_drain" if self.drain_age_s >= RECOVERY_DRAIN_LIMIT
                 else "no_report" if self.report_at is None or now - self.report_at > REPORT_MAX_AGE
                 else "rtcp_loss" if fresh and loss > 0.01 else None)
      if blocked is not None:
        self.healthy_since = None
        self.recovery_blocked = blocked
      elif fresh and queued_bytes == 0:
        self.recovery_blocked = "none"
        if self.healthy_since is None:
          self.healthy_since = now
        elif now - self.healthy_since >= RECOVERY_SECONDS and self.current < self.ceiling:
          self._publish(min(self.ceiling, self.current + 500_000), now, "link_recovered")
      elif queued_bytes > 0:
        # Do not raise while a write is pending, but a brief normal write must
        # not erase all earlier healthy reports and permanently pin quality.
        self.recovery_blocked = "write_pending"
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as error:
      # Auxiliary tuning must not terminate a working video connection.
      self._disable(type(error).__name__)

  def status_fields(self, now):
    recovery_s = 0.0 if self.healthy_since is None else max(0.0, now - self.healthy_since)
    report_age = "none" if self.report_at is None else f"{max(0.0, now - self.report_at) * 1000:.1f}"
    return (f" | bitrate_target_bps={self.current} | bitrate_recovery_s={recovery_s:.1f}"
            + f" | bitrate_blocked={self.recovery_blocked} | bitrate_rr_age_ms={report_age}"
            + f" | bitrate_rr_seq_bits={self.report_sequence_bits or 'none'}"
            + f" | bitrate_rr_samples={self.report_samples} | bitrate_rr_wraps={self.report_wraps}"
            + f" | state_drain_age_ms={self.drain_age_s * 1000:.1f}")

  def close(self):
    if not self.changed:
      return
    try:
      value = self.params.get(BITRATE_PARAM)
      # A queued write can still be unconfirmed during shutdown. Restore after
      # it on the same Params owner, but preserve another writer's new value.
      if value == self.expected or (self.pending is not None and value == self.pending):
        self.params.put(BITRATE_PARAM, self.ceiling, block=False)
        flog(f"[CLUSTER_H264_BITRATE] action=restore | to_bps={self.ceiling}")
    except (OSError, RuntimeError, ValueError, TypeError) as error:
      flog(f"[CLUSTER_H264_BITRATE] action=restore_failed | reason={type(error).__name__}")
    finally:
      self.changed = False
      self.disabled = True
