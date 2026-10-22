import asyncio
from copy import deepcopy
from functools import wraps
from fractions import Fraction
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display import network_h264 as network
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_protocol as wire


def asyncio_test(function):
  @wraps(function)
  def run(*args, **kwargs):
    return asyncio.run(function(*args, **kwargs))
  return run


def config():
  return SimpleNamespace(network_bind_host="127.0.0.1", network_port=0, width=1920, height=480, fps=20,
                         is_metric=True, rotate_180=False, camera_contrast=1.08)


def models():
  result = Mock()
  path = {"path_x": [1.0, 2.0], "path_y": [0.0, 0.0], "path_z": [0.0, 0.0],
          "calib_transform": np.eye(3, dtype=np.float32), "left_lane_x": [1234]}
  result.get_render_data.return_value = True, {"v_ego": 12.5, "enabled": True, "tpms": [34.0] * 4}, path
  result.get_camera_source_size.return_value = [1344, 760]
  return result


class Stream:
  def __init__(self):
    self.stopped = False
    self.disconnected = asyncio.Event()

  async def start(self):
    return SimpleNamespace(sdp="v=0\r\nanswer", type="answer")

  async def wait_for_connection(self):
    pass

  async def wait_for_disconnection(self):
    await self.disconnected.wait()

  async def stop(self):
    self.stopped = True
    self.disconnected.set()


def factory():
  stream = Stream()
  track = SimpleNamespace(video_frame_id=10, video_timestamp_eof=None, video_frames=20, video_bytes=1024,
                          last_video_at=None, resyncs=0, stop=Mock())
  def stop_track():
    assert stream.stopped, "Native senders must stop before their producer is released"
  track.stop.side_effect = stop_track
  return stream, track


def test_scene_snapshot_keeps_geometry_but_excludes_unused_duplicate_lines():
  data = models()
  result = network.scene_snapshot(config(), data, {"model_age_ms": 10.0})
  assert result["path"]["calib_transform"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
  assert "left_lane_x" not in result["path"]
  assert result["model_valid"] and result["hud"]["v_ego"] == 12.5
  assert not network.scene_snapshot(config(), data, {}, degraded=True)["model_valid"]
  data.get_camera_source_size.return_value = None
  assert not network.scene_snapshot(config(), data, {})["model_valid"]


def test_scene_rounding_reduces_payload_without_visible_projection_error():
  source = models()
  xs = np.linspace(1.0, 120.0, 33)
  ys, zs = np.sin(xs / 17.0), np.cos(xs / 43.0) * 0.1
  matrix = np.array([[672.0135914, -909.8192581, 0.0014511], [379.1273948, -0.3193511, -909.1235519],
                     [0.99999791, 0.00020914, 0.00130571]])
  path = {"path_x": xs.tolist(), "path_y": ys.tolist(), "path_z": zs.tolist(), "calib_transform": matrix,
          "lane_lines": [[xs.tolist(), (ys + offset).tolist(), zs.tolist()] for offset in (-4.8, -1.6, 1.6, 4.8)]}
  hud = {"v_ego": 23.764358121, "steering_angle": -11.3896473, "enabled": True, "tpms": [34.0123412] * 4}
  source.get_render_data.return_value = True, hud, path
  compact = network.scene_snapshot(config(), source, {})
  full = deepcopy(compact)
  full.update(hud=hud, path={**path, "calib_transform": matrix.tolist()})
  assert len(wire.pack_control(wire.STATE, compact)) < len(wire.pack_control(wire.STATE, full)) * 0.6
  before = matrix @ np.stack((xs, ys, zs))
  after = np.array(compact["path"]["calib_transform"]) @ np.array([compact["path"][key] for key in ("path_x", "path_y", "path_z")])
  np.testing.assert_allclose(before[:2] / before[2], after[:2] / after[2], atol=0.75, rtol=0)
  assert abs(compact["hud"]["v_ego"] - hud["v_ego"]) < 0.001
  assert path["path_y"] == ys.tolist()  # Snapshot packing never mutates model inputs.


def test_plain_rounding_keeps_mixed_and_numpy_values_and_boolean_types():
  source = [1.2345678, True, 3, None, np.float32(4.567891), np.array([1.2345678, 9.8765432])]
  assert network._plain(source) == [1.235, True, 3, None, 4.568, [1.235, 9.877]]
  assert type(network._plain(source)[1]) is bool
  assert type(network._plain(source)[2]) is int
  assert network._plain((1.2345678, 9.8765432), precision=6) == [1.234568, 9.876543]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_fast_float_list_rounding_cannot_bypass_wire_numeric_checks(bad):
  source = models()
  source.get_render_data.return_value[2]["path_y"] = [bad, 0.0]
  with pytest.raises(ValueError):
    wire.pack_scene(network.scene_snapshot(config(), source, {}))


@asyncio_test
async def test_receiver_recovery_requests_are_rate_limited_and_decoder_errors_are_logged(monkeypatch):
  messages = []
  monkeypatch.setattr(network, "flog", messages.append)
  monkeypatch.setattr(network, "time", SimpleNamespace(monotonic=Mock(side_effect=[10.0, 10.1, 10.49, 10.5])))
  monkeypatch.setattr(network, "read_control", AsyncMock(side_effect=[
    (wire.FEEDBACK, 0, {"keyframe_request": True, "decoder_errors": 2, "camera_display_fps": 14.75}),
    (wire.FEEDBACK, 1, {"keyframe_request": True}), (wire.FEEDBACK, 2, {"keyframe_request": True}),
    (wire.FEEDBACK, 3, {"keyframe_request": True}), EOFError(),
  ]))
  track = SimpleNamespace(request_recovery=Mock())
  server = network.H264NetworkServer(config(), models())
  with pytest.raises(EOFError):
    await server._feedback(None, track)
  assert track.request_recovery.call_count == 2
  assert sum("[CLUSTER_H264_RECOVERY]" in message for message in messages) == 2
  assert any("decoder_errors=2.00" in message for message in messages)
  assert any("camera_display_fps=14.75" in message for message in messages)


def test_camera_age_uses_the_native_boot_clock_including_suspend(monkeypatch):
  get_boot = Mock(return_value=12_000_000_000)
  monkeypatch.setattr(network, "time", SimpleNamespace(CLOCK_BOOTTIME=7, clock_gettime_ns=get_boot, monotonic_ns=lambda: 9))
  assert network.camera_clock_ns() == 12_000_000_000
  get_boot.assert_called_once_with(7)
  monkeypatch.setattr(network, "time", SimpleNamespace(monotonic_ns=lambda: 9))
  assert network.camera_clock_ns() == 9


@asyncio_test
async def test_real_control_socket_negotiates_video_separately_and_streams_state_without_frame_acks(monkeypatch):
  messages, statuses = [], []
  monkeypatch.setattr(network, "flog", messages.append)
  monkeypatch.setattr(network, "write_network_status", statuses.append)
  stream, track = factory()
  server = network.H264NetworkServer(config(), models(), lambda _sdp: (stream, track))
  await server.start()
  reader, writer = await asyncio.open_connection(*server.server.sockets[0].getsockname())
  try:
    kind, _, hello = await network.read_control(reader)
    assert kind == wire.HELLO and hello["codec"] == "h264" and hello["transport"] == "webrtc"
    await network.write_control(writer, wire.OFFER, {"type": "offer", "sdp": "v=0\r\noffer"})
    assert (await network.read_control(reader))[0] == wire.ANSWER
    for _ in range(3):
      kind, sequence, state = await network.read_control(reader)
      assert kind == wire.STATE and wire.validate_scene(state)
      assert state["state_seq"] == sequence
    server.status_updated = time.monotonic() - 2
    await network.read_control(reader)
    assert statuses.count("127.0.0.1") >= 2  # Connection status expires after 10s unless renewed.
    # A feedback packet reports telemetry only; state sends did not wait for it.
    await network.write_control(writer, wire.FEEDBACK, {"screen_off": True, "display_fps": 19.5})
    for _ in range(20):
      if server.screen_off:
        break
      await asyncio.sleep(0.005)
    assert server.screen_off
    assert any("compose=pi" in message for message in messages)
  finally:
    writer.close()
    await writer.wait_closed()
    await server.close()
  assert stream.stopped and track.stop.called and statuses[-1] is None
  assert not server.clients and server.active is None


@asyncio_test
async def test_short_state_pause_keeps_video_session_and_resumes_with_latest_models(monkeypatch):
  monkeypatch.setattr(network, "write_network_status", lambda _ip: None)
  messages = []
  monkeypatch.setattr(network, "flog", messages.append)
  stream, track = factory()
  source = models()
  server = network.H264NetworkServer(config(), source, lambda _sdp: (stream, track))
  resumed = asyncio.Event()
  states = server._states

  class PausedWriter:
    def __init__(self, writer):
      self.writer, self.transport = writer, writer.transport
      self.first = True

    def write(self, data):
      self.writer.write(data)

    async def drain(self):
      if self.first:
        self.first = False
        await resumed.wait()
      await self.writer.drain()

  async def paused_states(writer, camera, rtc_stream):
    await states(PausedWriter(writer), camera, rtc_stream)

  monkeypatch.setattr(server, "_states", paused_states)
  await server.start()
  reader, writer = await asyncio.open_connection(*server.server.sockets[0].getsockname())
  try:
    assert (await network.read_control(reader))[0] == wire.HELLO
    await network.write_control(writer, wire.OFFER, {"type": "offer", "sdp": "v=0\r\noffer"})
    assert (await network.read_control(reader))[0] == wire.ANSWER
    assert (await network.read_control(reader))[0] == wire.STATE
    await asyncio.sleep(0.35)  # Exceeds the former 250 ms reconnect threshold.
    assert server.active is not None and not stream.stopped
    assert source.get_render_data.call_count == 1  # No pending snapshots accumulate.
    valid, hud, path = source.get_render_data.return_value
    source.get_render_data.return_value = valid, {**hud, "v_ego": 31.0}, path
    resumed.set()
    kind, sequence, snapshot = await network.read_control(reader, budget_s=1)
    assert kind == wire.STATE and sequence == 1 and snapshot["hud"]["v_ego"] == 31.0
    assert sum("[CLUSTER_H264_CONNECTED]" in line for line in messages) == 1
    assert any("STATE_BACKPRESSURE" in line for line in messages)
    assert any("STATE_RECOVERED" in line for line in messages)
    assert not stream.stopped
  finally:
    writer.close()
    await writer.wait_closed()
    await server.close()


@pytest.mark.parametrize("cancel", [False, True])
@asyncio_test
async def test_state_write_stall_has_a_hard_deadline_and_cleans_pending_drain(monkeypatch, cancel):
  monkeypatch.setattr(network, "STATE_DRAIN_WARN_S", 0.01)
  monkeypatch.setattr(network, "STATE_DRAIN_TIMEOUT_S", 0.05)
  started, finished = asyncio.Event(), asyncio.Event()

  async def stalled():
    started.set()
    try:
      await asyncio.Event().wait()
    finally:
      finished.set()

  writer = SimpleNamespace(drain=stalled, transport=SimpleNamespace(get_write_buffer_size=lambda: 4096))
  task = asyncio.create_task(network.drain_state(writer))
  await started.wait()
  if cancel:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
  else:
    with pytest.raises(TimeoutError, match="Scene TCP write stalled for 0.05 seconds"):
      await asyncio.wait_for(task, 0.5)
  assert finished.is_set()


@asyncio_test
async def test_failed_connection_aborts_a_transport_that_cannot_finish_closing(monkeypatch):
  monkeypatch.setattr(network, "write_network_status", lambda _ip: None)
  monkeypatch.setattr(network, "read_control", AsyncMock(return_value=(wire.OFFER, 0, {"type": "offer", "sdp": "v=0\r\noffer"})))
  stream, track = factory()
  server = network.H264NetworkServer(config(), models(), lambda _sdp: (stream, track))

  async def stalled():
    await asyncio.Event().wait()

  monkeypatch.setattr(server, "_states", AsyncMock(side_effect=ConnectionError("control socket failed")))
  monkeypatch.setattr(server, "_feedback", lambda *_args: stalled())
  transport = Mock()
  writer = SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock(), transport=transport, wait_closed=stalled,
                           get_extra_info=lambda key: ("127.0.0.1", 1) if key == "peername" else None)
  await asyncio.wait_for(server._client(None, writer), 2)
  transport.abort.assert_called_once()
  assert stream.stopped and track.stop.called and server.active is None and not server.clients


@asyncio_test
async def test_second_peer_cannot_replace_active_cluster_and_shutdown_cancels_negotiation(monkeypatch):
  monkeypatch.setattr(network, "write_network_status", lambda _ip: None)
  server = network.H264NetworkServer(config(), models())
  await server.start()
  address = server.server.sockets[0].getsockname()
  r1, w1 = await asyncio.open_connection(*address)
  r2 = w2 = None
  try:
    assert (await network.read_control(r1))[0] == wire.HELLO
    owner = server.active
    r2, w2 = await asyncio.open_connection(*address)
    kind, _, error = await network.read_control(r2)
    assert kind == wire.ERROR and error["error"] == "h264_cluster_busy"
    assert server.active is owner
    await server.close()
    assert await r1.read() == b"" and not server.clients
  finally:
    w1.close()
    await w1.wait_closed()
    if w2 is not None:
      w2.close()
      await w2.wait_closed()
    await server.close()


@asyncio_test
async def test_missing_webrtc_is_explicit(monkeypatch):
  monkeypatch.setattr(network, "write_network_status", lambda _ip: None)

  def unavailable(_offer):
    raise ImportError("optional WebRTC backend unavailable")

  server = network.H264NetworkServer(config(), models(), unavailable)
  await server.start()
  reader, writer = await asyncio.open_connection(*server.server.sockets[0].getsockname())
  try:
    assert (await network.read_control(reader))[0] == wire.HELLO
    await network.write_control(writer, wire.OFFER, {"type": "offer", "sdp": "v=0\r\noffer"})
    kind, _, error = await network.read_control(reader)
    assert kind == wire.ERROR and error["error"] == "ImportError"
    assert await reader.read() == b""
  finally:
    writer.close()
    await writer.wait_closed()
    await server.close()


@asyncio_test
async def test_receiver_dependency_error_is_preserved_in_c4_log(monkeypatch):
  messages = []
  monkeypatch.setattr(network, "flog", messages.append)
  monkeypatch.setattr(network, "write_network_status", lambda _ip: None)
  server = network.H264NetworkServer(config(), models())
  await server.start()
  reader, writer = await asyncio.open_connection(*server.server.sockets[0].getsockname())
  try:
    assert (await network.read_control(reader))[0] == wire.HELLO
    await network.write_control(writer, wire.ERROR, {"error": "ImportError", "detail": "aiortc unavailable; run pi_update.sh"})
    kind, _, error = await network.read_control(reader)
    assert kind == wire.ERROR and "aiortc unavailable" in error["detail"]
    assert any("Pi H.264 unavailable: ImportError" in message and "pi_update.sh" in message for message in messages)
  finally:
    writer.close()
    await writer.wait_closed()
    await server.close()


@pytest.fixture
def camera_backend(monkeypatch):
  socket_options, requests = [], []
  packets = []

  class Packet(bytes):
    pts = 0

  class BaseTrack:
    camera_to_sock_mapping = {"road": "livestreamNarrowRoadEncodeData"}

    def __init__(self, camera_type):
      self._sock = self._make_sock(camera_type)
      self.params = SimpleNamespace(put=Mock())
      self._seen_keyframe = False

    async def recv(self):
      msg = packets.pop(0)
      if not self._seen_keyframe and msg.frame.idx.flags & 8:
        self._seen_keyframe = True
        self.params.put("LivestreamRequestKeyframe", False, block=False)
      return Packet(self._build_frame_data(msg))

    def request_keyframe(self):
      requests.append(threading.get_ident())

    def stop(self):
      pass

  class Stream:
    def __init__(self, _offer, track):
      self.track = track

  monkeypatch.setitem(sys.modules, "openpilot.selfdrive.addon.cluster.hdmi_display.network_h264_stream",
                      SimpleNamespace(ClusterH264Stream=Stream))
  monkeypatch.setitem(sys.modules, "openpilot.system.webrtc.device.video", SimpleNamespace(LiveStreamVideoStreamTrack=BaseTrack))
  monkeypatch.setitem(sys.modules, "openpilot.cereal", SimpleNamespace(messaging=SimpleNamespace(
    sub_sock=lambda *args, **kwargs: socket_options.append(kwargs),
  )))

  def packet(index, key=False, header=b"", old=False):
    data = SimpleNamespace(idx=SimpleNamespace(frameId=index, encodeId=index, flags=8 if key else 0,
                                               timestampEof=time.monotonic_ns() - (1_000_000_000 if old else 0)),
                           header=header, data=b"encoded")
    return SimpleNamespace(which=lambda: "frame", frame=data)

  return SimpleNamespace(packets=packets, packet=packet, requests=requests, socket_options=socket_options)


@asyncio_test
async def test_native_camera_waits_for_idr_and_resynchronizes_after_encoded_reference_gap(camera_backend):
  packets, packet = camera_backend.packets, camera_backend.packet
  _stream, track = network.create_camera_stream("v=0")
  packets.extend([packet(10), packet(11, key=True, header=b"sps-pps")])
  assert bytes(await track.recv()) == b"sps-ppsencoded"
  assert track.video_frame_id == 11 and track.video_frames == 1
  assert camera_backend.socket_options == [{"conflate": False}] and camera_backend.requests
  packets.extend([packet(13), packet(14, key=True)])
  assert bytes(await track.recv()) == b"sps-ppsencoded"
  assert track.resyncs == 1 and track.video_frame_id == 14
  packets.extend([packet(15, old=True), packet(16, key=True)])
  assert bytes(await track.recv()) == b"sps-ppsencoded"
  assert track.resyncs == 2 and track.video_frames == 3
  assert not track._keyframe_requested
  track.params.put.assert_called_with("LivestreamRequestKeyframe", False, block=False)
  track.request_recovery()
  packets.extend([packet(17), packet(18, key=True)])
  # Requesting an IDR after a receiver decode error must not discard the
  # healthy C4 source's P frames and manufacture an additional freeze.
  assert bytes(await track.recv()) == b"encoded"
  assert track.video_frame_id == 17 and track.resyncs == 3
  assert bytes(await track.recv()) == b"sps-ppsencoded"
  assert track.video_frame_id == 18 and track.resyncs == 3
  clear_count = track.params.put.call_count
  track.request_keyframe()  # Receiver PLI while the reference chain is healthy.
  packets.append(packet(19, key=True))
  await track.recv()
  assert not track._keyframe_requested
  assert track.params.put.call_count == clear_count + 1
  track.request_keyframe()
  track.stop()  # Disconnect before the requested IDR arrives.
  assert not track._keyframe_requested
  assert track.params.put.call_count == clear_count + 2
  track.params.put.assert_called_with("LivestreamRequestKeyframe", False, block=False)
  track.stop()
  assert track.params.put.call_count == clear_count + 2


@asyncio_test
async def test_native_pli_burst_and_loop_feedback_use_one_owner_thread_and_one_pending_write(camera_backend):
  owner = threading.get_ident()
  _stream, track = network.create_camera_stream("v=0")

  def native_pli_burst():
    for _ in range(100):
      track.request_keyframe()

  callbacks = [threading.Thread(target=native_pli_burst) for _ in range(4)]
  for callback in callbacks:
    callback.start()
  for callback in callbacks:
    callback.join(timeout=2)
    assert not callback.is_alive()
  assert not camera_backend.requests  # Native callbacks never enter Params.
  track.request_recovery()  # TCP feedback can arrive before the queued PLI.
  await asyncio.sleep(0)
  assert camera_backend.requests == [owner]
  assert track._keyframe_requested
  camera_backend.packets.append(camera_backend.packet(1, key=True, header=b"sps-pps"))
  await track.recv()
  assert not track._keyframe_requested
  track.params.put.assert_called_once_with("LivestreamRequestKeyframe", False, block=False)
  track.stop()


@asyncio_test
async def test_queued_or_late_native_pli_cannot_write_params_after_camera_stop(camera_backend):
  _stream, track = network.create_camera_stream("v=0")
  callback = threading.Thread(target=track.request_keyframe)
  callback.start()
  callback.join(timeout=2)
  assert not callback.is_alive()
  track.stop()
  await asyncio.sleep(0)
  track.request_keyframe()
  late_callback = threading.Thread(target=track.request_keyframe)
  late_callback.start()
  late_callback.join(timeout=2)
  assert not late_callback.is_alive()
  await asyncio.sleep(0)
  assert not camera_backend.requests
  track.params.put.assert_not_called()


@asyncio_test
async def test_recovery_feedback_cannot_postpone_an_unfulfilled_keyframe_retry(camera_backend, monkeypatch):
  clock = [10.0]
  monkeypatch.setattr(network, "time", SimpleNamespace(monotonic=lambda: clock[0]))
  _stream, track = network.create_camera_stream("v=0")
  for now in (10.0, 10.5, 11.0):
    clock[0] = now
    track.request_recovery()
  assert len(camera_backend.requests) == 2
  track.stop()


@asyncio_test
async def test_initial_idr_and_later_requested_idr_each_acknowledge_once(camera_backend):
  _stream, track = network.create_camera_stream("v=0")
  camera_backend.packets.append(camera_backend.packet(1, key=True, header=b"sps-pps"))
  await track.recv()
  assert not camera_backend.requests  # An available IDR needs no new request.
  assert track.params.put.call_count == 1
  track.request_keyframe()
  camera_backend.packets.append(camera_backend.packet(2, key=True))
  await track.recv()
  assert track.params.put.call_count == 2
  assert not track._keyframe_requested
  track.stop()


@asyncio_test
async def test_real_webrtc_h264_reaches_pi_decoder_with_independent_scene_control(monkeypatch):
  rtc = pytest.importorskip("aiortc")
  av = pytest.importorskip("av")
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.control_socket import recv_exact
  from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.h264_receiver import H264Receiver

  monkeypatch.setattr(network, "write_network_status", lambda _address: None)
  backends = []

  class Video(rtc.VideoStreamTrack):
    def __init__(self):
      super().__init__()
      self.video_frame_id = self.video_timestamp_eof = None
      self.video_frames = self.video_bytes = self.resyncs = 0
      self.last_video_at = None

    async def recv(self):
      await asyncio.sleep(0.05)
      pixels = np.zeros((96, 160, 3), dtype=np.uint8)
      pixels[:, :, 1] = 160
      frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
      frame.pts, frame.time_base = self.video_frames * 4500, Fraction(1, 90000)
      self.video_frames += 1
      self.video_frame_id, self.video_timestamp_eof = self.video_frames, time.monotonic_ns()
      self.last_video_at = time.monotonic()
      return frame

  class Backend:
    def __init__(self, offer):
      self.offer, self.track = offer, Video()
      self.peer = rtc.RTCPeerConnection(rtc.RTCConfiguration(iceServers=[]))
      self.connected, self.disconnected = asyncio.Event(), asyncio.Event()

      @self.peer.on("connectionstatechange")
      async def state_changed():
        if self.peer.connectionState == "connected":
          self.connected.set()
        if self.peer.connectionState in ("closed", "failed"):
          self.disconnected.set()

    async def start(self):
      await self.peer.setRemoteDescription(rtc.RTCSessionDescription(sdp=self.offer, type="offer"))
      self.peer.addTrack(self.track)
      for transceiver in self.peer.getTransceivers():
        codecs = [codec for codec in rtc.RTCRtpSender.getCapabilities("video").codecs if codec.mimeType.lower() == "video/h264"]
        transceiver.setCodecPreferences(codecs)
      await self.peer.setLocalDescription(await self.peer.createAnswer())
      return self.peer.localDescription

    async def wait_for_connection(self):
      await self.connected.wait()

    async def wait_for_disconnection(self):
      await self.disconnected.wait()

    async def stop(self):
      await self.peer.close()

  def build(offer):
    backend = Backend(offer)
    backends.append(backend)
    return backend, backend.track

  server = network.H264NetworkServer(config(), models(), build)
  await server.start()
  address = server.server.sockets[0].getsockname()

  def connect():
    sock = socket.create_connection(address, timeout=3)
    kind, _, size = wire.unpack_control_header(recv_exact(sock, 16))
    assert kind == wire.HELLO
    return sock, wire.decode_payload(recv_exact(sock, size))

  sock, hello = await asyncio.to_thread(connect)
  receiver = H264Receiver(sock, hello)
  receiver.start()
  try:
    deadline = time.monotonic() + 12
    while receiver.received_frames < 4 and time.monotonic() < deadline:
      if receiver.error is not None:
        raise receiver.error
      await asyncio.sleep(0.02)
    assert receiver.received_frames >= 4 and receiver.error is None
    rgb, state = receiver.take_updates(timeout=0)
    assert rgb[0].shape == (96, 160, 3) and rgb[0][:, :, 1].mean() > 140
    assert state is not None and state[0]["hud"]["v_ego"] == 12.5
    assert backends[0].peer.connectionState == "connected"
  finally:
    await asyncio.to_thread(receiver.close)
    sock.close()
    await server.close()
  assert not receiver.thread.is_alive() and not server.clients
