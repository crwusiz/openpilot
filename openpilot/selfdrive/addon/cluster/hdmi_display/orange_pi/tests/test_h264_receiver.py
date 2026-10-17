import asyncio
import builtins
import logging
import socket
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import cluster_receiver, h264_receiver
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.control_socket import recv_exact
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_protocol import (
  ANSWER, CONTROL_HEADER, ERROR, FEEDBACK, HELLO, OFFER, STATE, decode_payload, pack_control, unpack_control_header,
)


HELLO_DATA = {"codec": "h264", "transport": "webrtc", "camera": "road", "width": 1920, "height": 480, "fps": 20}


def read_control(sock):
  kind, sequence, size = unpack_control_header(recv_exact(sock, CONTROL_HEADER.size))
  return kind, sequence, decode_payload(recv_exact(sock, size))


def wait_until(predicate):
  deadline = time.monotonic() + 2.0
  while not predicate() and time.monotonic() < deadline:
    time.sleep(0.005)
  assert predicate()


@pytest.fixture
def fake_aiortc(monkeypatch):
  peers = []
  module = ModuleType("aiortc")
  module.RTCConfiguration = lambda **kwargs: SimpleNamespace(**kwargs)
  module.RTCSessionDescription = lambda **kwargs: SimpleNamespace(**kwargs)
  module.RTCRtpSender = SimpleNamespace(getCapabilities=lambda _kind: SimpleNamespace(codecs=[
    SimpleNamespace(mimeType="video/VP8"), SimpleNamespace(mimeType="video/H264"),
  ]))

  class Track:
    kind = "video"

    def __init__(self):
      self.frames = 0

    async def recv(self):
      if self.frames:
        await asyncio.Event().wait()
      self.frames += 1
      return SimpleNamespace(width=3, height=2, to_ndarray=lambda **_kwargs: np.full((2, 3, 3), 90, dtype=np.uint8))

  class Peer:
    def __init__(self, configuration):
      assert configuration.iceServers == []
      self.handlers = {}
      self.closed = False
      self.connectionState = "new"
      peers.append(self)

    def on(self, name):
      def install(callback):
        self.handlers[name] = callback
        return callback
      return install

    def addTransceiver(self, kind, direction):
      assert (kind, direction) == ("video", "recvonly")
      return SimpleNamespace(setCodecPreferences=lambda codecs: setattr(self, "codecs", codecs))

    async def createOffer(self):
      return SimpleNamespace(sdp="v=0\r\na=recvonly\r\n", type="offer")

    async def setLocalDescription(self, description):
      self.localDescription = description

    async def setRemoteDescription(self, description):
      self.remoteDescription = description
      self.handlers["track"](Track())

    async def close(self):
      self.closed = True

  module.RTCPeerConnection = Peer
  monkeypatch.setitem(sys.modules, "aiortc", module)
  return peers


def test_webrtc_signaling_metadata_and_feedback_share_control_socket_without_frame_ack(monkeypatch, fake_aiortc):
  monkeypatch.setattr(h264_receiver, "FEEDBACK_INTERVAL", 0.02)
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    receiver.start()
    kind, _sequence, offer = read_control(c4_sock)
    assert kind == OFFER and offer == {"type": "offer", "sdp": "v=0\r\na=recvonly\r\n"}
    # State can precede the answer while the encoder is warming up.
    c4_sock.sendall(pack_control(STATE, {"state_seq": 7}))
    c4_sock.sendall(pack_control(ANSWER, {"type": "answer", "sdp": "v=0\r\n"}))
    wait_until(lambda: receiver.received_frames == 1 and receiver.state_revision == 1)
    rgb, state = receiver.take_updates()
    assert np.array_equal(rgb[0], np.full((2, 3, 3), 90, dtype=np.uint8))
    assert state[0] == {"state_seq": 7} and state[1] <= time.monotonic()
    assert receiver.take_updates(timeout=0) == (None, None)
    now = time.monotonic()
    receiver.record_display(now, now + 0.02, now + 0.03, video_received_at=now - 0.04)
    feedbacks = []
    for _ in range(10):
      kind, _sequence, feedback = read_control(c4_sock)
      assert kind == FEEDBACK
      feedbacks.append(feedback)
      if feedback["display_fps"] > 0:
        break
    assert any(item["video_fps"] > 0 for item in feedbacks) and feedback["display_fps"] > 0
    assert feedback["render_avg_ms"] == pytest.approx(20.0)
    assert feedback["display_avg_ms"] == pytest.approx(10.0)
    assert feedback["queue_wait_avg_ms"] == pytest.approx(40.0)
    assert feedback["rgb_convert_avg_ms"] >= 0
    assert not feedback["video_stale"] and not feedback["state_stale"]
    assert feedback["decoder_backend"] == "aiortc_pyav_software"
    assert feedback["screen_off"] is False
    assert [codec.mimeType for codec in fake_aiortc[0].codecs] == ["video/H264"]
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()
  assert fake_aiortc[0].closed and not receiver.thread.is_alive()


def test_close_cancels_pending_answer_and_closes_peer_without_waiting_for_negotiation_timeout(fake_aiortc):
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    receiver.start()
    assert read_control(c4_sock)[0] == OFFER
    started = time.monotonic()
    receiver.close()
    assert time.monotonic() - started < 1.0
    assert receiver.error is None and receiver.done and not receiver.thread.is_alive()
    assert fake_aiortc[0].closed
  finally:
    c4_sock.close()
    pi_sock.close()


def test_missing_optional_webrtc_dependency_reports_error_to_c4(monkeypatch):
  monkeypatch.setitem(sys.modules, "aiortc", None)
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    receiver.start()
    kind, _sequence, payload = read_control(c4_sock)
    assert kind == ERROR and "aiortc/PyAV dependencies" in payload["error"]
    wait_until(lambda: receiver.done)
    with pytest.raises(RuntimeError, match="aiortc/PyAV dependencies"):
      receiver.take_updates()
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()


def test_missing_scene_dependency_reports_error_and_uses_normal_reconnect_exception(monkeypatch):
  original_import = builtins.__import__

  def without_scene(name, *args, **kwargs):
    if name.split(".")[-1] == "h264_scene":
      raise ImportError("cv2 unavailable")
    return original_import(name, *args, **kwargs)

  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(1.0)
  try:
    monkeypatch.setattr(builtins, "__import__", without_scene)
    with pytest.raises(RuntimeError, match="run pi_update.sh"):
      h264_receiver.receive_h264_session(pi_sock, None, HELLO_DATA)
    kind, _, payload = read_control(c4_sock)
    assert kind == ERROR and payload["error"] == "h264_scene_dependencies_unavailable"
    assert "NumPy and OpenCV" in payload["detail"]
  finally:
    c4_sock.close()
    pi_sock.close()


def test_scene_asset_failure_reports_detail_before_starting_webrtc(monkeypatch):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  monkeypatch.setattr(h264_scene, "H264Scene", Mock(side_effect=RuntimeError("Required cluster fonts missing: NotoSansKR-Bold.ttf")))
  receiver = Mock()
  monkeypatch.setattr(h264_receiver, "H264Receiver", receiver)
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(1.0)
  try:
    with pytest.raises(RuntimeError, match="Required cluster fonts missing"):
      h264_receiver.receive_h264_session(pi_sock, None, HELLO_DATA)
    kind, _, payload = read_control(c4_sock)
    assert kind == ERROR and payload == {
      "error": "h264_scene_unavailable", "detail": "Required cluster fonts missing: NotoSansKR-Bold.ttf",
    }
    receiver.assert_not_called()
    assert pi_sock.gettimeout() == 0.25
  finally:
    c4_sock.close()
    pi_sock.close()


def test_decoder_warning_counter_is_scoped_to_running_receiver(fake_aiortc):
  decoder_log = logging.getLogger("aiortc.codecs.h264")
  original_handlers = decoder_log.handlers[:]
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    receiver.start()
    assert read_control(c4_sock)[0] == OFFER
    decoder_log.warning("H264Decoder() failed to decode, skipping package: %s", "avcodec_send_packet()")
    decoder_log.warning("Unrelated H.264 warning")
    decoder_log.info("H264Decoder() failed to decode")
    logging.getLogger("unrelated").warning("failed to decode")
    assert receiver.decoder_errors == 1
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()
  assert decoder_log.handlers == original_handlers
  decoder_log.warning("H264Decoder() failed to decode, skipping package")
  assert receiver.decoder_errors == 1


def test_decoder_warning_requests_idr_before_the_periodic_fps_report(monkeypatch, fake_aiortc):
  monkeypatch.setattr(h264_receiver, "FEEDBACK_INTERVAL", 30.0)
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(1.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    receiver.start()
    assert read_control(c4_sock)[0] == OFFER
    c4_sock.sendall(pack_control(ANSWER, {"type": "answer", "sdp": "v=0\r\n"}))
    wait_until(lambda: receiver.received_frames == 1)
    logging.getLogger("aiortc.codecs.h264").warning("H264Decoder() failed to decode, skipping package")
    kind, _, feedback = read_control(c4_sock)
    assert kind == FEEDBACK and feedback["keyframe_request"] is True
    assert feedback["decoder_errors"] == 1 and feedback["recovery_reason"] == "decoder_error"
    assert "display_fps" not in feedback  # Immediate recovery never resets FPS accounting.
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()


def test_decoder_recovery_coalesces_warning_bursts_and_limits_idr_requests(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  now, sent, delays = [10.0], [], []

  async def pause(delay):
    delays.append(delay)
    now[0] += delay

  async def send(kind, payload):
    assert kind == FEEDBACK
    sent.append((now[0], payload))
    if len(sent) == 1:
      receiver.decoder_errors += 5
      receiver._recovery_event.set()
    else:
      raise asyncio.CancelledError

  async def run():
    receiver._recovery_event = asyncio.Event()
    receiver._recovery_event.set()
    receiver.decoder_errors = 1
    with pytest.raises(asyncio.CancelledError):
      await receiver._recover_video()

  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(sleep=pause))
  monkeypatch.setattr(receiver, "_send_control", send)
  asyncio.run(run())
  assert delays == [h264_receiver.RECOVERY_INTERVAL]
  assert [payload["decoder_errors"] for _, payload in sent] == [1, 6]
  assert sent[1][0] - sent[0][0] == pytest.approx(h264_receiver.RECOVERY_INTERVAL)


def run_feedback_ticks(monkeypatch, receiver, tick_count, update):
  now, feedbacks = [0.0], []

  async def sleep(interval):
    assert interval == h264_receiver.FEEDBACK_INTERVAL
    now[0] += interval
    if len(feedbacks) >= tick_count:
      raise asyncio.CancelledError
    update(len(feedbacks) + 1, now[0])

  async def send(kind, payload):
    assert kind == FEEDBACK
    feedbacks.append(payload)

  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(sleep=sleep))
  monkeypatch.setattr(receiver, "_send_control", send)
  with pytest.raises(asyncio.CancelledError):
    asyncio.run(receiver._feedback())
  return feedbacks


def test_keyframe_feedback_only_requests_new_decode_errors_or_existing_video_staleness(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, now):
    if tick == 2:
      receiver.decoder_errors = 1
    if tick >= 4:
      receiver.publish_rgb("decoded", received_at=now - (0.5 if tick == 5 else 0.49))
    if tick == 7:
      receiver.decoder_errors = 3

  feedbacks = run_feedback_ticks(monkeypatch, receiver, 8, update)
  assert [item["keyframe_request"] for item in feedbacks] == [False, True, False, False, True, False, True, False]
  assert [item["decoder_errors"] for item in feedbacks] == [0, 1, 1, 1, 1, 1, 3, 3]
  assert feedbacks[0]["video_stale"] and feedbacks[0]["video_age_ms"] is None
  assert feedbacks[4]["video_age_ms"] == pytest.approx(500.0)
  assert all(type(item["keyframe_request"]) is bool for item in feedbacks)


def test_receiver_perf_logs_decode_error_delta_over_each_ten_seconds(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, _now):
    if tick in (2, 11):
      receiver.decoder_errors += 1 if tick == 2 else 2

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    feedbacks = run_feedback_ticks(monkeypatch, receiver, 20, update)
  messages = [record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage()]
  assert len(messages) == 2
  assert "decoder_errors=1 |" in messages[0] and "decoder_errors=2 |" in messages[1]
  assert feedbacks[-1]["decoder_errors"] == 3


def test_receiver_stage_timings_average_only_present_valid_samples(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, _now):
    receiver.record_display(0.0, 0.02, 0.03, scene_timings={"path": 0.01, "hud": float("nan"), "prepare": -1},
                            display_timings={"rgb_surface": 0.002, "upload": True})
    if tick % 2 == 0:
      receiver.record_display(0.0, 0.02, 0.03, scene_timings={"path": 0.02}, display_timings={})

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    run_feedback_ticks(monkeypatch, receiver, 10, update)
  message = next(record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage())
  assert "scene_path_avg=13.3ms" in message and "hdmi_rgb_surface_avg=2.0ms" in message
  assert "scene_hud_avg=" not in message and "scene_prepare_avg=" not in message and "hdmi_upload_avg=" not in message


@pytest.mark.parametrize("width,height", [(8192, 480), (4096, 2160)])
def test_decoder_checks_dimensions_before_allocating_rgb(width, height):
  convert = Mock()
  frame = SimpleNamespace(width=width, height=height, to_ndarray=convert)
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  async def consume():
    receiver.stop_event = asyncio.Event()
    await receiver._consume_video(SimpleNamespace(recv=AsyncMock(return_value=frame)))
    assert receiver.stop_event.is_set()

  asyncio.run(consume())
  convert.assert_not_called()
  assert isinstance(receiver._async_error, ConnectionError) and "pixel budget" in str(receiver._async_error)


@pytest.mark.parametrize("cancel_during_conversion", [False, True])
def test_rgb_conversion_leaves_signaling_loop_responsive_and_has_only_one_frame_in_flight(cancel_during_conversion):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  started, release = threading.Event(), threading.Event()
  owner, conversion_threads, received = threading.get_ident(), [], []

  def convert(**kwargs):
    assert kwargs == {"format": "rgb24"}
    conversion_threads.append(threading.get_ident())
    started.set()
    assert release.wait(timeout=2.0)
    return "decoded RGB"

  async def recv():
    received.append(True)
    if len(received) > 1:
      await asyncio.Event().wait()
    return SimpleNamespace(width=2, height=2, to_ndarray=convert)

  async def run():
    receiver.stop_event = asyncio.Event()
    task = asyncio.create_task(receiver._consume_video(SimpleNamespace(recv=recv)))
    try:
      for _ in range(200):
        if started.is_set():
          break
        await asyncio.sleep(0.001)
      assert started.is_set() and conversion_threads == [conversion_threads[0]]
      assert conversion_threads[0] != owner and len(received) == 1
      # A control callback still runs while RGB is deliberately blocked.
      receiver.publish_state({"state_seq": 7})
      assert receiver.state_revision == 1 and receiver.received_frames == 0
      if cancel_during_conversion:
        receiver.closing = True
        task.cancel()
      release.set()
      if not cancel_during_conversion:
        for _ in range(200):
          if receiver.received_frames:
            break
          await asyncio.sleep(0.001)
        assert receiver.received_frames == 1 and receiver.latest_rgb[0] == "decoded RGB"
    finally:
      release.set()
      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task
    if cancel_during_conversion:
      assert receiver.received_frames == 0 and receiver.latest_rgb is None
  asyncio.run(run())


def test_decoded_rgb_replacement_preserves_latest_state_arrival_time():
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  receiver.publish_state({"state_seq": 3}, received_at=10.0)
  receiver.publish_rgb("old decoded picture", received_at=10.1)
  receiver.publish_rgb("latest decoded picture", received_at=10.2)
  assert receiver.take_updates(timeout=0) == (("latest decoded picture", 10.2), ({"state_seq": 3}, 10.0))
  assert receiver.received_frames == 2 and receiver.dropped_frames == 1
  receiver.publish_rgb("next picture", received_at=10.3)
  assert receiver.take_updates(timeout=0) == (("next picture", 10.3), None)
  assert receiver.last_state_at == 10.0


@pytest.mark.parametrize("changes", [
  {"codec": "unsupported"}, {"transport": "tcp"}, {"camera": "unknown"}, {"width": 100000},
  {"width": True}, {"fps": 0}, {"width": 4096, "height": 2160},
])
def test_invalid_hello_cannot_start_decoder_or_allocate_a_huge_scene(changes):
  with pytest.raises(ValueError):
    h264_receiver.H264Receiver(None, {**HELLO_DATA, **changes})


def test_receiver_validates_h264_hello_before_starting_video(monkeypatch):
  c4_sock, pi_sock = socket.socketpair()
  display = SimpleNamespace(pump_events=lambda: True, set_stream_mode=Mock())
  called = []
  monkeypatch.setattr(cluster_receiver, "receive_h264_session", lambda sock, screen, hello, timeout, maximum:
                      called.append((sock, screen, hello, timeout, maximum)) or 5)
  try:
    c4_sock.sendall(pack_control(HELLO, HELLO_DATA))
    assert cluster_receiver.receive_frames(pi_sock, display, max_frames=5, frame_timeout=0.5) == 5
    assert called == [(pi_sock, display, HELLO_DATA, 0.5, 5)]
  finally:
    c4_sock.close()
    pi_sock.close()


def test_receiver_rejects_non_hello_h264_packet_without_entering_video_mode():
  c4_sock, pi_sock = socket.socketpair()
  try:
    c4_sock.sendall(pack_control(STATE, {}))
    with pytest.raises(ValueError, match="Expected H.264 session hello"):
      cluster_receiver.receive_frames(pi_sock, SimpleNamespace(pump_events=lambda: True), frame_timeout=0.5)
  finally:
    c4_sock.close()
    pi_sock.close()


@pytest.mark.parametrize("stall", [False, True])
def test_scene_updates_preserve_arrival_times_and_sdl_stays_on_caller_thread(monkeypatch, stall):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  owner = threading.get_ident()
  now, scenes, receivers, presented, waiting = [10.0], [], [], [], []
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))

  class Scene:
    def __init__(self, *_args):
      self.states, self.videos = [], []
      scenes.append(self)

    def update(self, state, received_at):
      self.states.append((state, received_at))

    def set_video(self, rgb, received_at, take_ownership=False):
      assert take_ownership
      self.videos.append((rgb, received_at))

    def render(self):
      assert threading.get_ident() == owner
      return f"scene {len(self.videos)}"

  class Receiver:
    def __init__(self, *_args):
      self.condition = threading.Condition()
      self.received_frames = self.displayed_frames = 0
      self.last_video_at = None
      self.last_state_at = 9.98
      self.screen_off = self.done = self.closed = False
      self.step = 0
      receivers.append(self)

    def start(self):
      pass

    def take_updates(self, timeout=0.02):
      self.step += 1
      if self.step == 1:
        self.received_frames = 1
        self.last_video_at = 9.99
        return ("first RGB", 9.99), ({"state_seq": 1}, 9.98)
      if stall and self.step == 2:
        now[0] = 10.6
        return None, None
      if stall and self.step == 3:
        now[0] = 12.01
        return None, None
      if stall and self.step == 4:
        now[0] = 12.02
        return None, None
      now[0] = 12.1 if stall else 10.1
      self.received_frames = 2
      self.last_video_at = now[0]
      return ("second RGB", now[0]), None

    def close(self):
      self.closed = True

    def record_display(self, _started, _rendered, _displayed, _video_at, presented, **_kwargs):
      self.displayed_frames += int(presented)

  def present(image):
    assert threading.get_ident() == owner
    presented.append(image)
    return True

  display = SimpleNamespace(pump_events=lambda: threading.get_ident() == owner, screen_off=False,
                            send_rgb=present, show_waiting=lambda: waiting.append(True) or True,
                            last_frame_presented=True)
  monkeypatch.setattr(h264_scene, "H264Scene", Scene)
  monkeypatch.setattr(h264_receiver, "H264Receiver", Receiver)
  assert h264_receiver.receive_h264_session(None, display, HELLO_DATA, max_frames=2) == 2
  assert scenes[0].states == [({"state_seq": 1}, 9.98)]
  assert scenes[0].videos == [("first RGB", 9.99), ("second RGB", 12.1 if stall else 10.1)]
  assert presented == (["scene 1", "scene 1", "scene 2"] if stall else ["scene 1", "scene 2"])
  assert waiting == ([True] if stall else [])
  assert receivers[0].closed and receivers[0].displayed_frames == (3 if stall else 2)


@pytest.mark.parametrize("render_cost,display_cost,screen_off", [
  (0.025, 0.014, False), (0.037, 0.014, False), (0.045, 0.014, False),
  (0.08, 0.014, False), (0.025, 0.014, True),
])
def test_render_deadlines_preserve_phase_without_discarding_a_slot_after_overrun(monkeypatch, render_cost, display_cost, screen_off):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  clock, starts, waits, receivers = [1.0], [], [], []
  jitter = (0.0, -0.003, 0.004, -0.001, 0.002)
  arrivals = [1.0 + index * 0.05 + jitter[index % len(jitter)] for index in range(100)]
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: clock[0]))

  class Scene:
    def __init__(self, *_args):
      pass

    def update(self, *_args, **_kwargs):
      pass

    def set_video(self, *_args, **_kwargs):
      pass

    def render(self):
      starts.append(clock[0])
      clock[0] += render_cost
      return "composed RGB"

  class Receiver:
    def __init__(self, *_args):
      self.condition = threading.Condition()
      self.received_frames = 0
      self.last_video_at = self.last_state_at = None
      self.screen_off = self.done = self.closed = False
      receivers.append(self)

    def start(self):
      pass

    def take_updates(self, timeout=0.02):
      waits.append(timeout)
      assert 0.0 <= timeout <= 0.02
      assert len(waits) < 2000, "A receiver with no pending updates must block, not spin"
      wake_at = clock[0] + timeout
      if self.received_frames < len(arrivals) and arrivals[self.received_frames] <= wake_at:
        clock[0] = max(clock[0], arrivals[self.received_frames])
        while self.received_frames < len(arrivals) and arrivals[self.received_frames] <= clock[0]:
          self.last_video_at = self.last_state_at = arrivals[self.received_frames]
          self.received_frames += 1
        return ("decoded RGB", self.last_video_at), None
      assert timeout > 0.0, "A due pending render must run before another empty poll"
      clock[0] = wake_at
      return None, None

    def record_display(self, *_args, **_kwargs):
      pass

    def close(self):
      self.closed = True

  def present(_image):
    clock[0] += display_cost
    return True

  monkeypatch.setattr(h264_scene, "H264Scene", Scene)
  monkeypatch.setattr(h264_receiver, "H264Receiver", Receiver)
  display = SimpleNamespace(pump_events=lambda: True, screen_off=screen_off, send_rgb=present, last_frame_presented=True)
  assert h264_receiver.receive_h264_session(None, display, HELLO_DATA, max_frames=100) == 100
  assert receivers[0].closed
  if screen_off:
    assert not starts
    assert all(timeout == 0.02 for timeout in waits)
  elif render_cost + display_cost < 0.05:
    # Replay the previous fixed-poll/reset-to-now policy with the same
    # arrivals and work. A pending picture can miss the next 50ms slot.
    legacy_now, legacy_last, legacy_index, legacy_frames = 1.0, 0.0, 0, 0
    legacy_pending = False
    while legacy_index < len(arrivals):
      if arrivals[legacy_index] <= legacy_now + 0.02:
        legacy_now = max(legacy_now, arrivals[legacy_index])
        while legacy_index < len(arrivals) and arrivals[legacy_index] <= legacy_now:
          legacy_index += 1
          legacy_pending = True
      else:
        legacy_now += 0.02
      if legacy_pending and legacy_now - legacy_last >= 0.05:
        legacy_last = legacy_now
        legacy_now += render_cost + display_cost
        legacy_frames += 1
        legacy_pending = False
    assert legacy_frames <= 86
    assert len(starts) >= 96
    assert (len(starts) - 1) / (starts[-1] - starts[0]) >= 19.5
    assert any(0.0 < timeout < 0.015 for timeout in waits)
  else:
    work = render_cost + display_cost
    # Work that just exceeds 50 ms must run near its actual throughput,
    # instead of alternating between two 50 ms deadlines (~10 FPS).
    expected_fps = 1.0 / work
    actual_fps = (len(starts) - 1) / (starts[-1] - starts[0])
    assert actual_fps >= expected_fps * 0.97
    assert all(second - first >= work - 1e-6 for first, second in zip(starts, starts[1:], strict=False))
