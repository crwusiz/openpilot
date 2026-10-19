"""WebRTC video receiver; SDL remains on the caller's thread."""

import asyncio
import contextlib
import logging
import math
from pathlib import Path
import socket
import threading
import time

try:
  from .h264_protocol import ANSWER, CONTROL_HEADER, ERROR, FEEDBACK, OFFER, STATE, decode_payload, pack_control, unpack_control_header
  from .receiver_resources import ReceiverResourceSampler, format_receiver_resources
except ImportError:
  from h264_protocol import ANSWER, CONTROL_HEADER, ERROR, FEEDBACK, OFFER, STATE, decode_payload, pack_control, unpack_control_header
  from receiver_resources import ReceiverResourceSampler, format_receiver_resources


LOG = logging.getLogger("cluster_receiver")
NEGOTIATION_TIMEOUT = 15.0
STREAM_TIMEOUT = 8.0
FEEDBACK_INTERVAL = 1.0
RECOVERY_INTERVAL = 0.5
STAGE_NAMES = {"scene": ("camera_copy", "camera_panel", "snapshot", "path", "path_cpu", "hud"),
               "hdmi": ("rgb_surface", "convert", "upload", "draw", "flip")}


class H264ConfigurationError(RuntimeError):
  retry_delay_s = 5.0


class _DecoderErrorHandler(logging.Handler):
  """Observe public aiortc decoder warnings for this receiver's lifetime."""

  def __init__(self, receiver):
    super().__init__(level=logging.WARNING)
    self.receiver = receiver

  def emit(self, record):
    if "failed to decode" in record.getMessage().lower():
      with self.receiver.condition:
        self.receiver.decoder_errors += 1
      # Decoder warnings originate on aiortc's decode worker. Wake the
      # signaling loop without waiting for the next one-second FPS report.
      loop, event = self.receiver.loop, self.receiver._recovery_event
      if loop is not None and event is not None:
        with contextlib.suppress(RuntimeError):
          loop.call_soon_threadsafe(event.set)


def validate_hello(hello):
  if hello.get("codec") != "h264" or hello.get("transport") != "webrtc":
    raise ValueError("Unsupported cluster video transport")
  if hello.get("camera") not in ("road", "wideRoad", "driver"):
    raise ValueError("Unsupported cluster video camera")
  for name, maximum in (("width", 4096), ("height", 2160), ("fps", 60)):
    value = hello.get(name)
    if type(value) is not int or not 1 <= value <= maximum:
      raise ValueError(f"Invalid H.264 {name}")
  if hello["width"] * hello["height"] > 4 * 1024 * 1024:
    raise ValueError("H.264 scene exceeds pixel budget")


class H264Receiver:
  """WebRTC decodes reference frames; only already decoded RGB may be replaced."""

  def __init__(self, sock, hello):
    validate_hello(hello)
    self.sock, self.hello = sock, hello
    self.condition = threading.Condition()
    self.latest_rgb = None
    self.latest_state = None
    self.state_revision = self.consumed_state_revision = 0
    self.received_frames = self.displayed_frames = self.dropped_frames = 0
    self.decoder_errors = 0
    self.last_video_at = self.last_state_at = None
    self.screen_off = False
    self.error = None
    self.done = self.closing = False
    self.loop = self.stop_event = None
    self._main_task = None
    self._async_error = None
    self._send_lock = None
    self._recovery_event = None
    self._recovered_decoder_errors = 0
    self._sequence = 0
    self._stats = self._empty_stats()
    self._last_display_at = None
    self._resources = ReceiverResourceSampler()
    self.thread = threading.Thread(target=self._run_thread, name="cluster-webrtc-receiver", daemon=True)

  def start(self):
    self.thread.start()

  @staticmethod
  def _empty_stats():
    keys = ["render_count", "render", "display", "queue_count", "queue", "convert_count", "convert", "gap"]
    for prefix, names in STAGE_NAMES.items():
      for name in names:
        keys.extend((f"{prefix}_{name}", f"{prefix}_{name}_count"))
    return dict.fromkeys(keys, 0.0)

  def _run_thread(self):
    decoder_log = logging.getLogger("aiortc.codecs.h264")
    decoder_handler = _DecoderErrorHandler(self)
    decoder_log.addHandler(decoder_handler)
    try:
      asyncio.run(self._run())
    except asyncio.CancelledError:
      pass
    except Exception as error:
      with self.condition:
        if not self.closing:
          self.error = error
    finally:
      decoder_log.removeHandler(decoder_handler)
      decoder_handler.close()
      with self.condition:
        if self.error is not None and not self.closing:
          now = time.monotonic()
          video_age = "none" if self.last_video_at is None else f"{max(0.0, now - self.last_video_at) * 1000:.1f}"
          state_age = "none" if self.last_state_at is None else f"{max(0.0, now - self.last_state_at) * 1000:.1f}"
          LOG.warning("[CLUSTER_H264_STOP] reason=%s | detail=%r | rx_frames=%d | decoder_errors=%d | "
                      + "video_age_ms=%s | state_age_ms=%s", type(self.error).__name__, str(self.error)[:300],
                      self.received_frames, self.decoder_errors, video_age, state_age)
        self.done = True
        self.condition.notify_all()

  def publish_rgb(self, rgb, received_at=None, convert_elapsed=0.0):
    received_at = time.monotonic() if received_at is None else received_at
    with self.condition:
      if self.closing:
        return
      self.dropped_frames += self.latest_rgb is not None
      self.latest_rgb = (rgb, received_at)
      self.received_frames += 1
      self.last_video_at = received_at
      self._stats["convert_count"] += 1
      self._stats["convert"] += convert_elapsed
      self.condition.notify_all()

  def record_display(self, render_started, rendered_at, displayed_at, video_received_at=None, presented=True,
                     scene_timings=None, display_timings=None):
    with self.condition:
      self._stats["render_count"] += 1
      self._stats["render"] += max(0.0, rendered_at - render_started)
      self._stats["display"] += max(0.0, displayed_at - rendered_at)
      for prefix, timings in (("scene", scene_timings), ("hdmi", display_timings)):
        for name in STAGE_NAMES[prefix]:
          value = timings.get(name) if isinstance(timings, dict) else None
          if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10:
            self._stats[f"{prefix}_{name}"] += value
            self._stats[f"{prefix}_{name}_count"] += 1
      if video_received_at is not None:
        self._stats["queue_count"] += 1
        self._stats["queue"] += max(0.0, render_started - video_received_at)
      if presented:
        self.displayed_frames += 1
        if self._last_display_at is not None:
          self._stats["gap"] = max(self._stats["gap"], displayed_at - self._last_display_at)
        self._last_display_at = displayed_at

  def publish_state(self, state, received_at=None):
    received_at = time.monotonic() if received_at is None else received_at
    with self.condition:
      if self.closing:
        return
      self.latest_state = (state, received_at)
      self.state_revision += 1
      self.last_state_at = received_at
      self.condition.notify_all()

  def take_updates(self, timeout=0.02):
    with self.condition:
      self.condition.wait_for(lambda: self.latest_rgb is not None or self.state_revision != self.consumed_state_revision
                              or self.error is not None or self.done, timeout=timeout)
      if self.error is not None:
        raise self.error
      rgb, self.latest_rgb = self.latest_rgb, None
      state = self.latest_state if self.state_revision != self.consumed_state_revision else None
      self.consumed_state_revision = self.state_revision
      return rgb, state

  async def _send_control(self, kind, payload):
    async with self._send_lock:
      self._sequence = (self._sequence + 1) & 0xFFFFFFFF
      packet = pack_control(kind, payload, self._sequence)
      await asyncio.wait_for(self.loop.sock_sendall(self.sock, packet), timeout=STREAM_TIMEOUT)

  async def _read_control(self, budget_s=STREAM_TIMEOUT):
    async def read_exact(size):
      data = bytearray()
      while len(data) < size:
        chunk = await self.loop.sock_recv(self.sock, size - len(data))
        if not chunk:
          raise ConnectionError("C4 closed H.264 control connection")
        data.extend(chunk)
      return bytes(data)

    async def read_packet():
      kind, _sequence, size = unpack_control_header(await read_exact(CONTROL_HEADER.size))
      return kind, decode_payload(await read_exact(size))

    try:
      return await asyncio.wait_for(read_packet(), timeout=budget_s)
    except TimeoutError as error:
      raise TimeoutError(f"H.264 control packet timed out after {budget_s:g} seconds") from error

  def _fail_async(self, error):
    if not self.closing and self._async_error is None:
      self._async_error = error
      self.stop_event.set()

  async def _consume_video(self, track):
    try:
      while not self.closing:
        frame = await track.recv()
        if (not 2 <= frame.width <= 4096 or not 2 <= frame.height <= 4096
            or frame.width * frame.height > 4 * 1024 * 1024):
          raise ValueError("Decoded H.264 frame exceeds pixel budget")
        # aiortc/PyAV has already decoded all dependent pictures. Replacing
        # this RGB slot never drops an H.264 reference packet before decoding.
        started = time.monotonic()
        # Keep UDP/RTCP and metadata reads responsive while libswscale builds
        # RGB. Await each conversion before receiving the next decoded frame:
        # there is only one conversion in flight, in reference-decoded order.
        rgb = await asyncio.to_thread(frame.to_ndarray, format="rgb24")
        self.publish_rgb(rgb, convert_elapsed=time.monotonic() - started)
    except asyncio.CancelledError:
      raise
    except Exception as error:
      self._fail_async(ConnectionError(f"WebRTC video stopped: {error}"))

  async def _read_states(self):
    while True:
      kind, payload = await self._read_control()
      if kind == STATE:
        self.publish_state(payload)
      elif kind == ERROR:
        raise RuntimeError(f"C4 H.264 error: {str(payload.get('error', payload))[:300]}")
      else:
        raise ValueError(f"Unexpected H.264 control message: {kind}")

  async def _recover_video(self):
    last_request_at = None
    while True:
      await self._recovery_event.wait()
      self._recovery_event.clear()
      if last_request_at is not None:
        delay = last_request_at + RECOVERY_INTERVAL - time.monotonic()
        if delay > 0:
          await asyncio.sleep(delay)
      with self.condition:
        errors = self.decoder_errors
      if errors <= self._recovered_decoder_errors:
        continue
      await self._send_control(FEEDBACK, {"screen_off": self.screen_off, "keyframe_request": True,
                                          "decoder_errors": errors, "recovery_reason": "decoder_error"})
      self._recovered_decoder_errors = errors
      last_request_at = time.monotonic()

  async def _feedback(self):
    previous_at = time.monotonic()
    previous_rx = previous_display = 0
    previous_decoder_errors = 0
    logged_at, logged_rx, logged_display, logged_dropped = previous_at, 0, 0, 0
    logged_decoder_errors = 0
    logged_stats = self._empty_stats()
    while True:
      await asyncio.sleep(FEEDBACK_INTERVAL)
      now = time.monotonic()
      with self.condition:
        rx, displayed = self.received_frames, self.displayed_frames
        decoder_errors = self.decoder_errors
        video_stale = self.last_video_at is None or now - self.last_video_at >= 0.5
        stats, self._stats = self._stats, self._empty_stats()
        payload = {
          "screen_off": self.screen_off,
          "video_fps": (rx - previous_rx) / max(now - previous_at, 0.001),
          "display_fps": (displayed - previous_display) / max(now - previous_at, 0.001),
          "decoder_backend": "aiortc_pyav_software",
          "decoder_errors": decoder_errors,
          "keyframe_request": (decoder_errors > max(previous_decoder_errors, self._recovered_decoder_errors)
                               or (self.last_video_at is not None and video_stale)),
          "dropped_display": self.dropped_frames,
          "render_avg_ms": stats["render"] * 1000 / max(stats["render_count"], 1),
          "display_avg_ms": stats["display"] * 1000 / max(stats["render_count"], 1),
          "queue_wait_avg_ms": stats["queue"] * 1000 / max(stats["queue_count"], 1),
          "rgb_convert_avg_ms": stats["convert"] * 1000 / max(stats["convert_count"], 1),
          "display_gap_max_ms": stats["gap"] * 1000,
          "video_age_ms": None if self.last_video_at is None else max(0, now - self.last_video_at) * 1000,
          "state_age_ms": None if self.last_state_at is None else max(0, now - self.last_state_at) * 1000,
          "video_stale": video_stale,
          "state_stale": self.last_state_at is None or now - self.last_state_at >= 0.5,
        }
      await self._send_control(FEEDBACK, payload)
      for key in stats:
        logged_stats[key] = max(logged_stats[key], stats[key]) if key == "gap" else logged_stats[key] + stats[key]
      if now - logged_at >= 10.0:
        elapsed = now - logged_at
        stages = "".join(f" | {prefix}_{name}_avg={logged_stats[f'{prefix}_{name}'] * 1000 / count:.1f}ms"
                         for prefix, names in STAGE_NAMES.items() for name in names
                         if (count := logged_stats[f"{prefix}_{name}_count"]) > 0)
        LOG.info("[CLUSTER_H264_RX_PERF] rx_fps=%.2f | display_fps=%.2f | dropped_display=%d | "
                 + "render_avg=%.1fms | display_avg=%.1fms | queue_wait_avg=%.1fms | rgb_convert_avg=%.1fms | "
                 + "display_gap_max=%.1fms | video_stale=%d | state_stale=%d | "
                 + "decoder_errors=%d | decoder=aiortc_pyav_software | screen_off=%d | mode=webrtc%s",
                 (rx - logged_rx) / elapsed, (displayed - logged_display) / elapsed,
                 payload["dropped_display"] - logged_dropped,
                 logged_stats["render"] * 1000 / max(logged_stats["render_count"], 1),
                 logged_stats["display"] * 1000 / max(logged_stats["render_count"], 1),
                 logged_stats["queue"] * 1000 / max(logged_stats["queue_count"], 1),
                 logged_stats["convert"] * 1000 / max(logged_stats["convert_count"], 1),
                 logged_stats["gap"] * 1000, payload["video_stale"], payload["state_stale"],
                 decoder_errors - logged_decoder_errors, payload["screen_off"], stages)
        LOG.info("[CLUSTER_PI_RESOURCE_PERF] %s", format_receiver_resources(self._resources.sample()))
        logged_at, logged_rx, logged_display, logged_dropped = now, rx, displayed, payload["dropped_display"]
        logged_decoder_errors = decoder_errors
        logged_stats = self._empty_stats()
      previous_at, previous_rx, previous_display = now, rx, displayed
      previous_decoder_errors = decoder_errors

  async def _watchdog(self, started):
    while True:
      await asyncio.sleep(0.1)
      now = time.monotonic()
      with self.condition:
        video_at, state_at = self.last_video_at, self.last_state_at
      if video_at is None:
        if now - started >= NEGOTIATION_TIMEOUT:
          raise TimeoutError("No decoded WebRTC video within 15 seconds")
      elif now - video_at >= STREAM_TIMEOUT:
        raise TimeoutError("WebRTC video stalled for eight seconds")
      if state_at is not None and now - state_at >= STREAM_TIMEOUT:
        raise TimeoutError("H.264 metadata stalled for eight seconds")

  async def _run(self):
    self.loop = asyncio.get_running_loop()
    self._main_task = asyncio.current_task()
    self.stop_event = asyncio.Event()
    self._send_lock = asyncio.Lock()
    self._recovery_event = asyncio.Event()
    if self.decoder_errors:
      self._recovery_event.set()
    if self.closing:
      return
    self.sock.setblocking(False)
    try:
      from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
    except ImportError as error:
      detail = "H.264 mode requires Orange Pi aiortc/PyAV dependencies; run the updated Pi installation/update script"
      await self._send_control(ERROR, {"error": detail})
      raise H264ConfigurationError(detail) from error

    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    tasks = []
    video_tasks = []

    @peer.on("track")
    def on_track(track):
      if track.kind == "video":
        video_tasks.append(asyncio.create_task(self._consume_video(track)))

    @peer.on("connectionstatechange")
    async def on_connection_state():
      LOG.info("[CLUSTER_H264_PEER] state=%s", peer.connectionState)
      if peer.connectionState in ("failed", "closed"):
        self._fail_async(ConnectionError(f"WebRTC connection {peer.connectionState}"))

    try:
      transceiver = peer.addTransceiver("video", direction="recvonly")
      h264 = [codec for codec in RTCRtpSender.getCapabilities("video").codecs if codec.mimeType.lower() == "video/h264"]
      if not h264:
        raise H264ConfigurationError("Pi WebRTC stack has no H.264 decoder")
      transceiver.setCodecPreferences(h264)

      async def negotiate():
        await peer.setLocalDescription(await peer.createOffer())
        await self._send_control(OFFER, {"type": "offer", "sdp": peer.localDescription.sdp})
        while True:
          kind, payload = await self._read_control(NEGOTIATION_TIMEOUT)
          if kind == ANSWER:
            if payload.get("type") != "answer" or not isinstance(payload.get("sdp"), str):
              raise ValueError("Invalid C4 WebRTC answer")
            await peer.setRemoteDescription(RTCSessionDescription(sdp=payload["sdp"], type="answer"))
            return
          if kind == STATE:
            self.publish_state(payload)
          elif kind == ERROR:
            raise RuntimeError(f"C4 H.264 error: {str(payload.get('error', payload))[:300]}")
          else:
            raise ValueError(f"Expected C4 WebRTC answer, got {kind}")

      await asyncio.wait_for(negotiate(), timeout=NEGOTIATION_TIMEOUT)
      LOG.info("[CLUSTER_H264_MODE] WebRTC H.264, decoder=aiortc_pyav_software, TCP=metadata only")
      started = time.monotonic()
      tasks = [asyncio.create_task(self._read_states()), asyncio.create_task(self._feedback()),
               asyncio.create_task(self._recover_video()),
               asyncio.create_task(self._watchdog(started)), asyncio.create_task(self.stop_event.wait())]
      done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
      for task in done:
        task.result()
      if self._async_error is not None and not self.closing:
        raise self._async_error
    finally:
      for task in tasks + video_tasks:
        task.cancel()
      await asyncio.gather(*(tasks + video_tasks), return_exceptions=True)
      try:
        await asyncio.wait_for(peer.close(), timeout=3.0)
      except TimeoutError:
        LOG.warning("WebRTC peer did not close within three seconds")

  def close(self):
    with self.condition:
      self.closing = True
      self.latest_rgb = None
      self.condition.notify_all()
    loop, main_task = self.loop, self._main_task
    if loop is not None and main_task is not None:
      try:
        loop.call_soon_threadsafe(main_task.cancel)
      except RuntimeError:
        pass
    try:
      self.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass
    if self.thread.ident is None:
      return
    self.thread.join(timeout=4.0)
    if self.thread.is_alive():
      LOG.warning("WebRTC receiver thread did not stop within four seconds")
    else:
      # A saved exception may keep the receiver reachable until cyclic GC.
      # A completed asyncio task/loop no longer needs to retain its media
      # callbacks, conversion executor or a second copy of that exception.
      self.loop = self._main_task = self.stop_event = None
      self._send_lock = self._recovery_event = self._async_error = None
      with self.condition:
        self.latest_state = None


def receive_h264_session(sock, display, hello, frame_timeout=2.0, max_frames=None):
  validate_hello(hello)
  try:
    if __package__:
      from .h264_scene import H264Scene
    else:
      from h264_scene import H264Scene
  except ImportError as error:
    detail = "H.264 scene requires Orange Pi Pillow, NumPy and OpenCV dependencies; run pi_update.sh"
    with contextlib.suppress(OSError):
      sock.settimeout(0.25)
      sock.sendall(pack_control(ERROR, {"error": "h264_scene_dependencies_unavailable", "detail": detail}))
    raise H264ConfigurationError(detail) from error
  try:
    scene = H264Scene(Path(__file__).resolve().parent / "assets", hello["width"], hello["height"], hello["fps"])
  except RuntimeError as error:
    with contextlib.suppress(OSError):
      sock.settimeout(0.25)
      sock.sendall(pack_control(ERROR, {"error": "h264_scene_unavailable", "detail": str(error)[:1000]}))
    raise H264ConfigurationError(str(error)) from error
  receiver = H264Receiver(sock, hello)
  waiting_shown = False
  next_render_at = None
  render_interval = 1.0 / hello["fps"]
  pending_render = False
  pending_video_at = None
  freshness = None
  try:
    receiver.start()
    while True:
      if not display.pump_events():
        raise KeyboardInterrupt
      with receiver.condition:
        receiver.screen_off = bool(getattr(display, "screen_off", False))
      timeout = 0.02
      if pending_render and next_render_at is not None and not receiver.screen_off:
        timeout = min(timeout, max(0.0, next_render_at - time.monotonic()))
      rgb, state = receiver.take_updates(timeout=timeout)
      if state is not None:
        scene.update(state[0], received_at=state[1])
        pending_render = True
      if rgb is not None:
        scene.set_video(rgb[0], received_at=rgb[1], take_ownership=True)
        pending_video_at = rgb[1]
        pending_render = True
      now = time.monotonic()
      current_freshness = (receiver.last_video_at is not None and now - receiver.last_video_at < 0.5,
                           receiver.last_state_at is not None and now - receiver.last_state_at < 0.5)
      if current_freshness != freshness:
        # Clear expired HUD/path even if both incoming streams stop together.
        pending_render = True
        freshness = current_freshness
      if receiver.last_video_at is not None and now - receiver.last_video_at >= frame_timeout:
        if not waiting_shown:
          if not display.show_waiting():
            raise RuntimeError("Unable to display WebRTC waiting screen")
          LOG.warning("[CLUSTER_H264_STALE] No decoded video within %.1fs; waiting for live video", frame_timeout)
          waiting_shown = True
        pending_render = False
      elif pending_render and (next_render_at is None or now >= next_render_at) and not receiver.screen_off:
        render_started = time.monotonic()
        image = scene.render()
        rendered_at = time.monotonic()
        if not display.send_rgb(image):
          raise RuntimeError("Unable to display H.264 cluster frame")
        displayed_at = time.monotonic()
        receiver.record_display(render_started, rendered_at, displayed_at, pending_video_at,
                                bool(getattr(display, "last_frame_presented", True)),
                                scene_timings=getattr(scene, "last_frame_timings", None),
                                display_timings=getattr(display, "last_frame_timings", None))
        if waiting_shown:
          LOG.info("[CLUSTER_H264_RESUMED] Live WebRTC video received")
        waiting_shown = False
        pending_render = False
        pending_video_at = None
        # Preserve the phase while work fits the frame budget. If a frame
        # overruns it, the newest pending decoded picture is immediately due:
        # rounding to the following slot throws away another whole 50 ms.
        # There is still one render at a time and no encoded frame dropping.
        if next_render_at is None:
          next_render_at = render_started
        next_render_at += render_interval
        next_render_at = max(next_render_at, displayed_at)
      if max_frames is not None and receiver.received_frames >= max_frames:
        return receiver.received_frames
      if receiver.done:
        # The worker can fail after take_updates() has checked its error.
        # Recheck after observing completion so that a reconnect never hides
        # the final control/video error as a successful session return.
        receiver.take_updates(timeout=0)
        return receiver.received_frames
  finally:
    try:
      receiver.close()
    finally:
      scene.close()
