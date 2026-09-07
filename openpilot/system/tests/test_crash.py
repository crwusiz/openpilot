import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpilot.system import crash


class TestCrash(unittest.TestCase):
  def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp_dir.cleanup)
    self.log_path = Path(self.temp_dir.name) / 'tmux_error.log'
    self.enterContext(patch.object(crash, 'CRASH_LOG_PATH', str(self.log_path)))
    self.cloudlog = self.enterContext(patch.object(crash, 'cloudlog'))
    self.popen = self.enterContext(patch.object(crash.subprocess, 'Popen'))
    self.script_exists = self.enterContext(patch.object(crash.os.path, 'isfile', return_value=True))

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
      ['bash', os.path.join(crash.BASEDIR, 'scripts', 'log_upload.sh'), 'tmux_error.log'],
      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )

  def test_missing_script_keeps_log(self):
    self.script_exists.return_value = False
    self.capture()
    self.assertIn('RuntimeError: crash test: 오류', self.log_path.read_text(encoding='utf-8'))
    self.popen.assert_not_called()

  def test_save_failure_does_not_upload_stale_log(self):
    self.log_path.write_text('old crash', encoding='utf-8')
    with patch('builtins.open', side_effect=PermissionError('read only')):
      self.capture()
    self.popen.assert_not_called()
    self.cloudlog.exception.assert_any_call('Failed to save crash log')

  def test_upload_failure_keeps_log(self):
    self.popen.side_effect = OSError('cannot start bash')
    self.capture()
    self.assertIn('RuntimeError: crash test: 오류', self.log_path.read_text(encoding='utf-8'))
    self.cloudlog.exception.assert_any_call('Failed to trigger FTP upload')

  def test_reporting_failure_preserves_original_exception(self):
    self.popen.side_effect = OSError('cannot start bash')
    with self.assertRaisesRegex(ValueError, 'original crash'):
      try:
        raise ValueError('original crash')
      except ValueError:
        crash.capture_exception()
        raise


if __name__ == '__main__':
  unittest.main()
