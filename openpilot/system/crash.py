"""Save crash tracebacks and locationd console logs for the community uploader."""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path

from openpilot.common.basedir import BASEDIR
from openpilot.common.log_paths import LOG_DIR
from openpilot.common.swaglog import cloudlog

CRASH_LOG_PATH = str(LOG_DIR / 'tmux_error.log')
CRASH_UPLOAD_FLAG = str(LOG_DIR / 'tmux_error.log.uploaded')
LOCATIOND_LOG_PATH = str(LOG_DIR / 'tmux_locationd.log')
LOCATIOND_UPLOAD_FLAG = str(LOG_DIR / 'tmux_locationd.log.uploaded')
# Keep the lock outside the directory cleared on restart, so a detached uploader
# and the new manager continue to lock the same inode.
CRASH_UPLOAD_LOCK = os.path.join(tempfile.gettempdir(), 'openpilot_crash_upload.lock')
UPLOAD_RETRY_INTERVAL = 60
_upload_process: subprocess.Popen | None = None


def log_version(path: str | None = None) -> str | None:
  try:
    st = os.stat(path if path is not None else CRASH_LOG_PATH)
    return f'{st.st_dev}:{st.st_ino}:{st.st_mtime_ns}:{st.st_size}'
  except FileNotFoundError:
    return None


def is_uploaded(version: str, flag: str | None = None) -> bool:
  try:
    return Path(flag if flag is not None else CRASH_UPLOAD_FLAG).read_text(encoding='utf-8') == version
  except FileNotFoundError:
    return False


def start_upload() -> None:
  """Start one nonblocking attempt; flock also excludes other crash reporters."""
  global _upload_process
  try:
    if _upload_process is not None and _upload_process.poll() is None:
      return
    pending = False
    for path, flag in ((CRASH_LOG_PATH, CRASH_UPLOAD_FLAG), (LOCATIOND_LOG_PATH, LOCATIOND_UPLOAD_FLAG)):
      version = log_version(path)
      pending |= version is not None and not is_uploaded(version, flag)
    if not pending:
      return
    _upload_process = subprocess.Popen(
      ['flock', '-n', CRASH_UPLOAD_LOCK, sys.executable, '-m', 'openpilot.system.crash'],
      cwd=BASEDIR, stdin=subprocess.DEVNULL, start_new_session=True,
    )
  except Exception:
    cloudlog.exception('Failed to trigger FTP upload')


def upload_pending_log(path: str | None = None, flag: str | None = None) -> None:
  """Called under flock. Record success for the exact log version uploaded."""
  path = path if path is not None else CRASH_LOG_PATH
  flag = flag if flag is not None else CRASH_UPLOAD_FLAG
  try:
    version = log_version(path)
    if version is None or is_uploaded(version, flag):
      return
    # Upload a stable snapshot even if another process reports a new crash.
    with tempfile.TemporaryDirectory(prefix='crash-upload-') as tmp:
      snapshot = os.path.join(tmp, os.path.basename(path))
      shutil.copyfile(path, snapshot)
      if log_version(path) != version:
        return
      result = subprocess.run(
        ['bash', os.path.join(BASEDIR, 'scripts', 'log_upload.sh'), snapshot],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
      )
    if result.returncode != 0:
      cloudlog.error(f'Log upload failed for {path} ({result.returncode}): {result.stdout}\n{result.stderr}')
      return
    # The version in the flag prevents an old upload from marking a new crash as sent.
    if log_version(path) == version:
      Path(flag).write_text(version, encoding='utf-8')
    cloudlog.info(f'Log upload completed: {path}')
  except Exception:
    cloudlog.exception(f'Failed to upload log: {path}')


def save_locationd_log() -> None:
  """Capture outside the control loop; manager retries pending uploads."""
  try:
    result = subprocess.run(
      ['tmux', 'capture-pane', '-p', '-t', '0', '-S', '-500'],
      stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5, check=True,
    )
    with tempfile.TemporaryDirectory(dir=os.path.dirname(LOCATIOND_LOG_PATH), prefix='locationd-save-') as tmp:
      snapshot = Path(tmp) / 'tmux_locationd.log'
      header = datetime.datetime.now().strftime('[%Y-%m-%d %H:%M:%S] locationdTemporaryError\n\n')
      snapshot.write_text(header + result.stdout, encoding='utf-8')
      Path(LOCATIOND_UPLOAD_FLAG).unlink(missing_ok=True)
      os.replace(snapshot, LOCATIOND_LOG_PATH)
  except Exception:
    cloudlog.exception('Failed to capture locationd tmux log')
    return
  start_upload()


def capture_locationd_log() -> None:
  try:
    threading.Thread(target=save_locationd_log, name='locationd-log', daemon=True).start()
  except Exception:
    cloudlog.exception('Failed to start locationd tmux capture')


def capture_exception() -> None:
  """Call from an exception handler; reporting failures must not hide the crash."""
  cloudlog.exception("crash")

  try:
    # Atomic replacement prevents the uploader from reading a partial traceback.
    with tempfile.TemporaryDirectory(dir=os.path.dirname(CRASH_LOG_PATH), prefix='crash-save-') as tmp:
      tmp_path = Path(tmp) / 'tmux_error.log'
      now = datetime.datetime.now()
      tmp_path.write_text(now.strftime('[%Y-%m-%d %H:%M:%S]') + "\n\n" + traceback.format_exc(), encoding='utf-8')
      Path(CRASH_UPLOAD_FLAG).unlink(missing_ok=True)
      os.replace(tmp_path, CRASH_LOG_PATH)
  except Exception:
    cloudlog.exception("Failed to save crash log")
    return

  start_upload()


if __name__ == '__main__':
  upload_pending_log()
  upload_pending_log(LOCATIOND_LOG_PATH, LOCATIOND_UPLOAD_FLAG)
