"""Save Python crash tracebacks and trigger the community log uploader."""
import datetime
import os
import subprocess
import traceback

from openpilot.common.basedir import BASEDIR
from openpilot.common.swaglog import cloudlog

CRASH_LOG_PATH = '/data/tmux_error.log'


def capture_exception() -> None:
  """Call from an exception handler; reporting failures must not hide the crash."""
  cloudlog.exception("crash")

  try:
    with open(CRASH_LOG_PATH, 'w', encoding='utf-8') as f:
      now = datetime.datetime.now()
      f.write(now.strftime('[%Y-%m-%d %H:%M:%S]') + "\n\n" + traceback.format_exc())
  except Exception:
    cloudlog.exception("Failed to save crash log")
    return

  try:
    script_path = os.path.join(BASEDIR, 'scripts', 'log_upload.sh')
    if os.path.isfile(script_path):
      subprocess.Popen(['bash', script_path, os.path.basename(CRASH_LOG_PATH)],
                       stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       start_new_session=True)
  except Exception:
    cloudlog.exception("Failed to trigger FTP upload")
