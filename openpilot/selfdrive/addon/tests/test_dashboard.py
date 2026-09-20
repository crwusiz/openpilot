import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from nicegui import Client, ui

from openpilot.selfdrive.addon import dashboard


class TestDashboardHelpers(unittest.TestCase):
  def test_script_arguments_and_environment(self):
    args = ['a path with spaces', 'literal;argument']
    self.assertEqual(dashboard._script_command('/tmp/test.sh', args), ['bash', '/tmp/test.sh', *args])
    self.assertEqual(dashboard._script_command('/tmp/test.py'), [sys.executable, '/tmp/test.py'])
    with patch.dict(os.environ, {'TMUX': 'session', 'TMUX_PANE': 'pane', 'KEEP_ME': 'yes'}):
      env = dashboard._subprocess_env()
      self.assertNotIn('TMUX', env)
      self.assertNotIn('TMUX_PANE', env)
      self.assertEqual(env['KEEP_ME'], 'yes')
      self.assertIn('TMUX', os.environ)

  def test_param_text_handles_bytes_and_missing_values(self):
    with patch.object(dashboard, 'params') as params:
      for value, expected in ((b'HYUNDAI', 'HYUNDAI'), ('GENESIS', 'GENESIS'), (None, 'default'), (b'', 'default')):
        params.get.return_value = value
        self.assertEqual(dashboard.get_param_text('key', 'default'), expected)

  def test_error_summary_prefers_curl_error(self):
    self.assertEqual(dashboard._get_error_summary('progress\rcurl: (7) connection refused\nmore progress'), 'curl: (7) connection refused')
    self.assertEqual(dashboard._get_error_summary('starting\nERROR: upload failed'), 'ERROR: upload failed')
    self.assertEqual(dashboard._get_error_summary(''), 'Unknown error')

  def test_routes_ignore_invalid_names_and_sort_segments_numerically(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      for name in ('old--route--10', 'old--route--2', 'new--route--0', 'boot', 'invalid--name', 'bad--route--tail'):
        path = root / name
        path.mkdir()
        timestamp = 20 if name.startswith('new') else 10
        os.utime(path, (timestamp, timestamp))
      (root / 'file--route--0').touch()
      routes = dashboard.get_routes(root)
      self.assertEqual([route.name for route in routes], ['new--route', 'old--route'])
      self.assertEqual([path.name for path in routes[1].paths], ['old--route--2', 'old--route--10'])
      self.assertEqual(dashboard.get_routes(root / 'missing'), [])


class TestDashboardAsync(unittest.IsolatedAsyncioTestCase):
  async def test_command_captures_stdout_and_stderr(self):
    snapshots = []
    code, output = await dashboard._run_command(
      [sys.executable, '-c', "import sys; print('stdout', flush=True); print('stderr', file=sys.stderr); sys.exit(7)"],
      snapshots.append,
    )
    self.assertEqual(code, 7)
    self.assertIn('stdout', output)
    self.assertIn('stderr', output)
    self.assertTrue(snapshots)

  async def test_large_output_without_newlines_is_bounded(self):
    with patch.object(dashboard, 'MAX_COMMAND_OUTPUT', 1024):
      code, output = await dashboard._run_command([sys.executable, '-c', "print('x' * 200000, end='TAIL')"])
    self.assertEqual(code, 0)
    self.assertEqual(len(output), 1024)
    self.assertTrue(output.endswith('TAIL'))

  async def test_split_utf8_output_is_decoded_correctly(self):
    process = Mock(returncode=0)
    process.stdout.read = AsyncMock(side_effect=[b'\xed', b'\x95\x9c', b''])
    process.wait = AsyncMock(return_value=0)
    with patch.object(dashboard.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
      code, output = await dashboard._run_command(['mock'])
    self.assertEqual((code, output), (0, '한'))

  async def test_cancellation_reaps_process(self):
    created = asyncio.Event()
    processes = []
    create = asyncio.create_subprocess_exec

    async def record_process(*args, **kwargs):
      process = await create(*args, **kwargs)
      processes.append(process)
      created.set()
      return process

    with patch.object(dashboard.asyncio, 'create_subprocess_exec', record_process):
      task = asyncio.create_task(dashboard._run_command([sys.executable, '-c', 'import time; time.sleep(60)']))
      await asyncio.wait_for(created.wait(), 5)
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task
    self.assertIsNotNone(processes[0].returncode)

  async def test_tmux_capture_failure_does_not_overwrite_previous_log(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / 'tmux_console.log'
      path.write_text('previous capture', encoding='utf-8')
      with patch.object(dashboard, 'TMUX_LOG_PATH', path), \
           patch.object(dashboard, '_run_command', AsyncMock(side_effect=[(0, ''), (1, 'no server running')])):
        with self.assertRaisesRegex(RuntimeError, 'no server running'):
          await dashboard.save_tmux_log()
      self.assertEqual(path.read_text(encoding='utf-8'), 'previous capture')

  async def test_tmux_capture_saves_only_successful_output(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / 'tmux_console.log'
      with patch.object(dashboard, 'TMUX_LOG_PATH', path), \
           patch.object(dashboard, '_run_command', AsyncMock(side_effect=[(0, ''), (0, 'captured')])) as command:
        self.assertEqual(await dashboard.save_tmux_log(), path)
      self.assertEqual(path.read_text(encoding='utf-8'), 'captured')
      self.assertEqual(command.call_args.args[0][-1], '-500')

  async def test_notifications_include_stdout_errors(self):
    with patch.object(dashboard, '_run_command', AsyncMock(return_value=(1, 'ERROR: file not found'))), \
         patch.object(ui, 'notify') as notify:
      self.assertEqual(await dashboard.run_script_async('Upload', '/tmp/test.sh'), 1)
      self.assertIn('ERROR: file not found', notify.call_args.args[0])

  async def test_git_pull_modal_does_not_reboot(self):
    client = Client(ui.page('/test-dashboard-modal'))
    self.addCleanup(client.delete)
    with client, patch.object(ui, 'run_javascript'), \
         patch.object(dashboard, '_run_command', AsyncMock(return_value=(0, 'tmux started'))), \
         patch.object(dashboard.subprocess, 'Popen') as popen:
      code = await dashboard.run_script_async('Git Pull', '/tmp/gitpull.sh', show_modal=True)
    self.assertEqual(code, 0)
    popen.assert_not_called()
    self.assertTrue(any('tmux에서 업데이트를 시작했습니다.' in element.content for element in client.elements.values() if isinstance(element, ui.html)))

  async def test_page_builds_with_stale_selections_and_byte_params(self):
    client = Client(ui.page('/test-dashboard-page'))
    self.addCleanup(client.delete)
    values = {'SelectedManufacturer': b'HYUNDAI', 'SelectedCar': 'removed car', 'SelectedBranch': 'removed branch', 'DevicePosition': '<device>'}
    params = Mock(get=Mock(side_effect=values.get), get_bool=Mock(return_value=False))
    with client, patch.object(dashboard, 'params', params), patch.object(ui, 'run_javascript'), patch.object(ui, 'timer'), \
         patch.object(dashboard, 'get_list_from_file', return_value=[]), patch.object(dashboard, 'get_routes', return_value=[]):
      dashboard.main_page()
    selects = [element for element in client.elements.values() if isinstance(element, ui.select)]
    self.assertEqual([element.value for element in selects[:3]], ['HYUNDAI', '[ Not Selected ]', '[ Not Selected ]'])
    self.assertTrue(any('&lt;device&gt;' in element.content for element in client.elements.values() if isinstance(element, ui.html)))


if __name__ == '__main__':
  unittest.main()
