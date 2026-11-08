"""Cluster-only guards around the native WebRTC video sender.

Keep the existing ICE/SRTP, RTCP reports, PLI and NACK handlers. Validate and
normalize elementary H.264 before entering their native packetizer, and set
the RTP timestamp explicitly rather than crossing the FrameInfo binding.
"""

import asyncio
import re
import struct
import time

from teleoprtc.stream import RTCSessionDescription, RtcpNackResponder, WebRTCAnswerStream

from openpilot.selfdrive.addon.cluster.cluster_logging import flog


START_CODE = re.compile(b"\x00\x00\x00\x01|\x00\x00\x01")
MAX_ACCESS_UNIT_BYTES = 4 * 1024 * 1024
MAX_NAL_UNITS = 256
MAX_RTCP_BYTES = 8192
MAX_RTCP_BLOCKS = 64
MAX_NACK_REQUESTS = 512
# A late original-sequence retransmission can reset aiortc's video buffer
# after it has advanced by 100 packets. Keep recent repairs, without sending
# a much older reference chain back into an already recovered live stream.
NACK_HISTORY_PACKETS = 96


def parse_nack_feedback(message, media_ssrc):
  """Return bounded observed requests, never a retransmission-success count.

  Validate the complete compound packet before publishing any NACK prefix.
  Foreign streams and other valid RTCP types remain uncounted.
  """
  if not isinstance(message, bytes) or not message:
    return 0, (), "invalid"
  if len(message) > MAX_RTCP_BYTES:
    return 0, (), "limited"
  offset, blocks, packets = 0, 0, 0
  requested = []
  while offset < len(message):
    if blocks >= MAX_RTCP_BLOCKS:
      return 0, (), "limited"
    if len(message) - offset < 4:
      return 0, (), "invalid"
    flags, packet_type, words = struct.unpack_from("!BBH", message, offset)
    end = offset + (words + 1) * 4
    if flags >> 6 != 2 or not 192 <= packet_type <= 223 or end > len(message):
      return 0, (), "invalid"
    payload_end = end
    if flags & 0x20:
      padding = message[end - 1]
      if end != len(message) or not 0 < padding <= end - offset - 4:
        return 0, (), "invalid"
      payload_end -= padding
    if packet_type == 205 and flags & 0x1F == 1:
      if payload_end - offset < 16 or (payload_end - offset - 12) % 4:
        return 0, (), "invalid"
      if struct.unpack_from("!I", message, offset + 8)[0] == media_ssrc:
        packets += 1
        for position in range(offset + 12, payload_end, 4):
          pid, mask = struct.unpack_from("!HH", message, position)
          if len(requested) + 1 + mask.bit_count() > MAX_NACK_REQUESTS:
            return 0, (), "limited"
          requested.append(pid)
          requested.extend((pid + bit + 1) & 0xFFFF for bit in range(16) if mask & (1 << bit))
    offset = end
    blocks += 1
  return packets, tuple(requested), None


def normalize_access_unit(data):
  """Return checked long-start-code Annex B and whether it contains an IDR.

  The native packetizer is configured for four-byte start codes. Qualcomm
  buffers may contain both Annex B separator lengths; never bundle a short
  separator into another NAL's RTP payload. Empty/truncated units are rejected
  before native code sees them. This checks framing, not slice decodability.
  """
  if not data or len(data) > MAX_ACCESS_UNIT_BYTES:
    raise ValueError("H.264 access unit outside byte budget")
  units, keyframe, previous = [], False, None
  for count, match in enumerate(START_CODE.finditer(data), 1):
    if count > MAX_NAL_UNITS:
      raise ValueError("H.264 access unit has too many NAL units")
    if previous is None:
      if any(data[:match.start()]):
        raise ValueError("Unexpected bytes before H.264 start code")
    else:
      units.append(data[previous:match.start()].rstrip(b"\x00"))
    previous = match.end()
  if previous is None:
    raise ValueError("H.264 access unit has no Annex B start code")
  units.append(data[previous:].rstrip(b"\x00"))
  for unit in units:
    if not unit or unit[0] & 0x80 or not 1 <= (unit[0] & 0x1F) <= 23:
      raise ValueError("Empty, truncated or invalid elementary H.264 NAL unit")
    if len(unit) < 2 and (unit[0] & 0x1F) not in (10, 11):
      raise ValueError("Truncated elementary H.264 NAL unit")
    keyframe |= (unit[0] & 0x1F) == 5
  return b"".join(b"\x00\x00\x00\x01" + unit for unit in units), keyframe


class CaptureClock:
  """Use camera EOF intervals so event-loop stalls do not jitter RTP time."""

  def __init__(self):
    self.origin_ns = self.last_pts = None

  def pts(self, eof_ns, fallback):
    if type(eof_ns) is int and eof_ns > 0:
      if self.origin_ns is None:
        self.origin_ns = eof_ns
      value = (eof_ns - self.origin_ns) * 90000 // 1_000_000_000
    else:
      value = int(fallback or 0)
    if self.last_pts is not None and value <= self.last_pts:
      # Rebase if the camera restarts its capture clock. Do not emit a
      # backwards/equal RTP timestamp or accelerate the replacement stream.
      value = self.last_pts + 4500
      if type(eof_ns) is int and eof_ns > 0:
        self.origin_ns = eof_ns - value * 1_000_000_000 // 90000
    self.last_pts = value
    return value


class ClusterH264Stream(WebRTCAnswerStream):
  def __init__(self, offer_sdp, track):
    super().__init__(RTCSessionDescription(sdp=offer_sdp, type="offer"), consumed_camera_types=[], consume_audio=False,
                     video_producer_tracks=[track], audio_producer_tracks=[], should_add_data_channel=False)
    self._native_video_handlers = []
    self._cluster_rtp_configs = {}
    self._cluster_rtcp_stats = dict.fromkeys(("nack_packets", "nack_requested", "nack_distance_ge100",
                                            "nack_distance_unavailable", "rtcp_invalid", "rtcp_limited"), 0)
    self._nack_distance_max = None

  def _add_producer_tracks(self, remote_sdp=None):
    super()._add_producer_tracks(remote_sdp)
    # Retain the native chain's Python wrappers until this stream is closed,
    # including PLI callbacks. Shutdown still joins senders before closing ICE.
    for rtc_track, _producer, _config in self._track_state:
      self._cluster_rtp_configs[_config.ssrc] = _config
      handlers = []
      handler = rtc_track.get_media_handler()
      for _index in range(8):
        if handler is None:
          break
        handlers.append(handler)
        handler = handler.next()
      else:
        raise RuntimeError("Unexpected native H.264 media handler chain")
      self._native_video_handlers.extend(self._configure_nack_history(handlers))

  @staticmethod
  def _configure_nack_history(handlers):
    # Configure only this cluster's native chain, before connection/sending.
    # Preserve packetization, SR, PLI and any downstream handlers. Retain the
    # replaced wrapper until shutdown, just like all other native wrappers.
    indices = [index for index, handler in enumerate(handlers) if isinstance(handler, RtcpNackResponder)]
    if len(indices) != 1 or indices[0] == 0:
      flog("[CLUSTER_H264_NACK_POLICY] action=keep_default | reason=unrecognized_chain")
      return handlers
    index = indices[0]
    previous = handlers[index]
    replacement = RtcpNackResponder(max_size=NACK_HISTORY_PACKETS)
    if index + 1 < len(handlers):
      replacement.set_next(handlers[index + 1])
    handlers[index - 1].set_next(replacement)
    handlers[index] = replacement
    flog(f"[CLUSTER_H264_NACK_POLICY] cache_packets={NACK_HISTORY_PACKETS} | action=recent_repairs | native=1")
    return [*handlers, previous]

  def _update_receiver_report(self, camera_type, ssrc, message):
    packets, requested, status = parse_nack_feedback(message, ssrc)
    # The existing RR parser still receives its bounded ordinary messages,
    # including malformed prefixes it previously handled defensively.
    if isinstance(message, bytes) and len(message) <= MAX_RTCP_BYTES:
      super()._update_receiver_report(camera_type, ssrc, message)
    if status is not None:
      self._cluster_rtcp_stats[f"rtcp_{status}"] += 1
      return
    self._cluster_rtcp_stats["nack_packets"] += packets
    self._cluster_rtcp_stats["nack_requested"] += len(requested)
    config = self._cluster_rtp_configs.get(ssrc)
    try:
      next_sequence = config.sequence_number
    except (AttributeError, RuntimeError, TypeError, ValueError):
      next_sequence = None
    available = type(next_sequence) is int and 0 <= next_sequence <= 0xFFFF
    for sequence in requested:
      distance = ((next_sequence - 1 - sequence) & 0xFFFF) if available else None
      if distance is None or distance >= 0x8000:
        self._cluster_rtcp_stats["nack_distance_unavailable"] += 1
      else:
        self._cluster_rtcp_stats["nack_distance_ge100"] += distance >= 100
        self._nack_distance_max = max(self._nack_distance_max or 0, distance)

  def get_cluster_rtcp_stats(self):
    """Cumulative request counters plus max observed distance since this call.

    Polling happens after native handling, so distance is not the age when
    retransmission was attempted, an RTT, or proof of a receiver reset.
    """
    snapshot = {**self._cluster_rtcp_stats, "nack_distance_max": self._nack_distance_max}
    self._nack_distance_max = None
    return snapshot

  async def stop(self):
    await super().stop()
    self._native_video_handlers.clear()
    self._cluster_rtp_configs.clear()

  async def _send_track_loop(self, rtc_track, producer_track, rtp_config):
    clock = CaptureClock()
    waiting_for_idr = False
    sent = invalid = gated = send_not_ready = 0
    access_units = idr_frames = max_access_unit_bytes = max_idr_bytes = 0
    last_keyframe_stats = (0, 0, 0)
    send_not_ready_at = None
    last_request = None
    report_at = time.monotonic()
    flog("[CLUSTER_H264_SENDER] api=track_send | framing=validated_annexb4 | clock=camera_eof | rtcp=sr_pli_nack")
    try:
      while True:
        if not rtc_track.is_open():
          if rtc_track.is_closed():
            break
          await asyncio.sleep(0.01)
          continue
        packet = await producer_track.recv()
        # recv() can yield while a native connection is being closed.
        if not rtc_track.is_open() or producer_track.readyState != "live":
          break
        now = time.monotonic()
        try:
          data, keyframe = normalize_access_unit(bytes(packet))
        except ValueError as error:
          invalid += 1
          waiting_for_idr = True
          if last_request is None or now - last_request >= 1.0:
            flog(f"[CLUSTER_H264_SEND_WARN] Invalid access unit: {error}; requesting IDR | invalid={invalid}")
            producer_track.request_keyframe()
            last_request = now
          continue
        if waiting_for_idr and not keyframe:
          gated += 1
          if last_request is None or now - last_request >= 1.0:
            producer_track.request_keyframe()
            last_request = now
          continue
        waiting_for_idr = False
        access_units += 1
        max_access_unit_bytes = max(max_access_unit_bytes, len(data))
        if keyframe:
          idr_frames += 1
          max_idr_bytes = max(max_idr_bytes, len(data))
        pts = clock.pts(getattr(producer_track, "video_timestamp_eof", None), packet.pts)
        rtp_config.timestamp = (rtp_config.start_timestamp + pts) & 0xFFFFFFFF
        # Track.send() runs the same media chain (packetization/SR/PLI/NACK)
        # with the explicit timestamp, without the native FrameInfo overload.
        if rtc_track.send(data) is False:
          # The bool is the last RTP transport write's status, not the peer's
          # connection state. Even a sustained False streak can coexist with
          # live decoded video on the Pi. Recreating ICE from this bool turns
          # transport pressure into a multi-second display interruption. Real
          # failures still terminate through native closure/errors and the
          # receiver's decoded-video/control and C4 feedback timeouts.
          send_not_ready += 1
          if not rtc_track.is_open():
            break
          if send_not_ready_at is None:
            send_not_ready_at = now
            flog("[CLUSTER_H264_SEND_BACKPRESSURE] track_send=0 | action=keep_session")
          # Keep the live reference chain flowing, including P pictures. The
          # receiver requests an IDR if a packet was actually lost. A False
          # transport write alone must not create another burst of keyframes.
          await asyncio.sleep(0)
        else:
          if send_not_ready_at is not None:
            flog(f"[CLUSTER_H264_SEND_RECOVERED] pause_ms={(now - send_not_ready_at) * 1000:.1f} | "
                 + f"send_not_ready={send_not_ready}")
            send_not_ready_at = None
          sent += 1
        if now - report_at >= 10.0:
          keyframe_stats = tuple(getattr(producer_track, name, 0) for name in
                                 ("keyframe_requests", "keyframe_coalesced", "keyframe_deferred"))
          flog(f"[CLUSTER_H264_SEND_PERF] submitted_fps={sent / (now - report_at):.2f} | "
               + f"invalid={invalid} | idr_gated={gated} | send_not_ready={send_not_ready} | "
               + f"last_bytes={len(data)} | max_access_unit_bytes={max_access_unit_bytes} | "
               + f"access_units={access_units} | idr_frames={idr_frames} | max_idr_bytes={max_idr_bytes} | "
               + f"idr_requests={keyframe_stats[0] - last_keyframe_stats[0]} | "
               + f"idr_coalesced={keyframe_stats[1] - last_keyframe_stats[1]} | "
               + f"idr_deferred={keyframe_stats[2] - last_keyframe_stats[2]} | last_pts={pts} | clock=camera_eof")
          report_at, sent = now, 0
          access_units = idr_frames = max_access_unit_bytes = max_idr_bytes = 0
          last_keyframe_stats = keyframe_stats
    except asyncio.CancelledError:
      raise
    except Exception as error:
      flog(f"[CLUSTER_H264_SEND_ERROR] {type(error).__name__}: {error} | "
           + f"invalid={invalid} | idr_gated={gated} | send_not_ready={send_not_ready}")
      self.logger.exception("Error in cluster H.264 send loop")
    finally:
      self._set_event(self.connection_stopped_event)
