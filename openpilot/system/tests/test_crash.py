import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpilot.common import log_paths
from openpilot.system import crash


class TestCrash(unittest.TestCase):
  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp_dir.cleanup)
    self.log_dir = Path(self.temp_dir.name) / 'log'
    self.log_dir.mkdir()
    self.log_path = self.log_dir / 'tmux_error.log'
    self.flag_path = self.log_dir / 'tmux_error.log.uploaded'
    self.lock_path = Path(self.temp_dir.name) / 'tmux_error.log.lock'
    self.enterContext(patch.object(log_paths, 'LOG_DIR', self.log_dir))
    self.enterContext(patch.object(crash, 'CRASH_LOG_PATH', str(self.log_path)))
    self.enterContext(patch.object(crash, 'CRASH_UPLOAD_FLAG', str(self.flag_path)))
    self.enterContext(patch.object(crash, 'CRASH_UPLOAD_LOCK', str(self.lock_path)))
    self.enterContext(patch.object(crash, '_upload_process', None))
    self.cloudlog = self.enterContext(patch.object(crash, 'cloudlog'))
    self.popen = self.enterContext(patch.object(crash.subprocess, 'Popen'))
    self.popen.return_value.poll.return_value = 0
    self.run = self.enterContext(patch.object(crash.subprocess, 'run'))
    self.run.return_value = subprocess.CompletedProcess([], 0, 'uploaded', '')

  @staticmethod
  def capture():
    try:
      raise RuntimeError('crash test: 오류')
    except RuntimeError:
      crash.capture_exception()

  def test_save_before_upload(self):
    def check_upload(*args, **kwargs):
      contents = self.log_path.read_text(encoding='utf-8')
      self.assertRegex(contents, r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\n\nTraceback')
      self.assertIn('RuntimeError: crash test: 오류', contents)

    self.popen.side_effect = check_upload
    self.capture()
    self.cloudlog.exception.assert_called_once_with('crash')
    self.popen.assert_called_once_with(
      ['flock', '-n', str(self.lock_path), sys.executable, '-m', 'openpilot.system.crash'],
      cwd=crash.BASEDIR, stdin=subprocess.DEVNULL, start_new_session=True,
    )

  def test_missing_log_does_not_upload(self):
    crash.start_upload()
    crash.upload_pending_log()
    self.popen.assert_not_called()
    self.run.assert_not_called()

  def test_save_failure_does_not_upload_stale_log(self):
    self.log_path.write_text('old crash', encoding='utf-8')
    with patch.object(Path, 'write_text', side_effect=PermissionError('read only')):
      self.capture()
    self.assertEqual(self.log_path.read_text(encoding='utf-8'), 'old crash')
    self.popen.assert_not_called()
    self.cloudlog.exception.assert_any_call('Failed to save crash log')

  def test_upload_failure_keeps_log(self):
    self.popen.side_effect = OSError('cannot start flock')
    self.capture()
    self.assertIn('RuntimeError: crash test: 오류', self.log_path.read_text(encoding='utf-8'))
    self.cloudlog.exception.assert_any_call('Failed to trigger FTP upload')

  def test_running_upload_is_not_started_twice(self):
    self.log_path.write_text('pending', encoding='utf-8')
    self.popen.return_value.poll.return_value = None
    crash.start_upload()
    crash.start_upload()
    self.popen.assert_called_once()

  def test_success_sets_flag_and_skips_subsequent_uploads(self):
    self.log_path.write_text('pending', encoding='utf-8')

    def upload(args, **kwargs):
      self.assertEqual(args[:2], ['bash', os.path.join(crash.BASEDIR, 'scripts', 'log_upload.sh')])
      self.assertNotEqual(args[2], str(self.log_path))
      self.assertEqual(Path(args[2]).name, 'tmux_error.log')
      self.assertEqual(Path(args[2]).read_text(encoding='utf-8'), 'pending')
      return subprocess.CompletedProcess(args, 0, 'uploaded', '')

    self.run.side_effect = upload
    crash.upload_pending_log()
    self.assertEqual(self.flag_path.read_text(encoding='utf-8'), crash.log_version())
    crash.start_upload()
    crash.upload_pending_log()
    self.run.assert_called_once()
    self.popen.assert_not_called()

  def test_failure_leaves_pending_log_for_retry(self):
    self.log_path.write_text('pending', encoding='utf-8')
    self.run.return_value = subprocess.CompletedProcess([], 7, 'starting upload', 'connection refused')
    crash.upload_pending_log()
    self.assertFalse(self.flag_path.exists())
    self.cloudlog.error.assert_called_once()
    self.assertIn('connection refused', self.cloudlog.error.call_args.args[0])
    crash.start_upload()
    self.popen.assert_called_once()
    self.run.return_value = subprocess.CompletedProcess([], 0, '', '')
    crash.upload_pending_log()
    self.assertTrue(crash.is_uploaded(crash.log_version()))

  def test_timeout_or_launch_error_keeps_log_pending(self):
    self.log_path.write_text('pending', encoding='utf-8')
    for error in (subprocess.TimeoutExpired('bash', 120), FileNotFoundError('missing bash')):
      with self.subTest(error=error):
        self.run.side_effect = error
        crash.upload_pending_log()
        self.assertFalse(self.flag_path.exists())
        self.assertTrue(self.log_path.exists())
    self.assertEqual(self.cloudlog.exception.call_count, 2)

  def test_new_crash_resets_success_flag(self):
    self.log_path.write_text('old crash', encoding='utf-8')
    crash.upload_pending_log()
    self.capture()
    self.assertFalse(self.flag_path.exists())
    self.popen.assert_called_once()

  def test_new_crash_during_upload_is_not_marked_as_uploaded(self):
    self.log_path.write_text('old crash', encoding='utf-8')

    def upload(args, **kwargs):
      self.capture()
      self.assertEqual(Path(args[2]).read_text(encoding='utf-8'), 'old crash')
      return subprocess.CompletedProcess(args, 0, '', '')

    self.run.side_effect = upload
    crash.upload_pending_log()
    self.assertFalse(self.flag_path.exists())
    self.assertIn('RuntimeError: crash test: 오류', self.log_path.read_text(encoding='utf-8'))
    self.run.side_effect = None
    crash.upload_pending_log()
    self.assertTrue(crash.is_uploaded(crash.log_version()))

  def test_old_flag_cannot_suppress_new_log(self):
    self.log_path.write_text('old crash', encoding='utf-8')
    old_version = crash.log_version()
    self.capture()
    # A previous uploader may write its flag after a new log was saved.
    self.flag_path.write_text(old_version, encoding='utf-8')
    self.assertTrue(self.log_path.exists())
    crash.upload_pending_log()
    self.assertTrue(crash.is_uploaded(crash.log_version()))

  def test_restart_cleans_uploaded_log_and_flag(self):
    self.log_path.write_text('old crash', encoding='utf-8')
    crash.upload_pending_log()
    log_paths.clear_log_files()
    self.assertFalse(self.log_path.exists())
    self.assertFalse(self.flag_path.exists())

  def test_restart_removes_failed_upload(self):
    self.log_path.write_text('pending', encoding='utf-8')
    log_paths.clear_log_files()
    self.assertFalse(self.log_path.exists())
    crash.start_upload()
    self.popen.assert_not_called()

  def test_restart_removes_orphan_flag(self):
    self.flag_path.write_text('old flag', encoding='utf-8')
    log_paths.clear_log_files()
    self.assertFalse(self.flag_path.exists())

  def test_restart_removes_files_regardless_of_extension(self):
    for name in ('traffic_debug.log.1', 'dump.txt', '.hidden', 'flag'):
      (self.log_dir / name).write_text('old', encoding='utf-8')
    self.lock_path.touch()
    log_paths.clear_log_files()
    self.assertEqual(list(self.log_dir.iterdir()), [])
    self.assertTrue(self.lock_path.exists())

  def test_missing_log_directory_does_not_hide_crash(self):
    self.log_dir.rmdir()
    self.capture()
    self.assertFalse(self.log_dir.exists())
    self.popen.assert_not_called()
    self.cloudlog.exception.assert_any_call('Failed to save crash log')

  def test_upload_finishing_after_cleanup_does_not_restore_log_or_flag(self):
    self.log_path.write_text('pending', encoding='utf-8')

    def upload(args, **kwargs):
      log_paths.clear_log_files()
      return subprocess.CompletedProcess(args, 0, '', '')

    self.run.side_effect = upload
    crash.upload_pending_log()
    self.assertFalse(self.log_path.exists())
    self.assertFalse(self.flag_path.exists())

  def test_reporting_failure_preserves_original_exception(self):
    self.popen.side_effect = OSError('cannot start flock')
    with self.assertRaisesRegex(ValueError, 'original crash'):
      try:
        raise ValueError('original crash')
      except ValueError:
        crash.capture_exception()
        raise


if __name__ == '__main__':
  unittest.main()
