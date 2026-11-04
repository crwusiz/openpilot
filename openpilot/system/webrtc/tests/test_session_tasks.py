"""Exercise session-owned background tasks without native messaging or RTC sockets."""
import asyncio
import json
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from openpilot.system.webrtc.helpers import StreamRequestBody
from openpilot.system.webrtc.tests.test_lan_lifecycle import load_lifecycle


class TestSessionTasks(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.params = Mock()
    self.api = load_lifecycle(asyncio.get_running_loop(), self.params)
    self.api.StreamSession.run.__globals__['SESSION_TIMEOUT_SECONDS'] = 300
    self.release_cleanup = asyncio.Event()
    self.release_cleanup.set()
    self.outgoing = self.make_worker()
    self.bitrate = self.make_worker()
    self.api.CerealOutgoingMessageProxy.return_value = self.outgoing
    self.api.LivestreamBitrateController.return_value = self.bitrate

    self.disconnected = asyncio.Event()
    self.stream = Mock()
    self.stream.wait_for_connection = AsyncMock()
    self.stream.wait_for_disconnection = AsyncMock(side_effect=self.disconnected.wait)
    self.stream.stop = AsyncMock()
    self.stream.has_messaging_channel.return_value = True
    self.track = Mock(id='road-track')
    video_module = ModuleType('openpilot.system.webrtc.device.video')
    video_module.LiveStreamVideoStreamTrack = Mock(return_value=self.track)
    builder_module = ModuleType('teleoprtc.builder')
    builder_module.WebRTCAnswerBuilder = Mock()
    builder_module.WebRTCAnswerBuilder.return_value.stream.return_value = self.stream
    with patch.dict(sys.modules, {video_module.__name__: video_module, builder_module.__name__: builder_module}):
      self.session = self.api.StreamSession(StreamRequestBody(
        'offer', ['road'], True, bridge_services_out=['carState'],
      ))
    self.addAsyncCleanup(self.cleanup_session)

  def make_worker(self):
    worker = SimpleNamespace(started=asyncio.Event(), finishing=asyncio.Event(), finishes=0, add_channel=Mock(), enable=Mock())

    async def run():
      worker.started.set()
      try:
        await asyncio.Event().wait()
      finally:
        worker.finishing.set()
        await self.release_cleanup.wait()
        worker.finishes += 1

    worker.run = AsyncMock(side_effect=run)
    return worker

  async def cleanup_session(self):
    self.release_cleanup.set()
    await self.session.stop()

  async def wait_started(self):
    await asyncio.wait_for(asyncio.gather(self.outgoing.started.wait(), self.bitrate.started.wait()), timeout=1)

  def assert_closed_once(self):
    self.track.stop.assert_called_once_with()
    self.stream.stop.assert_awaited_once_with()
    self.assertEqual(self.session.background_tasks, [])
    self.assertEqual(self.session.video_tracks, [])

  async def test_stop_waits_for_both_background_tasks_and_cleans_up_once(self):
    self.session.start()
    await self.wait_started()
    tasks = tuple(self.session.background_tasks)
    self.assertEqual(len(tasks), 2)
    self.outgoing.add_channel.assert_called_once_with(self.stream.get_messaging_channel.return_value)
    self.release_cleanup.clear()
    stop_task = asyncio.create_task(self.session.stop())
    try:
      await asyncio.wait_for(asyncio.gather(self.outgoing.finishing.wait(), self.bitrate.finishing.wait()), timeout=1)
      self.assertFalse(stop_task.done())
      self.track.stop.assert_not_called()
      self.stream.stop.assert_not_awaited()
    finally:
      self.release_cleanup.set()
      await stop_task

    self.assertTrue(all(task.cancelled() for task in tasks))
    self.assertEqual((self.outgoing.finishes, self.bitrate.finishes), (1, 1))
    self.outgoing.run.assert_awaited_once_with()
    self.bitrate.run.assert_awaited_once_with()
    await self.session.stop()
    self.assert_closed_once()

  async def test_video_only_connection_starts_bitrate_without_outgoing_bridge(self):
    self.stream.has_messaging_channel.return_value = False
    self.session.start()
    await asyncio.wait_for(self.bitrate.started.wait(), timeout=1)
    tasks = tuple(self.session.background_tasks)
    self.assertEqual(len(tasks), 1)
    self.outgoing.add_channel.assert_not_called()
    self.outgoing.run.assert_not_awaited()
    await self.session.stop()
    self.assertTrue(tasks[0].cancelled())
    self.assertEqual(self.bitrate.finishes, 1)
    self.assert_closed_once()

  async def test_connection_failure_closes_transport_without_starting_workers(self):
    self.stream.wait_for_connection.side_effect = ConnectionError('ICE failed')
    self.session.start()
    await asyncio.wait_for(self.session.run_task, timeout=1)
    self.outgoing.run.assert_not_awaited()
    self.bitrate.run.assert_not_awaited()
    self.assert_closed_once()
    await self.session.stop()
    self.assert_closed_once()

  async def test_remote_disconnect_cancels_workers_before_closing_transport(self):
    self.session.start()
    await self.wait_started()
    tasks = tuple(self.session.background_tasks)
    self.disconnected.set()
    await asyncio.wait_for(self.session.run_task, timeout=1)
    self.assertTrue(all(task.cancelled() for task in tasks))
    self.assertEqual((self.outgoing.finishes, self.bitrate.finishes), (1, 1))
    await self.session.stop()
    self.assert_closed_once()

  async def test_paused_session_requests_keyframe_when_video_is_enabled(self):
    self.session.message_handler(json.dumps({'type': 'livestreamVideoEnable', 'data': {'enabled': False}}).encode())
    self.assertFalse(self.session.video_enabled)
    self.track.enable.assert_called_once_with(False)
    self.outgoing.enable.assert_called_once_with(False)
    self.bitrate.enable.assert_called_once_with(False)
    self.params.put.assert_not_called()

    self.session.start()
    await self.wait_started()
    self.params.put.assert_not_called()
    self.session.message_handler(json.dumps({'type': 'livestreamVideoEnable', 'data': {'enabled': True}}).encode())
    self.assertTrue(self.session.video_enabled)
    self.track.enable.assert_called_with(True)
    self.outgoing.enable.assert_called_with(True)
    self.bitrate.enable.assert_called_with(True)
    self.params.put.assert_called_once_with('LivestreamRequestKeyframe', True)
