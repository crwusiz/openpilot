import asyncio
import builtins
import socket
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import cluster_receiver, h264_receiver
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.cluster_protocol import recv_exact
from openpilot.selfdrive.addon.cluster.hdmi_display.network_protocol import pack_frame_header
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
  {"codec": "jpeg"}, {"transport": "tcp"}, {"camera": "unknown"}, {"width": 100000},
  {"width": True}, {"fps": 0}, {"width": 4096, "height": 2160},
])
def test_invalid_hello_cannot_start_decoder_or_allocate_a_huge_scene(changes):
  with pytest.raises(ValueError):
    h264_receiver.H264Receiver(None, {**HELLO_DATA, **changes})


def test_receiver_detects_h264_hello_before_jpeg_header_decoding(monkeypatch):
  c4_sock, pi_sock = socket.socketpair()
  display = SimpleNamespace(pump_events=lambda: True)
  called = []
  monkeypatch.setattr(h264_receiver, "receive_h264_session", lambda sock, screen, hello, timeout, maximum:
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


def test_jpeg_stream_does_not_import_optional_h264_runtime(monkeypatch):
  original_import = builtins.__import__

  def guarded_import(name, *args, **kwargs):
    if name.split(".")[-1] in ("aiortc", "av", "h264_receiver", "h264_scene"):
      pytest.fail(f"JPEG unexpectedly imported {name}")
    return original_import(name, *args, **kwargs)

  c4_sock, pi_sock = socket.socketpair()
  display = SimpleNamespace(pump_events=lambda: True, send_jpeg=lambda _jpeg: True)
  try:
    c4_sock.sendall(pack_frame_header(1, 4) + b"jpeg")
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert cluster_receiver.receive_frames(pi_sock, display, max_frames=1) == 1
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

    def set_video(self, rgb, received_at):
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

    def take_updates(self):
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

    def record_display(self, _started, _rendered, _displayed, _video_at, presented):
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
