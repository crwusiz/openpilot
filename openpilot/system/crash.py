"""Save Python crash tracebacks and trigger the community log uploader."""
import datetime
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from openpilot.common.basedir import BASEDIR
from openpilot.common.log_paths import CRASH_UPLOAD_LOCK as _CRASH_UPLOAD_LOCK, LOG_DIR
from openpilot.common.swaglog import cloudlog

CRASH_LOG_PATH = str(LOG_DIR / 'tmux_error.log')
CRASH_UPLOAD_FLAG = str(LOG_DIR / 'tmux_error.log.uploaded')
# Keep the lock outside the directory cleared on restart, so a detached uploader
# and the new manager continue to lock the same inode.
CRASH_UPLOAD_LOCK = str(_CRASH_UPLOAD_LOCK)
UPLOAD_RETRY_INTERVAL = 60
_upload_process: subprocess.Popen | None = None


def log_version(path: str | None = None) -> str | None:
  """Identify contents, so touching or replacing an unchanged log cannot resend it."""
  try:
    with open(CRASH_LOG_PATH if path is None else path, 'rb') as log_file:
      if not log_file.read(1):
        return None
      log_file.seek(0)
      return f'sha256:{hashlib.file_digest(log_file, "sha256").hexdigest()}'
  except FileNotFoundError:
    return None


def is_uploaded(version: str) -> bool:
  try:
    return Path(CRASH_UPLOAD_FLAG).read_text(encoding='utf-8') == version
  except FileNotFoundError:
    return False


def upload_token(snapshot: str, version: str) -> str:
  """Retry the same NAS name even if FTP succeeded but its acknowledgement was lost."""
  with open(snapshot, encoding='utf-8', errors='replace') as log_file:
    first_line = log_file.readline().strip()
  try:
    captured_at = datetime.datetime.strptime(first_line, '[%Y-%m-%d %H:%M:%S]').strftime('%y-%m-%d-%H:%M:%S')
  except ValueError:
    captured_at = 'crash'
  return f'{captured_at}-{version.removeprefix("sha256:")[:16]}'


def mark_uploaded(version: str) -> None:
  # A failed/interrupted flag write must not leave a partial success marker.
  with tempfile.TemporaryDirectory(dir=os.path.dirname(CRASH_UPLOAD_FLAG), prefix='crash-sent-') as tmp:
    flag = Path(tmp) / 'uploaded'
    flag.write_text(version, encoding='utf-8')
    os.replace(flag, CRASH_UPLOAD_FLAG)


def start_upload() -> None:
  """Start one nonblocking attempt; flock also excludes other crash reporters."""
  global _upload_process
  try:
    if _upload_process is not None and _upload_process.poll() is None:
      return
    version = log_version()
    if version is None or is_uploaded(version):
      return
    _upload_process = subprocess.Popen(
      ['flock', '-n', CRASH_UPLOAD_LOCK, sys.executable, '-m', 'openpilot.system.crash'],
      cwd=BASEDIR, stdin=subprocess.DEVNULL, start_new_session=True,
    )
  except Exception:
    cloudlog.exception('Failed to trigger FTP upload')


def upload_pending_log() -> None:
  """Called under flock. Record success for the exact log version uploaded."""
  try:
    version = log_version()
    if version is None or is_uploaded(version):
      return
    # Upload a stable snapshot even if another process reports a new crash.
    with tempfile.TemporaryDirectory(prefix='crash-upload-') as tmp:
      snapshot = os.path.join(tmp, os.path.basename(CRASH_LOG_PATH))
      shutil.copyfile(CRASH_LOG_PATH, snapshot)
      if log_version(snapshot) != version or log_version() != version:
        return
      result = subprocess.run(
        ['bash', os.path.join(BASEDIR, 'scripts', 'log_upload.sh'), snapshot],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, 'LOG_UPLOAD_PREFIX': upload_token(snapshot, version)},
      )
    if result.returncode != 0:
      cloudlog.error(f'Crash log upload failed ({result.returncode}): {result.stdout}\n{result.stderr}')
      return
    # The version in the flag prevents an old upload from marking a new crash as sent.
    if log_version() == version:
      try:
        mark_uploaded(version)
      except Exception:
        cloudlog.exception('Crash log uploaded but success marker could not be saved; retry reuses the same remote name')
        return
    cloudlog.info('Crash log upload completed')
  except Exception:
    cloudlog.exception('Failed to upload crash log')


def capture_exception() -> None:
  """Call from an exception handler; reporting failures must not hide the crash."""
  cloudlog.exception("crash")

  try:
    # Atomic replacement prevents the uploader from reading a partial traceback.
    with tempfile.TemporaryDirectory(dir=os.path.dirname(CRASH_LOG_PATH), prefix='crash-save-') as tmp:
      tmp_path = Path(tmp) / 'tmux_error.log'
      now = datetime.datetime.now()
      tmp_path.write_text(now.strftime('[%Y-%m-%d %H:%M:%S]') + "\n\n" + traceback.format_exc(), encoding='utf-8')
      os.replace(tmp_path, CRASH_LOG_PATH)
  except Exception:
    cloudlog.exception("Failed to save crash log")
    return

  start_upload()


if __name__ == '__main__':
  upload_pending_log()
