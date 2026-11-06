import asyncio
import builtins
import contextlib
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
  ANSWER, CONTROL_HEADER, ERROR, FEEDBACK, HELLO, OFFER, STATE, STATE_ZLIB, decode_payload, pack_control, pack_scene,
  unpack_control_header,
)


HELLO_DATA = {"codec": "h264", "transport": "webrtc", "camera": "road", "width": 1920, "height": 480, "fps": 20}


def test_slow_frame_keeps_render_and_present_stalls_separate_and_bounds_logs(caplog):
  assert h264_receiver._log_slow_frame(None, 10, 10.01, 10.03, 0.01, None, None, 9.99) is None
  assert not caplog.records
  stamp = h264_receiver._log_slow_frame(None, 10, 11.6, 11.62, 0.03,
                                        {"hud": 1.5, "path_wait": 0.0, "path": None, "unexpected": 50},
                                        {"upload": 0.002, "flip": float("nan")}, 9.99)
  assert stamp == 11.62
  message = caplog.records[-1].getMessage()
  assert "render_ms=1600.0" in message and "display_ms=20.0" in message
  assert "owner_cpu_ms=30.0" in message and "decoded_video_age_ms=1630.0" in message
  assert "scene_hud_ms=1500.0" in message and "hdmi_upload_ms=2.0" in message
  assert "scene_path_ms" not in message and "unexpected" not in message and "flip_ms" not in message
  assert h264_receiver._log_slow_frame(stamp, 12, 12.3, 12.32, 0.02, {}, {}, None) == stamp
  assert len(caplog.records) == 1
  assert h264_receiver._log_slow_frame(stamp, 17, 17.01, 17.5, 0.04, {}, {"upload": 0.49}, None) == 17.5
  assert len(caplog.records) == 2 and "decoded_video_age_ms=none" in caplog.records[-1].getMessage()


@pytest.mark.parametrize("before,kernel_max,requested,actual", [
  (212992, 212992, 212992, 425984),
  (212992, 4 * 1024 * 1024, 1024 * 1024, 2 * 1024 * 1024),
  (2 * 1024 * 1024, 4 * 1024 * 1024, None, 2 * 1024 * 1024),
  (500000, 212992, None, 500000),  # A lowered cap must not shrink the socket.
])
def test_linux_udp_receive_buffer_respects_kernel_cap_and_existing_larger_buffers(monkeypatch, caplog,
                                                                                  before, kernel_max, requested, actual):
  sock = Mock(getsockopt=Mock(side_effect=[before, actual]))
  transport = Mock(get_extra_info=Mock(return_value=sock))
  monkeypatch.setattr(h264_receiver, "sys", SimpleNamespace(platform="linux"))
  monkeypatch.setattr(h264_receiver.Path, "read_text", lambda *_args, **_kwargs: str(kernel_max))
  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    h264_receiver.configure_udp_receive_buffer(transport)
  transport.get_extra_info.assert_called_once_with("socket")
  if requested is None:
    sock.setsockopt.assert_not_called()
  else:
    sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_RCVBUF, requested)
  assert f"before={before}" in caplog.text and f"actual={actual}" in caplog.text
  assert f"kernel_max={kernel_max}" in caplog.text


@pytest.mark.parametrize("limit", [None, "unavailable", "-1", "0"])
def test_unreadable_or_invalid_linux_udp_limit_keeps_socket_usable(monkeypatch, caplog, limit):
  sock = Mock(getsockopt=Mock(return_value=212992))
  transport = Mock(get_extra_info=Mock(return_value=sock))
  monkeypatch.setattr(h264_receiver, "sys", SimpleNamespace(platform="linux"))
  def read(*_args, **_kwargs):
    if limit is None:
      raise OSError("proc unavailable")
    return limit
  monkeypatch.setattr(h264_receiver.Path, "read_text", read)
  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    h264_receiver.configure_udp_receive_buffer(transport)
  sock.setsockopt.assert_not_called()
  assert "action=keep_default" in caplog.text and "reason=limit_unavailable" in caplog.text


@pytest.mark.parametrize("before", [65536, 2 * 1024 * 1024])
def test_non_linux_udp_buffer_uses_platform_reported_size(monkeypatch, before):
  target = h264_receiver.UDP_RECEIVE_BUFFER_BYTES
  sock = Mock(getsockopt=Mock(side_effect=[before, max(before, target)]))
  monkeypatch.setattr(h264_receiver, "sys", SimpleNamespace(platform="win32"))
  h264_receiver.configure_udp_receive_buffer(Mock(get_extra_info=Mock(return_value=sock)))
  if before < target:
    sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_RCVBUF, target)
  else:
    sock.setsockopt.assert_not_called()


def test_udp_buffer_failure_does_not_close_the_transport(monkeypatch, caplog):
  sock = Mock(getsockopt=Mock(return_value=65536), setsockopt=Mock(side_effect=OSError("not permitted")))
  transport = Mock(get_extra_info=Mock(return_value=sock))
  monkeypatch.setattr(h264_receiver, "sys", SimpleNamespace(platform="win32"))
  h264_receiver.configure_udp_receive_buffer(transport)
  transport.close.assert_not_called()
  sock.close.assert_not_called()
  assert "action=keep_socket" in caplog.text and "not permitted" in caplog.text
  h264_receiver.configure_udp_receive_buffer(Mock(get_extra_info=Mock(return_value=None)))


def test_receiver_loop_tunes_its_udp_only_and_still_delivers_datagrams(caplog):
  control = Mock()
  receiver = h264_receiver.H264Receiver(control, HELLO_DATA)
  loops = []
  async def run():
    loop = asyncio.get_running_loop()
    loops.append(loop)
    received = loop.create_future()
    class Protocol(asyncio.DatagramProtocol):
      def datagram_received(self, data, address):
        received.set_result(data)
    transport, _protocol = await loop.create_datagram_endpoint(Protocol, local_addr=("127.0.0.1", 0))
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
      sender.sendto(b"H264 packet", transport.get_extra_info("sockname"))
      assert await asyncio.wait_for(received, 1.0) == b"H264 packet"
      assert loop.udp_sampler.sample()["udp_sockets"] == 1
    finally:
      sender.close()
      transport.close()
      await asyncio.sleep(0)
      assert loop.udp_sampler.sample()["udp_sockets"] == 0
  receiver._run = run
  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    receiver._run_thread()
  assert receiver.done and receiver.error is None
  assert isinstance(loops[0], h264_receiver._ReceiverEventLoop) and loops[0].is_closed()
  assert "[CLUSTER_H264_UDP_BUFFER]" in caplog.text
  control.getsockopt.assert_not_called()
  control.setsockopt.assert_not_called()
  default = asyncio.new_event_loop()
  try:
    assert not isinstance(default, h264_receiver._ReceiverEventLoop)
  finally:
    default.close()


@pytest.mark.parametrize("before,expected", [(524288, 2097152), (4194304, 4194304)])
def test_ice_ready_buffer_setup_repairs_library_overwrite_and_keeps_larger_socket_buffers(monkeypatch, caplog, before, expected):
  size = [before]
  sock = Mock(getsockopt=lambda *_args: size[0])
  sock.setsockopt.side_effect = lambda _level, _option, requested: size.__setitem__(0, requested * 2)
  transport = Mock(is_closing=Mock(return_value=False), get_extra_info=Mock(return_value=sock))
  closed = Mock(is_closing=Mock(return_value=True))
  loop = h264_receiver._ReceiverEventLoop()
  loop.udp_sampler.register(transport)
  loop.udp_sampler.register(closed)
  monkeypatch.setattr(h264_receiver, "sys", SimpleNamespace(platform="linux"))
  monkeypatch.setattr(h264_receiver.Path, "read_text", lambda *_args, **_kwargs: "1048576")
  try:
    with caplog.at_level(logging.INFO, logger="cluster_receiver"):
      loop.configure_udp_receive_buffers()
    assert size[0] == expected
    assert "phase=ice_ready" in caplog.text and f"actual={expected}" in caplog.text
    if before < expected:
      sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
    else:
      sock.setsockopt.assert_not_called()
    closed.get_extra_info.assert_not_called()
  finally:
    loop.close()


def test_ice_ready_socket_tuning_failure_keeps_connection_and_reports_phase(monkeypatch, caplog):
  sock = Mock(getsockopt=Mock(return_value=262144), setsockopt=Mock(side_effect=OSError("unavailable")))
  transport = Mock(is_closing=Mock(return_value=False), get_extra_info=Mock(return_value=sock))
  loop = h264_receiver._ReceiverEventLoop()
  loop.udp_sampler.register(transport)
  monkeypatch.setattr(h264_receiver, "sys", SimpleNamespace(platform="win32"))
  try:
    loop.configure_udp_receive_buffers()
    assert "action=keep_socket" in caplog.text and "phase=ice_ready" in caplog.text
    transport.close.assert_not_called()
    sock.close.assert_not_called()
  finally:
    loop.close()


def test_legacy_asyncio_still_runs_receiver_without_changing_global_policy(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  receiver._run = AsyncMock()
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(run=asyncio.run, set_event_loop=asyncio.set_event_loop,
                                                             CancelledError=asyncio.CancelledError))
  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    receiver._run_thread()
  receiver._run.assert_awaited_once()
  assert receiver.done and receiver.error is None
  assert "reason=legacy_asyncio" in caplog.text


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


def test_real_aioice_gathering_overwrite_is_repaired_before_receiver_offer(monkeypatch, fake_aiortc, caplog):
  aioice = pytest.importorskip("aioice")
  # Bind only local loopback; gathering has no STUN/TURN or external traffic.
  monkeypatch.setattr(aioice.ice, "get_host_addresses", lambda **_kwargs: ["127.0.0.1"])
  peer_type = sys.modules["aiortc"].RTCPeerConnection
  original_local, original_close = peer_type.setLocalDescription, peer_type.close
  target = h264_receiver.UDP_RECEIVE_BUFFER_BYTES
  multiplier = 2 if sys.platform.startswith("linux") else 1
  if multiplier == 2:
    target = min(target, int(Path("/proc/sys/net/core/rmem_max").read_text(encoding="ascii")))

  async def gather(peer, description):
    await original_local(peer, description)
    peer.ice = aioice.Connection(ice_controlling=False, stun_server=None, turn_server=None, use_ipv6=False)
    await peer.ice.gather_candidates()
    transports = asyncio.get_running_loop().udp_sampler.active_transports()
    assert len(transports) == 1
    peer.gather_socket = transports[0].get_extra_info("socket")
    peer.after_gather_buffer = peer.gather_socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

  async def close(peer):
    if hasattr(peer, "ice"):
      await peer.ice.close()
    await original_close(peer)

  monkeypatch.setattr(peer_type, "setLocalDescription", gather)
  monkeypatch.setattr(peer_type, "close", close)
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    with caplog.at_level(logging.INFO, logger="cluster_receiver"):
      receiver.start()
      assert read_control(c4_sock)[0] == OFFER
    peer = fake_aiortc[0]
    # The library shrinks the buffer after our endpoint hook. Its public
    # gathering lifecycle has now finished and the offer is not sent until
    # the receiver restores its own bounded request.
    assert peer.after_gather_buffer <= aioice.turn.UDP_SOCKET_BUFFER_SIZE * multiplier
    assert peer.gather_socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) >= target * multiplier
    assert "phase=endpoint" in caplog.text and "phase=ice_ready" in caplog.text
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()
  assert not receiver.thread.is_alive() and fake_aiortc[0].closed


def test_legacy_default_loop_still_negotiates_without_receiver_udp_setup(monkeypatch, fake_aiortc, caplog):
  monkeypatch.delattr(asyncio, "Runner")
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    with caplog.at_level(logging.INFO, logger="cluster_receiver"):
      receiver.start()
      assert read_control(c4_sock)[0] == OFFER
    c4_sock.sendall(pack_control(ANSWER, {"type": "answer", "sdp": "v=0\r\n"}))
    wait_until(lambda: receiver.received_frames == 1)
    assert "reason=legacy_asyncio" in caplog.text
    assert "phase=ice_ready" not in caplog.text
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()
  assert not receiver.thread.is_alive() and fake_aiortc[0].closed


def test_close_after_control_eof_finishes_peer_cleanup_without_a_second_cancellation(monkeypatch, fake_aiortc):
  peer_type = sys.modules["aiortc"].RTCPeerConnection
  original_close = peer_type.close
  entered, completed = threading.Event(), threading.Event()
  gate, cleanup_calls, cancel_callbacks = [], [], []

  async def finish_peer_close(peer):
    cleanup_calls.append(peer)
    release = asyncio.Event()
    gate.append(release)
    entered.set()
    await release.wait()
    await original_close(peer)
    completed.set()

  monkeypatch.setattr(peer_type, "close", finish_peer_close)
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  original_cancel = receiver._cancel_if_running

  def cancel_then_release(task):
    cancel_callbacks.append(task)
    original_cancel(task)
    # Release only after the actual close() cancellation callback ran. The
    # old unconditional cancel propagates into this suspended peer cleanup
    # before it can finish, so this controls the race without timing sleeps.
    gate[0].set()

  monkeypatch.setattr(receiver, "_cancel_if_running", cancel_then_release)
  try:
    receiver.start()
    assert read_control(c4_sock)[0] == OFFER
    c4_sock.shutdown(socket.SHUT_RDWR)
    assert entered.wait(timeout=2.0), "Control EOF must start the real peer teardown before local close"
    receiver.close()
    receiver.close()  # Repeated local close must be harmless and bounded.
    assert completed.is_set() and fake_aiortc[0].closed
    assert len(cleanup_calls) == len(cancel_callbacks) == 1
    assert receiver.done and not receiver.thread.is_alive() and receiver.loop is None
  finally:
    if receiver.loop is not None and gate:
      with contextlib.suppress(RuntimeError):
        receiver.loop.call_soon_threadsafe(gate[0].set)
    receiver.close()
    c4_sock.close()
    pi_sock.close()


@pytest.mark.parametrize("already_cancelled", [False, True])
def test_receiver_stop_does_not_issue_repeated_cancellation_for_a_live_task(already_cancelled):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  async def run():
    task = asyncio.create_task(asyncio.Event().wait())
    receiver._main_task = task
    await asyncio.sleep(0)
    if already_cancelled:
      task.cancel()
    receiver._cancel_if_running(task)
    receiver._cancel_if_running(task)
    assert task.cancelling() == 1
    with pytest.raises(asyncio.CancelledError):
      await task

  asyncio.run(run())


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


@pytest.mark.parametrize("acknowledged", [False, True])
def test_compressed_scene_requires_capability_offer_and_answer(fake_aiortc, acknowledged):
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(2)
  receiver = h264_receiver.H264Receiver(pi_sock, {**HELLO_DATA, "state_compression": "zlib"})
  snapshot = {"model_valid": True, "source_size": [1344, 760], "hud": {"v_ego": 25},
              "path": {"path_x": [1.0, 2.0], "path_y": [0.0, 0.0], "path_z": [0.0, 0.0]}}
  try:
    receiver.start()
    kind, _, offer = read_control(c4_sock)
    assert kind == OFFER and offer["state_compression"] == "zlib"
    answer = {"type": "answer", "sdp": "v=0\r\n"}
    if acknowledged:
      answer["state_compression"] = "zlib"
    c4_sock.sendall(pack_control(ANSWER, answer))
    packet = pack_scene(snapshot, compression="zlib")
    assert unpack_control_header(packet[:CONTROL_HEADER.size])[0] == STATE_ZLIB
    c4_sock.sendall(packet)
    if acknowledged:
      wait_until(lambda: receiver.state_revision == 1)
      assert receiver.state_compression == "zlib"
      assert receiver.take_updates(timeout=0)[1][0] == snapshot
    else:
      wait_until(lambda: receiver.done)
      with pytest.raises(ValueError, match="was not negotiated"):
        receiver.take_updates(timeout=0)
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()


def test_camera_gap_excludes_intervening_hud_refreshes_and_ignores_hidden_or_unpresented_video():
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  receiver.record_display(1, 1.01, 1.02, video_received_at=1)
  for index in range(1, 21):
    shown = 1.02 + index * 0.1
    receiver.record_display(shown - 0.02, shown - 0.01, shown)
  receiver.record_display(3.1, 3.11, 3.12, video_received_at=3.1)
  assert receiver._stats["camera_gap"] == pytest.approx(2.1)
  assert receiver._stats["gap"] == pytest.approx(0.1)
  receiver.record_display(3.2, 3.21, 3.22, video_received_at=3.2, camera_visible=False)
  receiver.record_display(3.3, 3.31, 3.32, video_received_at=3.3, presented=False)
  assert receiver._last_camera_display_at == pytest.approx(3.12)


def test_camera_gap_and_gpu_upload_savings_are_logged_per_window(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, now):
    receiver.record_display(now - 0.02, now - 0.01, now, video_received_at=now - 0.03 if tick % 3 == 0 else None,
                            display_timings={"upload_bytes": 1000, "path_upload_skipped": 1, "camera_upload_skipped": 0,
                                             "overlay_upload_skipped": 1})

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    feedbacks = run_feedback_ticks(monkeypatch, receiver, 20, update)
  assert max(item["camera_display_gap_max_ms"] for item in feedbacks) == pytest.approx(3000)
  messages = [record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage()]
  assert len(messages) == 2
  assert all("camera_display_gap_max=3000.0ms" in message for message in messages)
  assert all("hdmi_upload_kbps=8.0" in message and "hdmi_path_reused=10" in message for message in messages)
  assert all("hdmi_camera_reused=0" in message for message in messages)
  assert all("hdmi_overlay_reused=10" in message for message in messages)


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


def test_existing_watchdog_records_loop_delay_and_its_own_cpu_separately(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  now, cpu = [10.0], [4.0]
  ticks = iter([(0.1, 0.002), (0.35, 0.03)])

  async def tick(interval):
    assert interval == 0.1
    try:
      wall_elapsed, cpu_elapsed = next(ticks)
    except StopIteration:
      raise asyncio.CancelledError from None
    now[0] += wall_elapsed
    cpu[0] += cpu_elapsed

  unknown = h264_receiver._loop_metrics(receiver._stats)
  assert unknown == {"loop_samples": 0, "loop_lag_max_ms": None, "loop_wall_ms": None, "loop_cpu_ms": None}
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(h264_receiver, "thread_time", lambda: cpu[0])
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(sleep=tick))
  with pytest.raises(asyncio.CancelledError):
    asyncio.run(receiver._watchdog(started=10.0))
  metrics = h264_receiver._loop_metrics(receiver._stats)
  assert metrics == pytest.approx({"loop_samples": 2, "loop_lag_max_ms": 250, "loop_wall_ms": 450, "loop_cpu_ms": 32})


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


def test_decoder_recovery_coalesces_warning_bursts_and_limits_idr_requests():
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  receiver.decoder_errors = 1
  sent = [receiver._take_recovery_request(10.0)]
  receiver.decoder_errors += 5
  assert receiver._take_recovery_request(10.1) is None
  assert receiver._take_recovery_request(10.49) is None
  sent.append(receiver._take_recovery_request(10.5))
  assert receiver._take_recovery_request(11.0) is None
  assert [payload["decoder_errors"] for payload in sent] == [1, 6]
  assert all(payload["recovery_reason"] == "decoder_error" for payload in sent)


def test_silent_video_stall_requests_idr_before_the_periodic_fps_report(monkeypatch, fake_aiortc, caplog):
  monkeypatch.setattr(h264_receiver, "FEEDBACK_INTERVAL", 30.0)
  c4_sock, pi_sock = socket.socketpair()
  c4_sock.settimeout(1.0)
  receiver = h264_receiver.H264Receiver(pi_sock, HELLO_DATA)
  try:
    with caplog.at_level(logging.INFO, logger="cluster_receiver"):
      receiver.start()
      assert read_control(c4_sock)[0] == OFFER
      c4_sock.sendall(pack_control(ANSWER, {"type": "answer", "sdp": "v=0\r\n"}))
      wait_until(lambda: receiver.received_frames == 1)
      kind, _, payload = read_control(c4_sock)
    assert kind == FEEDBACK and payload["keyframe_request"] is True
    assert payload["recovery_reason"] == "video_stale" and payload["decoder_errors"] == 0
    assert 500 <= payload["video_age_ms"] < 900
    assert "display_fps" not in payload
    assert "reason=video_stale" in caplog.text
  finally:
    receiver.close()
    c4_sock.close()
    pi_sock.close()


def test_video_recovery_uses_original_arrival_and_does_not_spam_startup_or_healthy_video():
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  assert receiver._take_recovery_request(10.49, started=10.0) is None
  startup = receiver._take_recovery_request(10.5, started=10.0)
  assert startup["recovery_reason"] == "video_startup" and startup["video_age_ms"] is None
  assert receiver._take_recovery_request(10.9, started=10.0) is None
  receiver.publish_rgb("fresh", received_at=10.9)
  assert receiver._take_recovery_request(11.0, started=10.0) is None
  receiver.screen_off = True
  stale = receiver._take_recovery_request(11.5, started=10.0)
  assert stale["recovery_reason"] == "video_stale" and stale["screen_off"] is True
  receiver.publish_rgb("recovered", received_at=11.6)
  assert receiver._take_recovery_request(12.0, started=10.0) is None


def test_fast_recovery_and_regular_feedback_share_one_cooldown(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  receiver.last_video_at = 0.1
  receiver.decoder_errors = 1
  assert receiver._take_recovery_request(0.9)["recovery_reason"] == "decoder_error"
  feedbacks = run_feedback_ticks(monkeypatch, receiver, 2, lambda *_args: None)
  assert not feedbacks[0]["keyframe_request"]
  assert feedbacks[1]["keyframe_request"] and feedbacks[1]["recovery_reason"] == "video_stale"


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
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(sleep=sleep, to_thread=asyncio.to_thread))
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


def test_rgb_arrival_metrics_use_publication_time_without_refreshing_decode_age_or_state(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  clock = [0.0]
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: clock[0]))
  assert h264_receiver._arrival_metrics(receiver._stats) == {
    "decoded_gap_max_ms": None, "rgb_gap_max_ms": None, "rgb_burst_frames": 0, "rgb_intervals": 0,
  }
  receiver.publish_state({"state_seq": 7}, received_at=-1.0)
  receiver.screen_off = True  # Telemetry observes reception independently of presentation.
  for published_at in (0.0, 0.025, 0.049, 0.5, 0.5):
    clock[0] = published_at
    receiver.publish_rgb("pixels", received_at=published_at - 0.1, convert_elapsed=0.1)
  metrics = h264_receiver._arrival_metrics(receiver._stats)
  assert metrics.pop("decoded_gap_max_ms") is None
  assert metrics == pytest.approx({"rgb_gap_max_ms": 451, "rgb_burst_frames": 2, "rgb_intervals": 4})
  assert receiver.received_frames == 5 and receiver.dropped_frames == 4
  assert receiver.last_video_at == pytest.approx(0.4) and receiver.last_state_at == -1.0
  assert receiver.take_updates(timeout=0) == (("pixels", 0.4), ({"state_seq": 7}, -1.0))
  # Resetting a feedback window does not reset the inter-publication clock.
  receiver._stats = receiver._empty_stats()
  clock[0] = 0.56
  receiver.publish_rgb("next", received_at=0.46)
  assert h264_receiver._arrival_metrics(receiver._stats)["rgb_gap_max_ms"] == pytest.approx(60)
  assert receiver._stats["rgb_intervals"] == 1 and receiver._stats["rgb_burst_frames"] == 0
  receiver.closing = True
  receiver.publish_rgb("ignored", received_at=0.56)
  assert receiver.received_frames == 6 and receiver._stats["rgb_intervals"] == 1


def test_decoded_arrival_gap_counts_every_reference_picture_before_latest_rgb_conversion(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  clock, arrivals = [0.0], iter((0.0, 0.01, 0.41, 0.42))
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: clock[0]))

  async def recv():
    try:
      clock[0] = next(arrivals)
    except StopIteration:
      await asyncio.Event().wait()
    return SimpleNamespace(width=2, height=2, to_ndarray=lambda **_kwargs: "RGB")

  async def run():
    receiver.stop_event = asyncio.Event()
    task = asyncio.create_task(receiver._consume_video(SimpleNamespace(recv=recv)))
    try:
      for _ in range(200):
        if receiver.received_frames:
          break
        await asyncio.sleep(0.001)
      assert receiver.decoded_frames == 4 and receiver.skipped_rgb == 3
      assert receiver.received_frames == 1 and receiver.latest_rgb == ("RGB", 0.42)
      metrics = h264_receiver._arrival_metrics(receiver._stats)
      assert metrics["decoded_gap_max_ms"] == pytest.approx(400)
      assert metrics["rgb_gap_max_ms"] is None and metrics["rgb_intervals"] == 0
    finally:
      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task

  asyncio.run(run())


def test_arrival_feedback_and_ten_second_logs_preserve_maximum_sums_and_unknown_windows(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, _now):
    if tick == 1:
      receiver._stats.update(decoded_intervals=2, decoded_gap=0.4, rgb_intervals=2, rgb_gap=0.3, rgb_burst_frames=1)
    elif tick == 2:
      receiver._stats.update(decoded_intervals=1, decoded_gap=0.05, rgb_intervals=1, rgb_gap=0.01, rgb_burst_frames=1)
    elif tick == 11:
      receiver._stats.update(decoded_intervals=1, decoded_gap=0.05, rgb_intervals=1, rgb_gap=0.05)

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    feedbacks = run_feedback_ticks(monkeypatch, receiver, 30, update)
  assert feedbacks[0]["decoded_gap_max_ms"] == 400 and feedbacks[0]["rgb_gap_max_ms"] == 300
  assert feedbacks[1]["rgb_burst_frames"] == 1 and feedbacks[1]["rgb_intervals"] == 1
  assert feedbacks[2]["decoded_gap_max_ms"] is None and feedbacks[2]["rgb_gap_max_ms"] is None
  messages = [record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage()]
  assert len(messages) == 3
  assert "decoded_gap_max_ms=400.00 | rgb_gap_max_ms=300.00 | rgb_burst_frames=2.00 | rgb_intervals=3.00" in messages[0]
  assert "decoded_gap_max_ms=50.00 | rgb_gap_max_ms=50.00 | rgb_burst_frames=0.00 | rgb_intervals=1.00" in messages[1]
  assert "decoded_gap_max_ms=none | rgb_gap_max_ms=none | rgb_burst_frames=0.00 | rgb_intervals=0.00" in messages[2]


def test_loop_feedback_and_ten_second_logs_preserve_maximum_and_unknown_windows(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, _now):
    if tick in (1, 2, 11):
      receiver._stats.update(loop_samples=10, loop_lag=0.25 if tick == 1 else 0.02, loop_wall=1.0, loop_cpu=0.03)

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    feedbacks = run_feedback_ticks(monkeypatch, receiver, 20, update)
  assert feedbacks[0]["loop_lag_max_ms"] == 250
  assert feedbacks[1]["loop_lag_max_ms"] == 20
  assert feedbacks[2]["loop_samples"] == 0 and feedbacks[2]["loop_lag_max_ms"] is None
  assert feedbacks[2]["loop_wall_ms"] is None and feedbacks[2]["loop_cpu_ms"] is None
  messages = [record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage()]
  assert "loop_samples=20.00 | loop_lag_max_ms=250.00 | loop_wall_ms=2000.00 | loop_cpu_ms=60.00" in messages[0]
  assert "loop_samples=10.00 | loop_lag_max_ms=20.00 | loop_wall_ms=1000.00 | loop_cpu_ms=30.00" in messages[1]


def test_udp_feedback_and_ten_second_logs_keep_window_delta_peak_and_unknowns(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  snapshots = []
  for tick in range(1, 41):
    snapshot = {"udp_sockets": 1, "udp_drops_total": 100 + tick,
                "udp_drops_delta": None if tick in (1, 21, 22) else 2 if 11 <= tick <= 20 else 0,
                "udp_rmem_bytes": 500 if tick == 11 else 2, "udp_rcvbuf_bytes": 2097152}
    if tick == 21:
      snapshot.update(udp_drops_total=None, udp_rmem_bytes=None, udp_rcvbuf_bytes=None)
    snapshots.append(snapshot)
  sampler = SimpleNamespace(sample=Mock(side_effect=snapshots))
  receiver.loop = SimpleNamespace(udp_sampler=sampler)
  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    feedbacks = run_feedback_ticks(monkeypatch, receiver, 40, lambda *_args: None)
  assert all(all(payload[name] == snapshot[name] for name in snapshot) for payload, snapshot in zip(feedbacks, snapshots, strict=True))
  assert all(not payload["keyframe_request"] for payload in feedbacks)
  assert sampler.sample.call_count == 40
  messages = [record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage()]
  assert len(messages) == 4
  assert "udp_drops_delta=none" in messages[0] and "udp_drops_delta=20" in messages[1]
  assert "udp_drops_delta=none" in messages[2] and "udp_drops_delta=0" in messages[3]
  assert "udp_rmem_bytes=2" in messages[1] and "udp_rmem_peak_bytes=500" in messages[1]
  assert "udp_drops_total=120" in messages[1] and "udp_rcvbuf_bytes=2097152" in messages[1]


def test_slow_resource_sensor_reads_leave_the_rtc_loop_available(monkeypatch):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)
  release = threading.Event()
  notifier = []
  sample_threads, samples_released = [], []
  now, sleeps = [0.0], [0]

  def sample():
    sample_threads.append(threading.get_ident())
    loop, entered = notifier[0]
    loop.call_soon_threadsafe(entered.set)
    samples_released.append(release.wait(timeout=1.0))
    return {}

  async def tick(_interval):
    sleeps[0] += 1
    if sleeps[0] > 1:
      raise asyncio.CancelledError
    now[0] += 10.0
    await asyncio.sleep(0)

  async def run():
    owner = threading.get_ident()
    entered = asyncio.Event()
    notifier.append((asyncio.get_running_loop(), entered))
    task = asyncio.create_task(receiver._feedback())
    try:
      await asyncio.wait_for(entered.wait(), timeout=2.0)
      # A decoded-video callback can still execute while the sysfs read is
      # pending; a synchronous sample would have already timed out above.
      assert not task.done() and not samples_released
      receiver.publish_rgb("live callback", received_at=now[0])
      assert receiver.received_frames == 1
      assert len(sample_threads) == 1 and sample_threads[0] != owner
      release.set()
      with pytest.raises(asyncio.CancelledError):
        await task
    finally:
      release.set()
      if not task.done():
        task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  monkeypatch.setattr(receiver._resources, "sample", sample)
  monkeypatch.setattr(receiver, "_send_control", AsyncMock())
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: now[0]))
  monkeypatch.setattr(h264_receiver, "asyncio", SimpleNamespace(sleep=tick, to_thread=asyncio.to_thread))
  asyncio.run(run())
  assert samples_released == [True] and len(sample_threads) == 1


def test_receiver_stage_timings_average_only_present_valid_samples(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, _now):
    receiver.record_display(0.0, 0.02, 0.03,
                            scene_timings={"path": 0.01, "path_wait": 0.003, "path_labels": 0.002,
                                           "hud_cpu": 0.004, "hud": float("nan"), "prepare": -1},
                            display_timings={"rgb_surface": 0.002, "upload": True})
    if tick % 2 == 0:
      receiver.record_display(0.0, 0.02, 0.03, scene_timings={"path": 0.02}, display_timings={})

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    run_feedback_ticks(monkeypatch, receiver, 10, update)
  message = next(record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage())
  assert "scene_path_avg=13.3ms" in message and "hdmi_rgb_surface_avg=2.0ms" in message
  assert "scene_path_wait_avg=3.0ms" in message
  assert "scene_path_labels_avg=2.0ms" in message and "scene_hud_cpu_avg=4.0ms" in message
  assert "scene_hud_avg=" not in message and "scene_prepare_avg=" not in message and "hdmi_upload_avg=" not in message


def test_path_visibility_diagnostics_count_presented_async_frames_and_reset_each_window(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO_DATA)

  def update(tick, _now):
    reason = "path_visible" if tick <= 5 else "path_pending" if tick <= 7 else "path_expired"
    receiver.record_display(0, 0.01, 0.02, scene_timings={"path_async": 1, "path_expected": 1, reason: 1,
                                                        "path_reset": int(tick == 1)})
    receiver.record_display(0, 0.01, 0.02, presented=False,
                            scene_timings={"path_async": 1, "path_expired": 1})
    receiver.record_display(0, 0.01, 0.02, scene_timings={"path_async": 0, "path_expired": 1})

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    run_feedback_ticks(monkeypatch, receiver, 20, update)
  messages = [record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage()]
  assert len(messages) == 2
  assert "path_async_frames=10" in messages[0] and "path_expected=10" in messages[0]
  assert "path_visible=5" in messages[0] and "path_pending=2" in messages[0] and "path_expired=3" in messages[0]
  assert "path_reset=1" in messages[0]
  assert "path_async_frames=10" in messages[1] and "path_visible=0" in messages[1]
  assert "path_expired=10" in messages[1] and "path_reset=0" in messages[1]


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
