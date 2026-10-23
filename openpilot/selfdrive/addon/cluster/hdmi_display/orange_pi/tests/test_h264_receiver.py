import asyncio
import builtins
import gc
import logging
from pathlib import Path
import socket
import sys
import threading
import time
import weakref
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
    assert feedback["camera_display_fps"] > 0
    assert not feedback["video_stale"] and not feedback["state_stale"]
    assert feedback["decoder_backend"] == "aiortc_pyav_software"
    assert feedback["screen_off"] is False
    assert [codec.mimeType for codec in fake_aiortc[0].codecs] == ["video/H264"]
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()
  assert fake_aiortc[0].closed and not receiver.thread.is_alive()


def test_camera_display_count_excludes_metadata_only_stale_and_unpresented_frames():
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  receiver.record_display(1.0, 1.02, 1.03, video_received_at=0.99)
  receiver.record_display(1.1, 1.12, 1.13)
  receiver.record_display(1.2, 1.22, 1.23, video_received_at=1.19, presented=False)
  receiver.record_display(1.3, 1.32, 1.33, video_received_at=1.29)
  receiver.record_display(2.0, 2.02, 2.03, video_received_at=1.4, camera_visible=False)
  assert receiver.metadata_displayed_frames == 1
  assert receiver.hidden_video_frames == 1
  assert receiver.displayed_frames == 4
  assert receiver.displayed_video_frames == 2
  # Keeping the arrival timestamp preserves queue-age evidence even when the
  # scene hides an expired picture instead of showing it as fresh video.
  assert receiver._stats["queue_count"] == 4
  assert receiver._stats["queue"] == pytest.approx(0.63)


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


def test_control_timeout_names_the_failed_stage():
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  # A pending read must yield to wait_for's deadline rather than return data.
  async def pending_read(*_args):
    await asyncio.Event().wait()

  async def run_pending():
    receiver.loop = SimpleNamespace(sock_recv=pending_read)
    with pytest.raises(TimeoutError, match="H.264 control packet timed out after 0.01 seconds"):
      await receiver._read_control(0.01)

  asyncio.run(run_pending())


@pytest.mark.parametrize("stalled, expected_seconds, message", [
  ("video", 8, "WebRTC video stalled for eight seconds"),
  ("state", 8, "H.264 metadata stalled for eight seconds"),
  ("startup", 15, "No decoded WebRTC video within 15 seconds"),
])
def test_receive_watchdog_detects_actual_stalls_even_when_the_other_stream_keeps_arriving(
  monkeypatch, stalled, expected_seconds, message,
):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  now = [10.0]
  receiver.last_video_at = None if stalled == "startup" else now[0]
  receiver.last_state_at = now[0]

  async def tick(_interval):
    now[0] += 1.0
    if stalled == "state":
      receiver.publish_rgb("live pixels", received_at=now[0])
    else:
      receiver.publish_state({"state_seq": int(now[0])}, received_at=now[0])

  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(sleep=tick))
  with pytest.raises(TimeoutError, match=message):
    asyncio.run(receiver._watchdog(started=10.0))
  assert now[0] == 10.0 + expected_seconds


def test_receive_watchdog_keeps_video_that_recovers_after_more_than_two_seconds(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  now = [10.0]
  receiver.last_video_at = receiver.last_state_at = now[0]

  async def tick(_interval):
    now[0] += 1.0
    receiver.publish_state({"state_seq": int(now[0])}, received_at=now[0])
    # A slow but progressing stream must not be reset by a 2s sender hint.
    if int(now[0] - 10.0) % 3 == 0:
      receiver.publish_rgb("new pixels", received_at=now[0])
    if now[0] == 40.0:
      raise asyncio.CancelledError

  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(sleep=tick))
  with pytest.raises(asyncio.CancelledError):
    asyncio.run(receiver._watchdog(started=10.0))
  assert receiver.received_frames == 10


def test_worker_failure_records_reason_and_stream_ages_without_a_traceback(caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  receiver.last_video_at = receiver.last_state_at = time.monotonic() - 2
  receiver.received_frames, receiver.decoder_errors = 123, 4
  receiver._run = AsyncMock(side_effect=ConnectionError("C4 closed H.264 control connection"))
  with caplog.at_level(logging.WARNING, logger="cluster_receiver"):
    receiver._run_thread()
  assert receiver.done and isinstance(receiver.error, ConnectionError)
  message = next(record.getMessage() for record in caplog.records if "CLUSTER_H264_STOP" in record.getMessage())
  assert "reason=ConnectionError" in message and "C4 closed H.264 control connection" in message
  assert "video_age_ms=" in message and "state_age_ms=" in message
  assert "rx_frames=123" in message and "decoder_errors=4" in message


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
def test_rgb_conversion_leaves_signaling_loop_responsive_and_has_only_one_conversion_in_flight(cancel_during_conversion):
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
      assert conversion_threads[0] != owner
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


def test_decoded_burst_converts_only_the_latest_pending_picture_and_keeps_control_responsive():
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  started, release = threading.Event(), threading.Event()
  converted, decoded = [], []

  def frame(index):
    def convert(**_kwargs):
      converted.append(index)
      if index == 0:
        started.set()
        assert release.wait(timeout=2)
      return index
    return SimpleNamespace(width=2, height=2, to_ndarray=convert)

  async def run():
    queued = asyncio.Queue()
    receiver.stop_event = asyncio.Event()

    async def recv():
      index = await queued.get()
      decoded.append(index)
      return frame(index)

    task = asyncio.create_task(receiver._consume_video(SimpleNamespace(recv=recv)))
    try:
      queued.put_nowait(0)
      for _ in range(200):
        if started.is_set():
          break
        await asyncio.sleep(0.001)
      assert started.is_set()
      queued.put_nowait(1)
      queued.put_nowait(2)
      await asyncio.sleep(0.02)
      receiver.publish_state({"state_seq": 9})
      assert receiver.state_revision == 1
      release.set()
      for _ in range(200):
        if receiver.latest_rgb is not None and receiver.latest_rgb[0] == 2:
          break
        await asyncio.sleep(0.001)
      assert decoded == [0, 1, 2]  # All reference pictures were decoded first.
      assert converted == [0, 2]
      assert receiver.latest_rgb[0] == 2
      assert receiver.decoded_frames == 3 and receiver.skipped_rgb == 1
    finally:
      release.set()
      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task

  asyncio.run(run())


def test_video_freshness_includes_rgb_conversion_time(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  clock = [10.0]
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: clock[0]))

  def convert(**_kwargs):
    clock[0] = 10.05
    return "RGB pixels"

  async def run():
    receiver.stop_event = asyncio.Event()
    first = True

    async def recv():
      nonlocal first
      if not first:
        await asyncio.Event().wait()
      first = False
      return SimpleNamespace(width=2, height=2, to_ndarray=convert)

    task = asyncio.create_task(receiver._consume_video(SimpleNamespace(recv=recv)))
    try:
      for _ in range(200):
        if receiver.latest_rgb is not None:
          break
        await asyncio.sleep(0.001)
      assert receiver.latest_rgb == ("RGB pixels", 10.0)
      assert receiver._stats["convert"] == pytest.approx(0.05)
    finally:
      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task

  asyncio.run(run())


@pytest.mark.parametrize("changes", [
  {"codec": "unsupported"}, {"transport": "tcp"}, {"camera": "unknown"}, {"width": 100000},
  {"width": True}, {"fps": 0}, {"width": 4096, "height": 2160},
])
def test_invalid_hello_cannot_start_decoder_or_allocate_a_huge_scene(changes):
  with pytest.raises(ValueError):
    h264_receiver.H264Receiver(None, {**HELLO_DATA, **changes})


def test_receiver_validates_h264_hello_before_starting_video(monkeypatch):
  c4_sock, pi_sock = socket.socketpair()
  display = SimpleNamespace(pump_events=lambda: True)
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


def test_failure_after_taking_updates_is_not_mistaken_for_normal_session_completion(monkeypatch):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  scene = SimpleNamespace(close=Mock())
  error = ConnectionError("receiver failed between the update and completion checks")
  receiver = SimpleNamespace(condition=threading.Condition(), last_video_at=None, last_state_at=None,
                             screen_off=False, done=False, received_frames=0, start=Mock(), close=Mock())

  def take_updates(timeout=0.02):
    if receiver.done:
      raise error
    receiver.done = True
    return None, None

  receiver.take_updates = take_updates
  monkeypatch.setattr(h264_scene, "H264Scene", lambda *_args: scene)
  monkeypatch.setattr(h264_receiver, "H264Receiver", lambda *_args: receiver)
  display = SimpleNamespace(pump_events=lambda: True, screen_off=True)
  with pytest.raises(ConnectionError, match="between the update and completion checks"):
    h264_receiver.receive_h264_session(None, display, HELLO_DATA)
  receiver.close.assert_called_once()
  scene.close.assert_called_once()


def test_failed_session_releases_renderer_without_waiting_for_cyclic_gc(monkeypatch):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  scene_class = h264_scene.H264Scene
  renderers = []

  def create_scene(*_args):
    scene = scene_class(Path(__file__).resolve().parents[5] / "assets")
    renderers.append(weakref.ref(scene.renderer))
    return scene

  receiver = SimpleNamespace(condition=threading.Condition(), start=Mock(), close=Mock(),
                             take_updates=Mock(side_effect=ConnectionError("C4 reset connection")))
  monkeypatch.setattr(h264_scene, "H264Scene", create_scene)
  monkeypatch.setattr(h264_receiver, "H264Receiver", lambda *_args: receiver)
  retained_error = None
  was_enabled = gc.isenabled()
  gc.disable()
  try:
    try:
      h264_receiver.receive_h264_session(None, SimpleNamespace(pump_events=lambda: True), HELLO_DATA)
    except ConnectionError as error:
      # Keep the traceback and its receive_h264_session frame alive, as the
      # worker's saved exception can do across a rapid reconnect.
      retained_error = error
    assert retained_error is not None and retained_error.__traceback__ is not None
    assert renderers[0]() is None
  finally:
    retained_error = None
    if was_enabled:
      gc.enable()


@pytest.mark.parametrize("stall", [False, True])
@pytest.mark.parametrize("layered", [False, True])
def test_scene_updates_preserve_arrival_times_and_sdl_stays_on_caller_thread(monkeypatch, stall, layered):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  owner = threading.get_ident()
  now, scenes, receivers, presented, waiting, camera_visibility = [10.0], [], [], [], [], []
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

    def _make_image(self):
      assert threading.get_ident() == owner
      self.last_status = {"video_fresh": now[0] - self.videos[-1][1] <= 0.5}
      return f"scene {len(self.videos)}"

    def render(self):
      assert not layered
      return self._make_image()

    def render_layers(self):
      assert layered
      return self._make_image()

    def close(self):
      pass

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
      camera_visibility.append(_kwargs["camera_visible"])
      self.displayed_frames += int(presented)

  def present(image):
    assert threading.get_ident() == owner
    presented.append(image)
    return True

  display = SimpleNamespace(pump_events=lambda: threading.get_ident() == owner, screen_off=False,
                            send_rgb=present, show_waiting=lambda: waiting.append(True) or True,
                            last_frame_presented=True, can_present_layers=layered, send_layers=present,
                            compositor_backend="pi_gpu_camera" if layered else "cpu_scene")
  monkeypatch.setattr(h264_scene, "H264Scene", Scene)
  monkeypatch.setattr(h264_receiver, "H264Receiver", Receiver)
  assert h264_receiver.receive_h264_session(None, display, HELLO_DATA, max_frames=2) == 2
  assert scenes[0].states == [({"state_seq": 1}, 9.98)]
  assert scenes[0].videos == [("first RGB", 9.99), ("second RGB", 12.1 if stall else 10.1)]
  assert presented == (["scene 1", "scene 1", "scene 2"] if stall else ["scene 1", "scene 2"])
  assert waiting == ([True] if stall else [])
  assert camera_visibility == ([True, False, True] if stall else [True, True])
  assert receivers[0].closed and receivers[0].displayed_frames == (3 if stall else 2)
  assert receivers[0].gpu_camera == layered


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

    def close(self):
      pass

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


def test_gpu_displays_decoded_bursts_without_the_extra_source_timer(monkeypatch):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  def run(gpu):
    now, rendered = [1.0], []
    arrivals = [1 + index // 3 / 6 + (index % 3) * 0.015 for index in range(99)]
    monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class Scene:
      def __init__(self, *_args):
        self.video = None

      def set_video(self, rgb, **_kwargs):
        self.video = rgb

      update = staticmethod(lambda *_args, **_kwargs: None)

      def render(self):
        rendered.append(self.video)
        now[0] += 0.013
        return self.video

      render_layers = render
      close = staticmethod(lambda: None)

    class Receiver:
      def __init__(self, *_args):
        self.condition = threading.Condition()
        self.received_frames = self.polls = self.states = 0
        self.last_video_at = self.last_state_at = None
        self.done = False

      def take_updates(self, timeout):
        self.polls += 1
        assert self.polls < 1000, "Camera delivery must wait instead of busy polling"
        wake_at = min(now[0] + timeout, max(now[0], arrivals[self.received_frames]),
                      max(now[0], 1 + self.states * 0.05))
        now[0] = wake_at
        rgb = None
        while self.received_frames < len(arrivals) and arrivals[self.received_frames] <= wake_at + 1e-9:
          self.last_video_at = arrivals[self.received_frames]
          rgb = (self.received_frames, self.last_video_at)
          self.received_frames += 1
        state = None
        while 1 + self.states * 0.05 <= wake_at + 1e-9:
          self.last_state_at = 1 + self.states * 0.05
          self.states += 1
          state = ({"sequence": self.states}, self.last_state_at)
        return rgb, state

      start = close = staticmethod(lambda: None)
      record_display = staticmethod(lambda *_args, **_kwargs: None)

    def present(_image):
      now[0] += 0.012
      return True

    monkeypatch.setattr(h264_scene, "H264Scene", Scene)
    monkeypatch.setattr(h264_receiver, "H264Receiver", Receiver)
    display = SimpleNamespace(pump_events=lambda: True, screen_off=False, can_present_layers=gpu,
                              compositor_backend="pi_gpu_camera" if gpu else "cpu_scene",
                              send_layers=present, send_rgb=present, last_frame_presented=True)
    assert h264_receiver.receive_h264_session(None, display, HELLO_DATA, max_frames=99) == 99
    return rendered

  # Same 18fps camera, 20Hz metadata and 25ms composition: a 50ms timer and
  # metadata-only redraw replace burst pictures; GPU delivery keeps each one.
  previous, accelerated = run(False), run(True)
  assert len(set(previous)) < len(set(accelerated))
  assert accelerated == list(range(99))


@pytest.mark.parametrize("camera_stalls", [False, True])
@pytest.mark.parametrize("gpu", [False, True])
def test_metadata_waits_for_new_camera_picture_with_bounded_hud_fallback(monkeypatch, camera_stalls, gpu):
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_scene

  clock, rendered, scenes, receivers = [1.0], [], [], []
  events = [(1.0, "state", 0), (1.0, "video", 0)]
  if camera_stalls:
    events.extend((1.047 + index * 0.05, "state", index + 1) for index in range(10))
  else:
    for index in range(20):
      # Metadata is ready just before the next render slot, while the camera
      # arrives just after it. Rendering on metadata would repeat old pictures.
      events.extend(((1.047 + index * 0.05, "state", index + 1),
                     (1.055 + index * 0.05, "video", index + 1)))
  events.sort()
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: clock[0]))

  class Scene:
    def __init__(self, *_args):
      self.state = self.video = None
      scenes.append(self)

    def update(self, state, received_at):
      self.state = state, received_at

    def set_video(self, video, received_at, **_kwargs):
      self.video = video

    def render(self):
      rendered.append((clock[0], self.video, self.state))
      clock[0] += 0.025
      return "composed RGB"

    render_layers = render

    def close(self):
      pass

  class Receiver:
    def __init__(self, *_args):
      self.condition = threading.Condition()
      self.received_frames = 0
      self.last_video_at = self.last_state_at = None
      self.screen_off = self.done = self.closed = False
      self.index = 0
      self.polls = 0
      receivers.append(self)

    def start(self):
      pass

    def take_updates(self, timeout=0.02):
      self.polls += 1
      assert self.polls < 300, "Metadata coalescing must wait without busy polling"
      wake_at = clock[0] + timeout
      if self.index < len(events):
        wake_at = min(wake_at, max(clock[0], events[self.index][0]))
      clock[0] = wake_at
      rgb = state = None
      while self.index < len(events) and events[self.index][0] <= wake_at + 1e-9:
        arrival, kind, value = events[self.index]
        self.index += 1
        if kind == "video":
          rgb = value, arrival
          self.received_frames += 1
          self.last_video_at = arrival
        else:
          state = value, arrival
          self.last_state_at = arrival
      self.done = self.index == len(events) and clock[0] >= events[-1][0] + 0.12
      return rgb, state

    def record_display(self, *_args, **_kwargs):
      pass

    def close(self):
      self.closed = True

  def present(_image):
    clock[0] += 0.014
    return True

  monkeypatch.setattr(h264_scene, "H264Scene", Scene)
  monkeypatch.setattr(h264_receiver, "H264Receiver", Receiver)
  display = SimpleNamespace(pump_events=lambda: True, screen_off=False, send_rgb=present, send_layers=present,
                            can_present_layers=gpu, compositor_backend="pi_gpu_camera" if gpu else "cpu_scene",
                            last_frame_presented=True)
  assert h264_receiver.receive_h264_session(None, display, HELLO_DATA) == (1 if camera_stalls else 21)
  assert receivers[0].closed
  if camera_stalls:
    # GPU presentation gives short Wi-Fi gaps 200ms before metadata-only
    # redraws; then HUD remains at 10Hz and expiry still clears the camera.
    assert len(rendered) >= (4 if gpu else 6)
    assert rendered[1][0] == pytest.approx(1.2 if gpu else 1.1)
    assert rendered[1][2] == pytest.approx((4, 1.197) if gpu else (2, 1.097))
    assert any(1.5 <= at < 1.53 for at, *_rest in rendered)
    assert rendered[-1][2][0] == 10
  else:
    assert [video for _at, video, _state in rendered] == list(range(21))
    assert all(video == state[0] for _at, video, state in rendered)
