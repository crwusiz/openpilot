"""Run the real WebRTC lifecycle with isolated native/transport dependencies."""
import ast
import asyncio
import contextlib
import json
import logging
import signal
import sys
import time
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from openpilot.system.webrtc.helpers import StreamRequestBody


class Timer:
  def __init__(self, delay, callback):
    self.delay, self.callback, self.cancelled = delay, callback, False

  def cancel(self):
    self.cancelled = True

  def fire(self):
    if not self.cancelled:
      self.callback()


class TimerLoop:
  def __init__(self):
    self.timers = []

  def call_later(self, delay, callback):
    timer = Timer(delay, callback)
    self.timers.append(timer)
    return timer


def load_lifecycle(loop, params):
  """Compile unchanged function bodies; mock only platform dependencies."""
  source = Path(__file__).parents[1] / 'webrtcd.py'
  selected = {
    '_ice_candidates', 'StreamSession', 'ServerState', 'cancel_teardown', 'schedule_teardown',
    '_json_response', 'handle_get_stream', 'on_shutdown', 'webrtcd_thread',
  }
  tree = ast.parse(source.read_text(encoding='utf-8'))
  nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in selected]
  cloudlog = Mock()
  cloudlog.ctx.side_effect = lambda **_kwargs: contextlib.nullcontext()
  namespace = {
    'asyncio': SimpleNamespace(Lock=asyncio.Lock, TimerHandle=asyncio.TimerHandle, Task=asyncio.Task,
                               wait_for=asyncio.wait_for, get_running_loop=lambda: loop,
                               new_event_loop=Mock(return_value=loop), set_event_loop=Mock()),
    'Any': Any, 'json': json, 'logging': logging, 'time': time, 'uuid': uuid, 'signal': signal,
    'StreamRequestBody': StreamRequestBody, 'Params': Mock(return_value=params), 'cloudlog': cloudlog,
    'DynamicPubMaster': Mock(), 'LivestreamBitrateController': Mock(),
    'CerealOutgoingMessageProxy': Mock(), 'CerealIncomingMessageProxy': Mock(),
    'prewarm_stream_session_imports': Mock(), 'WebrtcdHTTPServer': Mock(), 'WebrtcdHandler': Mock(),
    'threading': Mock(),
  }
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
  return SimpleNamespace(**namespace)


class TestLanMediaBinding(unittest.TestCase):
  def test_actual_session_constructor_leaves_interface_selection_to_ice(self):
    api = load_lifecycle(TimerLoop(), Mock())
    video_module = ModuleType('openpilot.system.webrtc.device.video')
    video_module.LiveStreamVideoStreamTrack = Mock()
    builder_module = ModuleType('teleoprtc.builder')
    builder_module.WebRTCAnswerBuilder = Mock()
    with patch.dict(sys.modules, {video_module.__name__: video_module, builder_module.__name__: builder_module}):
      session = api.StreamSession(StreamRequestBody('wifi-peer-offer', ['road'], True))
    # A cellular default route must not restrict Wi-Fi host candidates.
    builder_module.WebRTCAnswerBuilder.assert_called_once_with('wifi-peer-offer')
    builder = builder_module.WebRTCAnswerBuilder.return_value
    builder.add_video_stream.assert_called_once_with('road', video_module.LiveStreamVideoStreamTrack.return_value)
    self.assertIs(session.stream, builder.stream.return_value)


class TestWebrtcIdleLifecycle(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.timer_loop, self.params = TimerLoop(), Mock()
    self.api = load_lifecycle(self.timer_loop, self.params)
    self.state = self.api.ServerState()
    self.body = json.dumps({'sdp': 'v=0\r\na=candidate:wifi', 'cameras': ['road'], 'enabled': True}).encode()
    self.session = Mock(identifier='new-session', enabled=True, run_task=None)
    self.session.get_answer = AsyncMock(return_value=SimpleNamespace(sdp='v=0\r\na=candidate:c4-wifi', type='answer'))
    self.session.stop = AsyncMock()
    self.session.start.side_effect = self.start_session
    self.factory = Mock(return_value=self.session)
    # Functions retain the namespace used by exec, not the returned wrapper.
    self.api.handle_get_stream.__globals__['StreamSession'] = self.factory

  def start_session(self):
    self.session.run_task = asyncio.get_running_loop().create_future()

  async def request(self, body=None):
    return await self.api.handle_get_stream(self.state, self.body if body is None else body, 'application/json')

  def fire_idle(self):
    self.assertIsNotNone(self.state.teardown)
    self.assertEqual(self.state.teardown.delay, 5.0)
    self.state.teardown.fire()

  def test_initial_unused_offroad_service_returns_livestream_parameter(self):
    # Execute the actual process initializer while replacing sockets/threads.
    loop = Mock()
    loop.call_soon.side_effect = lambda function, *args: loop.run_forever.configure_mock(side_effect=lambda: function(*args))
    loop.call_later.side_effect = self.timer_loop.call_later
    api = load_lifecycle(loop, self.params)
    api.webrtcd_thread('127.0.0.1', 5001)
    self.assertEqual(len(self.timer_loop.timers), 1)
    self.timer_loop.timers[0].fire()
    self.params.put_bool.assert_called_once_with('IsLiveStreaming', False)
    api.WebrtcdHTTPServer.assert_called_once_with(('127.0.0.1', 5001), api.WebrtcdHandler)

  async def test_successful_negotiation_cancels_idle_timer_and_preserves_session(self):
    self.api.schedule_teardown(self.state)
    initial_timer = self.state.teardown
    status, raw, _ = await self.request()
    initial_timer.fire()
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(raw)['type'], 'answer')
    self.assertTrue(initial_timer.cancelled)
    self.assertIsNone(self.state.teardown)
    self.assertIs(self.state.streams['new-session'], self.session)
    self.params.put_bool.assert_not_called()

  async def test_answer_error_and_timeout_cleanup_and_schedule_idle(self):
    for error in (RuntimeError('bad SDP'), TimeoutError()):
      with self.subTest(error=type(error).__name__):
        self.state = self.api.ServerState()
        self.session.get_answer.side_effect = error
        self.session.stop.reset_mock()
        with self.assertRaises(type(error)):
          await self.request()
        self.session.stop.assert_awaited_once()
        self.assertEqual(self.state.streams, {})
        self.fire_idle()
    self.assertEqual(self.params.put_bool.call_count, 2)

  async def test_constructor_failure_also_schedules_idle(self):
    self.factory.side_effect = RuntimeError('native initialization failed')
    with self.assertRaisesRegex(RuntimeError, 'native initialization failed'):
      await self.request()
    self.assertEqual(self.state.streams, {})
    self.fire_idle()
    self.params.put_bool.assert_called_once_with('IsLiveStreaming', False)

  async def test_cleanup_failure_does_not_leave_stale_session_in_state(self):
    self.session.get_answer.side_effect = RuntimeError('bad SDP')
    self.session.stop.side_effect = RuntimeError('cleanup failed')
    with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
      await self.request()
    self.assertEqual(self.state.streams, {})
    self.fire_idle()

  async def test_cancelled_negotiation_cleans_up_and_returns_to_idle(self):
    started, pending = asyncio.Event(), asyncio.Event()

    async def answer():
      started.set()
      await pending.wait()

    self.session.get_answer.side_effect = answer
    task = asyncio.create_task(self.request())
    await started.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.session.stop.assert_awaited_once()
    self.assertEqual(self.state.streams, {})
    self.fire_idle()

  async def test_old_completion_timer_cannot_stop_encoder_during_replacement_cleanup(self):
    old = Mock(identifier='old-session', enabled=True, run_task=Mock(done=Mock(return_value=False)))

    async def stop_old():
      # The old run_task callback can remove it while stop() awaits cleanup.
      self.state.streams.pop(old.identifier, None)
      self.api.schedule_teardown(self.state)
      timer = self.state.teardown
      timer.fire()
      self.params.put_bool.assert_not_called()
      self.assertIsNot(self.state.teardown, timer)

    old.stop = AsyncMock(side_effect=stop_old)
    self.state.streams[old.identifier] = old
    await self.request()
    self.assertIsNone(self.state.teardown)
    self.assertEqual(list(self.state.streams), ['new-session'])
    self.params.put_bool.assert_not_called()

  async def test_active_session_is_preserved_when_disabled_prewarm_is_rejected(self):
    old = Mock(identifier='active', enabled=True, run_task=Mock(done=Mock(return_value=False)))
    old.stop = AsyncMock()
    self.state.streams['active'] = old
    disabled = json.dumps({'sdp': 'v=0', 'cameras': ['road'], 'enabled': False}).encode()
    status, raw, _ = await self.request(disabled)
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(raw)['error'], 'busy')
    self.factory.assert_not_called()
    old.stop.assert_not_awaited()
    self.assertIs(self.state.streams['active'], old)

  async def test_session_end_releases_livestream_after_grace_period(self):
    await self.request()
    self.session.run_task.set_result(None)
    await asyncio.sleep(0)
    self.assertEqual(self.state.streams, {})
    self.fire_idle()
    self.params.put_bool.assert_called_once_with('IsLiveStreaming', False)

  async def test_previous_session_cleanup_failure_leaves_idle_state(self):
    old = Mock(identifier='old-session', enabled=True, run_task=None, stop=AsyncMock(side_effect=RuntimeError('cleanup failed')))
    self.state.streams['old-session'] = old
    with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
      await self.request()
    self.assertEqual(self.state.streams, {})
    self.factory.assert_not_called()
    self.fire_idle()

  async def test_shutdown_cancels_pending_idle_timer(self):
    self.api.schedule_teardown(self.state)
    timer = self.state.teardown
    await self.api.on_shutdown(self.state)
    self.assertTrue(timer.cancelled)
    self.assertIsNone(self.state.teardown)
    timer.fire()
    self.params.put_bool.assert_not_called()

  async def test_shutdown_cancels_timer_rearmed_by_session_completion(self):
    await self.request()

    async def stop():
      self.session.run_task.set_result(None)
      await asyncio.sleep(0)  # Execute the real remove_finished_session callback.

    self.session.stop.side_effect = stop
    await self.api.on_shutdown(self.state)
    self.assertEqual(self.state.streams, {})
    self.assertIsNone(self.state.teardown)
    self.assertTrue(self.timer_loop.timers[-1].cancelled)
    self.timer_loop.timers[-1].fire()
    self.params.put_bool.assert_not_called()
