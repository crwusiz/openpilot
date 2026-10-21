from contextlib import ExitStack, contextmanager
import errno
import math
import os
from pathlib import Path
import shutil
import tempfile
import time


LOG_DIR = Path('/data/log')
CRASH_UPLOAD_LOCK = Path(tempfile.gettempdir()) / 'openpilot_crash_upload.lock'
LOG_UPLOAD_LOCK = Path(tempfile.gettempdir()) / 'openpilot_log_upload.lock'


def clear_log_files() -> None:
  """Start a fresh manager session, including logs that were not uploaded."""
  for path in LOG_DIR.iterdir():
    if path.is_file() or path.is_symlink():
      path.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path, deadline: float, *, create: bool = True):
  # Lazy import keeps normal manager startup and its tests portable. The full
  # update reset runs on Linux, where flock is also used by the upload scripts.
  import fcntl

  flags = os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, 'O_NOFOLLOW', 0)
  try:
    descriptor = os.open(path, flags, 0o600)
  except FileNotFoundError:
    if not create:
      yield
      return
    raise
  with os.fdopen(descriptor, 'r+') as lock:
    while True:
      try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except OSError as error:
        if error.errno not in (errno.EACCES, errno.EAGAIN):
          raise
        if time.monotonic() >= deadline:
          raise TimeoutError(f'Active log upload did not finish before reset: {path.name}') from error
        time.sleep(0.1)
    try:
      yield
    finally:
      fcntl.flock(lock, fcntl.LOCK_UN)


def clear_all_logs(timeout: float = 150.0) -> None:
  """Explicit successful-update reset; ordinary manager starts retain outboxes."""
  if not math.isfinite(timeout) or timeout <= 0:
    raise ValueError('Log reset timeout must be finite and greater than zero')
  if LOG_DIR.is_symlink():
    raise RuntimeError('Refusing to clear a symlinked log directory')
  LOG_DIR.mkdir(parents=True, exist_ok=True)
  if LOG_DIR.resolve() != LOG_DIR.absolute():
    raise RuntimeError('Refusing to clear logs through a symlinked parent')
  deadline = time.monotonic() + timeout
  print('Log reset: waiting for crash, log upload, and pending collection locks...', flush=True)
  with ExitStack() as locks:
    # Keep this order: the detached crash worker holds its lock while invoking
    # log_upload.sh, which then holds the shared log-upload lock.
    locks.enter_context(_exclusive_lock(CRASH_UPLOAD_LOCK, deadline))
    locks.enter_context(_exclusive_lock(LOG_UPLOAD_LOCK, deadline))
    outbox = LOG_DIR / '.cluster-pi-pending'
    if outbox.is_dir() and not outbox.is_symlink():
      # Previous running script versions do not know the shared lock. Their
      # reservation/job locks still protect snapshots and collectors until done.
      locks.enter_context(_exclusive_lock(outbox / 'reservation.lock', deadline))
      for job in sorted(outbox.glob('pending.*')):
        if job.is_dir() and not job.is_symlink():
          locks.enter_context(_exclusive_lock(job / 'lock', deadline, create=False))
    entries = list(LOG_DIR.iterdir())
    for path in entries:
      if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
      else:
        shutil.rmtree(path)
  print(f'Log reset complete: removed {len(entries)} entries; kept {LOG_DIR} and its permissions.', flush=True)


if __name__ == '__main__':
  import argparse
  import sys

  parser = argparse.ArgumentParser(description='Clear local logs after a successful Git Pull and stopped old session.')
  parser.add_argument('--clear-all', action='store_true', required=True)
  parser.add_argument('--timeout', type=float, default=150.0)
  options = parser.parse_args()
  try:
    clear_all_logs(options.timeout)
  except Exception as error:
    print(f'ERROR: Log reset failed: {error}', file=sys.stderr)
    sys.exit(1)
