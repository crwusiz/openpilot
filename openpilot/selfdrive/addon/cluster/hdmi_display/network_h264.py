"""H.264/WebRTC camera plus bounded TCP scene state.

The existing hardware livestream is reused without C4 camera RGB conversion
or cluster composition. The Pi renders the HUD locally.
"""
import asyncio
import contextlib
import json
import signal
import socket
import threading
import time

from openpilot.selfdrive.addon.cluster.cluster_health_guard import ClusterHealthGuard
from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.cluster_runtime_metrics import ClusterRuntimeMetrics
from openpilot.selfdrive.addon.cluster.hdmi_display.network_status import write_network_status
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_protocol import (
  ANSWER, CONTROL_HEADER, ERROR, FEEDBACK, HELLO, OFFER, PATH_KEYS,
  decode_payload, pack_control, pack_scene, unpack_control_header,
)

STATE_DRAIN_WARN_S = 0.25
STATE_DRAIN_TIMEOUT_S = 5.0
STATE_SEND_BUFFER_BYTES = 16 * 1024
KEYFRAME_REQUEST_INTERVAL = 1.0


def configure_state_transport(writer):
  # Keep at most one partially written state in asyncio. Large default TCP
  # buffers otherwise turn a short Wi-Fi pause into a queue of old HUD data.
  writer.transport.set_write_buffer_limits(high=0, low=0)
  sock = writer.get_extra_info("socket")
  if sock is not None:
    try:
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, STATE_SEND_BUFFER_BYTES)
    except OSError as error:
      flog(f"[CLUSTER_H264_STATE_WARN] Cannot bound TCP send buffer: {error}")


async def drain_state(writer):
  """Wait on one write without queuing obsolete states or resetting video."""
  started = time.monotonic()
  pending = asyncio.create_task(writer.drain())
  try:
    done, _ = await asyncio.wait({pending}, timeout=STATE_DRAIN_WARN_S)
    if not done:
      flog(f"[CLUSTER_H264_STATE_BACKPRESSURE] action=keep_video | queued_bytes={writer.transport.get_write_buffer_size()}")
      try:
        await asyncio.wait_for(pending, max(0.001, STATE_DRAIN_TIMEOUT_S - (time.monotonic() - started)))
      except TimeoutError as error:
        raise TimeoutError(f"Scene TCP write stalled for {STATE_DRAIN_TIMEOUT_S:g} seconds") from error
      flog(f"[CLUSTER_H264_STATE_RECOVERED] wait_ms={(time.monotonic() - started) * 1000:.1f} | next=latest_state")
    else:
      pending.result()  # Propagate an actual socket failure immediately.
    return time.monotonic() - started
  finally:
    if not pending.done():
      pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)


def publish_status(address):
  try:
    write_network_status(address)
  except OSError as error:
    flog(f"[CLUSTER_NETWORK_WARN] Cannot publish H.264 connection status: {error}")


def camera_clock_ns():
  # Native camera EOF timestamps use CLOCK_BOOTTIME, which includes suspend.
  if hasattr(time, "CLOCK_BOOTTIME"):
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  return time.monotonic_ns()


def _plain(value, precision=3):
  # Metres at millimetre precision and HUD values at 0.001 are below visible
  # resolution. Avoid transmitting full float64 decimal strings at 20 Hz.
  if isinstance(value, dict):
    return {key: _plain(item, precision) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    # Model coordinates are homogeneous Python float lists. Round these
    # without repeating all recursive type checks for every point.
    if value and all(type(item) is float for item in value):
      return [round(item, precision) for item in value]
    return [_plain(item, precision) for item in value]
  if hasattr(value, "tolist"):
    return _plain(value.tolist(), precision)
  if isinstance(value, float):
    return round(value, precision)
  return value


def scene_snapshot(config, models, health, degraded=False):
  """Build plain scene data; pack_scene validates it once before transmission."""
  valid, hud, path = models.get_render_data()
  source_size = models.get_camera_source_size()
  scene = {
    "model_valid": bool(valid and not degraded and source_size is not None),
    "hud": _plain(hud), "path": {key: _plain(path[key], 6 if key == "calib_transform" else 3) for key in PATH_KEYS if key in path},
    "source_size": source_size, "is_metric": bool(config.is_metric),
    "rotate_180": bool(config.rotate_180), "camera_contrast": float(config.camera_contrast),
    "model_age_ms": health.get("model_age_ms"), "car_state_age_ms": health.get("car_state_age_ms"),
    "state_created_mono_ns": time.monotonic_ns(),
  }
  return scene


async def read_control(reader, budget_s=8.0):
  async def read():
    kind, sequence, size = unpack_control_header(await reader.readexactly(CONTROL_HEADER.size))
    return kind, sequence, decode_payload(await reader.readexactly(size))
  return await asyncio.wait_for(read(), budget_s)


async def write_control(writer, kind, payload, sequence=0, budget_s=0.25):
  writer.write(pack_control(kind, payload, sequence))
  await asyncio.wait_for(writer.drain(), budget_s)


def create_camera_stream(offer_sdp):
  # Load the native WebRTC binding only when constructing a stream.
  from openpilot.selfdrive.addon.cluster.hdmi_display.network_h264_stream import ClusterH264Stream
  from openpilot.cereal import messaging
  from openpilot.system.webrtc.device.video import LiveStreamVideoStreamTrack

  class CameraTrack(LiveStreamVideoStreamTrack):
    def __init__(self):
      self.video_frames = self.video_bytes = self.resyncs = 0
      self.video_frame_id = self.video_timestamp_eof = None
      self.last_video_at = None
      self._encode_id = None
      self._ready = False
      self._header = b""
      self._keyframe_at = None
      self._keyframe_requested = False
      self._keyframe_deferred = False
      self.keyframe_requests = self.keyframe_coalesced = self.keyframe_deferred = 0
      self._loop = asyncio.get_running_loop()
      self._loop_thread = threading.get_ident()
      self._request_lock = threading.Lock()
      self._request_scheduled = False
      self._stopped = False
      super().__init__("road")

    def request_keyframe(self):
      # Native PLI callbacks run outside asyncio. Params.put(block=False)
      # releases the GIL and updates a shared C++ future, so it must not race
      # the loop's feedback, IDR acknowledgement or shutdown writes.
      if threading.get_ident() == self._loop_thread:
        self._request_keyframe_on_loop()
        return
      with self._request_lock:
        if self._stopped or self._request_scheduled:
          return
        self._request_scheduled = True
      try:
        self._loop.call_soon_threadsafe(self._request_keyframe_on_loop)
      except RuntimeError:  # A late native callback after the loop closed.
        with self._request_lock:
          self._request_scheduled = False

    def _request_keyframe_on_loop(self):
      with self._request_lock:
        self._request_scheduled = False
        if self._stopped:
          return
      now = time.monotonic()
      if self._keyframe_at is not None and now - self._keyframe_at < KEYFRAME_REQUEST_INTERVAL:
        self.keyframe_coalesced += 1
        # An IDR already pending fulfills overlapping requests. A new request
        # after its acknowledgement must survive the cooldown and be retried
        # on a later source frame, without holding back healthy P pictures.
        if not self._keyframe_requested and not self._keyframe_deferred:
          self._keyframe_deferred = True
          self.keyframe_deferred += 1
        return
      self._keyframe_deferred = False
      self._keyframe_requested = True
      self._keyframe_at = now
      self.keyframe_requests += 1
      super().request_keyframe()

    def request_recovery(self):
      # A decoder/network error at the receiver does not invalidate the
      # encoder's local reference chain. Keep sending until the fresh IDR
      # arrives; only an actual source gap/stale frame gates _ready below.
      self.resyncs += 1
      self.request_keyframe()

    def stop(self):
      with self._request_lock:
        self._stopped = True
      self._keyframe_deferred = False
      super().stop()
      if self._keyframe_requested:
        # The encoder reads this shared flag on every frame. Disconnecting
        # before an IDR arrives must not leave all livestreams forcing IDRs.
        self.params.put("LivestreamRequestKeyframe", False, block=False)
        self._keyframe_requested = False

    def _make_sock(self, camera_type):
      # Encoded P frames form a reference chain; conflate=True would silently
      # discard references when the event loop is delayed.
      return messaging.sub_sock(self.camera_to_sock_mapping[camera_type], conflate=False)

    def _build_frame_data(self, msg):
      data = getattr(msg, msg.which())
      self.video_frame_id = int(data.idx.frameId)
      self.video_timestamp_eof = int(data.idx.timestampEof)
      self._current_encode_id = int(data.idx.encodeId)
      self._current_keyframe = bool(data.idx.flags & 0x8)
      if data.header:
        self._header = bytes(data.header)
      return (self._header if self._current_keyframe else b"") + bytes(data.data)

    async def recv(self):
      while True:
        base_ack_pending = not self._seen_keyframe
        packet = await super().recv()
        now = time.monotonic()
        gap = self._encode_id is not None and self._current_encode_id != self._encode_id + 1
        self._encode_id = self._current_encode_id
        stale = self.video_timestamp_eof and camera_clock_ns() - self.video_timestamp_eof > 250_000_000
        if gap or stale:
          self._ready = False
          self.resyncs += 1
        if not self._ready:
          if not self._current_keyframe or not self._header or stale:
            if self._keyframe_at is None or now - self._keyframe_at >= KEYFRAME_REQUEST_INTERVAL:
              self.request_keyframe()
            await asyncio.sleep(0)
            continue
          self._ready = True
        if self._current_keyframe and self._keyframe_requested:
          # Fulfill both local resync and RTCP PLI requests. The initial-IDR
          # acknowledgement is already written by the base recv(). Later
          # requests are owned here; never enqueue two writes for one IDR.
          if not base_ack_pending:
            self.params.put("LivestreamRequestKeyframe", False, block=False)
          self._keyframe_requested = False
        if self._keyframe_deferred and now - self._keyframe_at >= KEYFRAME_REQUEST_INTERVAL:
          # Run after the current frame's acknowledgement: an IDR captured
          # before this retry must not acknowledge the new encoder request.
          self._request_keyframe_on_loop()
        self.video_frames += 1
        self.video_bytes += len(bytes(packet))
        self.last_video_at = now
        return packet

  track = CameraTrack()
  try:
    stream = ClusterH264Stream(offer_sdp, track)
  except BaseException:
    track.stop()
    raise
  return stream, track


class H264NetworkServer:
  def __init__(self, config, models, stream_factory=create_camera_stream):
    self.config, self.models, self.stream_factory = config, models, stream_factory
    self.server = None
    self.clients = set()
    self.active = None
    self.health = {}
    self.degraded = False
    self.screen_off = False
    self.peer_ip = None
    self.status_updated = 0.0

  async def start(self):
    self.server = await asyncio.start_server(self._client, self.config.network_bind_host, self.config.network_port,
                                             limit=128 * 1024)
    flog(f"[CLUSTER_NETWORK_LISTEN] codec=h264 | transport=webrtc | signaling={self.config.network_bind_host}:{self.config.network_port}")

  async def close(self):
    server, self.server = self.server, None
    if server is not None:
      server.close()
    tasks = list(self.clients)
    for task in tasks:
      task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    # Python 3.12 Server.wait_closed also waits for client transports. Cancel
    # negotiations first; otherwise shutdown would wait their full 15s budget.
    if server is not None:
      await server.wait_closed()
    publish_status(None)

  async def _feedback(self, reader, track):
    last_keyframe_request = None
    while True:
      kind, _sequence, payload = await read_control(reader)
      if kind != FEEDBACK:
        raise ValueError("Expected H.264 receiver feedback")
      self.screen_off = payload.get("screen_off") is True
      if payload.get("keyframe_request") is True:
        now = time.monotonic()
        if last_keyframe_request is None or now - last_keyframe_request >= 0.5:
          resync = getattr(track, "request_recovery", None)
          if callable(resync):
            resync()
            last_keyframe_request = now
            flog("[CLUSTER_H264_RECOVERY] Pi requested a fresh IDR; preserving the live source reference chain.")
      # Only bounded scalar telemetry is accepted; no vehicle messages/params
      # are published from a receiver's input.
      fields = []
      for key in ("video_fps", "decoded_fps", "skipped_rgb", "display_fps", "camera_display_fps", "metadata_display_fps",
                  "hidden_camera_fps", "gpu_camera", "rgb_convert_avg_ms",
                  "render_avg_ms", "display_avg_ms", "queue_wait_avg_ms", "video_age_ms", "state_age_ms", "display_gap_max_ms",
                  "dropped_display", "decoder_errors"):
        value = payload.get(key)
        if type(value) in (float, int) and 0 <= value <= 1e9:
          fields.append(f"{key}={value:.2f}")
      flog("[CLUSTER_H264_PI_PERF] codec=h264 | transport=webrtc | " + " | ".join(fields)
           + f" | screen_off={int(self.screen_off)}")

  async def _states(self, writer, track, stream):
    sequence, total_bytes = 0, 0
    started = time.monotonic()
    last_frames = last_video_bytes = 0
    state_frames = 0
    state_cpu = state_wall = 0.0
    while True:
      before, cpu_before = time.monotonic(), time.thread_time()
      scene = scene_snapshot(self.config, self.models, self.health, self.degraded)
      scene.update(video_frame_id=track.video_frame_id, video_timestamp_eof=track.video_timestamp_eof, state_seq=sequence)
      packet = pack_scene(scene, sequence)
      state_cpu += time.thread_time() - cpu_before
      writer.write(packet)
      await drain_state(writer)
      sequence = (sequence + 1) & 0xFFFFFFFF
      state_frames += 1
      total_bytes += len(packet)
      state_wall += time.monotonic() - before
      now = time.monotonic()
      if self.peer_ip is not None and now - self.status_updated >= 1.0:
        publish_status(self.peer_ip)
        self.status_updated = now
      if now - started >= 10:
        elapsed = now - started
        video_age = "n/a" if not track.video_timestamp_eof else f"{max(0, camera_clock_ns() - track.video_timestamp_eof) / 1e6:.1f}"
        report_details = ""
        get_reports = getattr(stream, "get_receiver_report_stats", None)
        reports = get_reports() if callable(get_reports) else {}
        report = reports.get("road")
        if report is not None:
          report_details = (f" | rtcp_loss_pct={report.fraction_lost * 100 / 256:.2f}"
                            + f" | rtcp_packets_lost={report.packets_lost} | rtcp_jitter_ms={report.jitter / 90:.2f}")
        params = getattr(track, "params", None)
        bitrate = params.get("LivestreamEncoderBitrate") if params is not None else None
        bitrate = str(bitrate) if bitrate else "encoder_default"
        if bitrate != "encoder_default" and (not bitrate.isdigit() or len(bitrate) > 9):
          bitrate = "unknown"
        flog(f"[CLUSTER_H264_PERF] codec=h264 | transport=webrtc | video_fps={(track.video_frames - last_frames) / elapsed:.2f}"
             + f" | video_payload_kbps={(track.video_bytes - last_video_bytes) * 8 / (1000 * elapsed):.1f}"
             + f" | state_fps={state_frames / elapsed:.2f} | state_kbps={total_bytes * 8 / (1000 * elapsed):.1f}"
             + f" | state_avg={state_wall * 1000 / state_frames:.1f}ms | state_cpu_avg={state_cpu * 1000 / state_frames:.1f}ms"
             + f" | video_source_age_ms={video_age} | resyncs={track.resyncs} | guard={int(self.degraded)}"
             + f" | encoder_bitrate_setting={bitrate}" + report_details
             + " | encoder=existing_livestream | camera_rgb=0 | cluster_render=0")
        started, last_frames, last_video_bytes = now, track.video_frames, track.video_bytes
        total_bytes = state_frames = 0
        state_cpu = state_wall = 0.0
      hz = 5 if self.degraded or self.screen_off else min(self.config.fps, 20)
      await asyncio.sleep(max(0.0, 1.0 / max(hz, 1) - (time.monotonic() - before)))

  async def _client(self, reader, writer):
    task = asyncio.current_task()
    self.clients.add(task)
    stream = track = None
    workers = []
    owns = self.active is None
    phase = "hello"
    try:
      if not owns:
        await write_control(writer, ERROR, {"error": "h264_cluster_busy"})
        return
      self.active = task
      self.screen_off = False
      configure_state_transport(writer)
      peer = writer.get_extra_info("peername")
      self.peer_ip, self.status_updated = peer[0], time.monotonic()
      publish_status(peer[0])
      flog(f"[CLUSTER_NETWORK_SUCCESS] Orange Pi connected from {peer[0]}:{peer[1]}.")
      await write_control(writer, HELLO, {"protocol": 1, "codec": "h264", "transport": "webrtc", "camera": "road",
                                        "width": self.config.width, "height": self.config.height, "fps": min(self.config.fps, 20),
                                        "state_hz": 20}, budget_s=2)
      phase = "offer"
      kind, _sequence, offer = await read_control(reader, budget_s=15)
      if kind == ERROR:
        raise RuntimeError(f"Pi H.264 unavailable: {offer.get('error', 'unknown')} | {str(offer.get('detail', ''))[:512]}")
      if kind != OFFER or offer.get("type") != "offer" or not isinstance(offer.get("sdp"), str) or not offer["sdp"].startswith("v=0"):
        raise ValueError("Expected a WebRTC H.264 offer")
      phase = "start_stream"
      stream, track = self.stream_factory(offer["sdp"])
      answer = await asyncio.wait_for(stream.start(), 15)
      phase = "answer"
      await write_control(writer, ANSWER, {"sdp": answer.sdp, "type": answer.type}, budget_s=2)
      phase = "connect"
      await asyncio.wait_for(stream.wait_for_connection(), 15)
      flog("[CLUSTER_H264_CONNECTED] codec=h264 | transport=webrtc | camera=road | compose=pi | ack=telemetry_only")
      workers = [asyncio.create_task(self._states(writer, track, stream), name="scene_state"),
                 asyncio.create_task(self._feedback(reader, track), name="receiver_feedback"),
                 asyncio.create_task(stream.wait_for_disconnection(), name="video_transport")]
      done, _pending = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
      for completed in done:
        phase = completed.get_name()
        completed.result()
        flog(f"[CLUSTER_H264_DISCONNECTED] codec=h264 | phase={phase}")
    except asyncio.CancelledError:
      raise
    except Exception as e:
      flog(f"[CLUSTER_H264_ERROR] codec=h264 | phase={phase} | {type(e).__name__}: {e}")
      with contextlib.suppress(Exception):
        await write_control(writer, ERROR, {"error": type(e).__name__, "detail": str(e)[:512]}, budget_s=0.25)
    finally:
      for worker in workers:
        worker.cancel()
      if workers:
        await asyncio.gather(*workers, return_exceptions=True)
      try:
        try:
          # Stop/join the native send loop and PLI callbacks before releasing
          # their producer socket or clearing its pending keyframe request.
          if stream is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
              await asyncio.wait_for(stream.stop(), 3)
        finally:
          if track is not None:
            track.stop()
      finally:
        writer.close()
        try:
          try:
            await asyncio.wait_for(writer.wait_closed(), 1)
          except (Exception, asyncio.CancelledError):
            # close() can keep a congested transport alive while it flushes.
            # Do not strand it (or Server.wait_closed) after our close budget.
            writer.transport.abort()
        finally:
          if owns:
            self.active = None
            self.peer_ip = None
            self.screen_off = False
            publish_status(None)
          self.clients.discard(task)


async def serve_h264(config, models):
  from openpilot.selfdrive.addon.cluster.cluster_policy import enforce_cluster_transport
  server = H264NetworkServer(config, models)
  guard, metrics = ClusterHealthGuard(), ClusterRuntimeMetrics()
  await server.start()
  flog(f"[CLUSTER_MODE] codec=h264 | transport=webrtc | compose=pi | target_camera_fps=20 | metadata_tcp={config.network_port}")
  try:
    while True:
      if not enforce_cluster_transport(config.params, config.display_transport):
        break
      if config.params.get("ClusterDisplayTransport") != "network":
        flog("[CLUSTER_H264_RESTART] Transport changed; restarting cluster.")
        break
      health = models.get_health_data()
      server.health = health
      changed = guard.update(health, time.monotonic())
      server.degraded = guard.degraded
      if changed:
        flog(changed)
      for event in health.get("diagnostic_events", ()):
        flog("[CLUSTER_INPUT_DIAG] " + json.dumps(event, ensure_ascii=True, separators=(",", ":")))
      resource = metrics.sample(health)
      if resource is not None:
        flog(resource)
      await asyncio.sleep(0.05)
  finally:
    await server.close()


def run_h264_cluster(config):
  from openpilot.selfdrive.addon.cluster.cluster_models import ClusterModels
  def stop(signum, _frame):
    flog(f"[CLUSTER_MAIN] H.264 stop signal received: {signum}")
    raise KeyboardInterrupt
  signal.signal(signal.SIGTERM, stop)
  models = ClusterModels(defer_render_updates=True)
  try:
    asyncio.run(serve_h264(config, models))
  except KeyboardInterrupt:
    flog("[CLUSTER_MAIN] H.264 interrupted.")
  finally:
    models.close()
