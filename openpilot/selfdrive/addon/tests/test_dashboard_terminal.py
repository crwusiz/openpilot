import asyncio
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nicegui import Client, core, ui

from openpilot.selfdrive.addon import dashboard


class TestDashboardTerminal(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    loop = patch.object(core, 'loop', asyncio.get_running_loop())
    loop.start()
    self.addCleanup(loop.stop)

  @contextmanager
  def terminal_page(self):
    tabs = SimpleNamespace(value='TERMINAL')
    client = Client(ui.page(f'/test-dashboard-terminal-{id(tabs)}'))
    self.addCleanup(lambda: client.delete() if not client.is_deleted else None)
    with client, patch.object(ui, 'timer') as timer, patch.object(ui, 'run_javascript') as javascript:
      dashboard.render_tab_terminal(tabs)
      interval, tick = timer.call_args.args
      self.assertEqual(interval, 1.0)
      html_elements = [element for element in client.elements.values() if isinstance(element, ui.html)]
      viewer = next(element for element in html_elements if element.content == 'Loading...')
      statusbar = next(element for element in html_elements if 'log-statusbar' in element.content)
      yield SimpleNamespace(client=client, tabs=tabs, tick=tick, viewer=viewer, statusbar=statusbar, javascript=javascript)

  async def test_overlapping_ticks_are_skipped_and_next_tick_can_refresh(self):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def read_capture():
      entered.set()
      await finish.wait()
      return 'captured terminal output'

    with self.terminal_page() as page, patch.object(dashboard, 'get_tmux_capture', AsyncMock(side_effect=read_capture)) as capture:
      first = asyncio.create_task(page.tick())
      try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(page.tick(), 1)
        self.assertEqual(capture.await_count, 1)
        page.javascript.assert_not_called()
      finally:
        finish.set()
        await first

      self.assertIn('captured terminal output', page.viewer.content)
      self.assertEqual(capture.await_count, 1)
      self.assertEqual(page.javascript.call_count, 1)
      await page.tick()
      self.assertEqual(capture.await_count, 2)
      self.assertEqual(page.javascript.call_count, 2)

  async def test_capture_errors_are_visible_and_allow_retry(self):
    for error in (OSError('capture <failed>'), RuntimeError('capture <failed>'), TimeoutError()):
      with self.subTest(error=type(error).__name__), self.terminal_page() as page, \
           patch.object(dashboard, 'get_tmux_capture', AsyncMock(side_effect=[error, 'recovered terminal output'])) as capture:
        await page.tick()
        self.assertIn('log-error', page.statusbar.content)
        self.assertIn('Tmux capture timed out' if isinstance(error, TimeoutError) else 'capture &lt;failed&gt;', page.viewer.content)

        await page.tick()
        self.assertEqual(capture.await_count, 2)
        self.assertIn('recovered terminal output', page.viewer.content)
        self.assertNotIn('log-error', page.statusbar.content)
        self.assertEqual(page.javascript.call_count, 2)

  async def test_cancelled_capture_releases_guard_without_updating_viewer(self):
    entered = asyncio.Event()

    async def read_capture():
      entered.set()
      await asyncio.Future()

    with self.terminal_page() as page, patch.object(dashboard, 'get_tmux_capture', AsyncMock(side_effect=read_capture)) as capture:
      initial = page.viewer.content, page.statusbar.content
      task = asyncio.create_task(page.tick())
      try:
        await asyncio.wait_for(entered.wait(), 1)
      finally:
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
          await task
      self.assertEqual((page.viewer.content, page.statusbar.content), initial)
      page.javascript.assert_not_called()

      capture.side_effect = None
      capture.return_value = 'retry after cancellation'
      await page.tick()
      self.assertEqual(capture.await_count, 2)
      self.assertIn('retry after cancellation', page.viewer.content)
      page.javascript.assert_called_once()

  async def test_tab_change_or_deleted_viewer_discards_pending_capture(self):
    for change in ('tab', 'viewer', 'client'):
      entered, finish = asyncio.Event(), asyncio.Event()

      async def read_capture(started=entered, released=finish):
        started.set()
        await released.wait()
        return 'stale terminal output'

      with self.subTest(change=change), self.terminal_page() as page, \
           patch.object(dashboard, 'get_tmux_capture', AsyncMock(side_effect=read_capture)) as capture:
        initial = page.viewer.content, page.statusbar.content
        task = asyncio.create_task(page.tick())
        try:
          await asyncio.wait_for(entered.wait(), 1)
          if change == 'tab':
            page.tabs.value = 'LOGS'
          elif change == 'viewer':
            page.viewer.delete()
          else:
            page.client.delete()
        finally:
          finish.set()
          await task

        self.assertEqual((page.viewer.content, page.statusbar.content), initial)
        page.javascript.assert_not_called()
        if change == 'tab':
          await page.tick()
          self.assertEqual(capture.await_count, 1)
          page.tabs.value = 'TERMINAL'
          capture.side_effect = None
          capture.return_value = 'fresh after tab return'
          await page.tick()
          self.assertEqual(capture.await_count, 2)
          self.assertIn('fresh after tab return', page.viewer.content)
          page.javascript.assert_called_once()
        else:
          await page.tick()
          self.assertEqual(capture.await_count, 1)
          page.javascript.assert_not_called()
