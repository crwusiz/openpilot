"""Cluster-only guards around the native WebRTC video sender.

Keep the existing ICE/SRTP, RTCP reports, PLI and NACK handlers. Validate and
normalize elementary H.264 before entering their native packetizer, and set
the RTP timestamp explicitly rather than crossing the FrameInfo binding.
"""

import asyncio
import re
import time

from teleoprtc.stream import RTCSessionDescription, WebRTCAnswerStream

from openpilot.selfdrive.addon.cluster.cluster_logging import flog


START_CODE = re.compile(b"\x00\x00\x00\x01|\x00\x00\x01")
MAX_ACCESS_UNIT_BYTES = 4 * 1024 * 1024
MAX_NAL_UNITS = 256


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

  def _add_producer_tracks(self, remote_sdp=None):
    super()._add_producer_tracks(remote_sdp)
    # Retain the native chain's Python wrappers until this stream is closed,
    # including PLI callbacks. Shutdown still joins senders before closing ICE.
    for rtc_track, _producer, _config in self._track_state:
      handler = rtc_track.get_media_handler()
      for _index in range(8):
        if handler is None:
          break
        self._native_video_handlers.append(handler)
        handler = handler.next()
      else:
        raise RuntimeError("Unexpected native H.264 media handler chain")

  async def stop(self):
    await super().stop()
    self._native_video_handlers.clear()

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
