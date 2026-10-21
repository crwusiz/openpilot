from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from openpilot.common import log_paths


ROOT = Path(__file__).resolve().parents[3]
GIT_BASH = Path('C:/Program Files/Git/bin/bash.exe')
BASH = str(GIT_BASH) if os.name == 'nt' and GIT_BASH.exists() else shutil.which('bash')


@pytest.fixture
def log_directory(tmp_path, monkeypatch):
  directory = tmp_path / 'log'
  directory.mkdir()
  monkeypatch.setattr(log_paths, 'LOG_DIR', directory)
  calls = []

  @contextmanager
  def locking(path, deadline, *, create=True):
    calls.append((Path(path), create))
    yield

  monkeypatch.setattr(log_paths, '_exclusive_lock', locking)
  return directory, calls


def test_update_reset_removes_hidden_outbox_and_keeps_directory(log_directory):
  directory, calls = log_directory
  permissions = directory.stat().st_mode
  job = directory / '.cluster-pi-pending/pending.original'
  job.mkdir(parents=True)
  (job / 'cluster_crash.log').write_text('old stack')
  (job / 'lock').touch()
  (directory / 'tmux_error.log').write_text('old error')
  (directory / '.flag').touch()
  log_paths.clear_all_logs()
  assert directory.is_dir() and list(directory.iterdir()) == []
  assert directory.stat().st_mode == permissions
  assert calls == [(log_paths.CRASH_UPLOAD_LOCK, True), (log_paths.LOG_UPLOAD_LOCK, True),
                   (directory / '.cluster-pi-pending/reservation.lock', True), (job / 'lock', False)]


def test_normal_restart_keeps_pending_uploads(log_directory):
  directory, calls = log_directory
  outbox = directory / '.cluster-pi-pending'
  outbox.mkdir()
  (outbox / 'saved.log').write_text('pending original')
  (directory / 'tmux_error.log').write_text('old error')
  log_paths.clear_log_files()
  assert (outbox / 'saved.log').read_text() == 'pending original'
  assert not (directory / 'tmux_error.log').exists()
  assert calls == []


@pytest.mark.parametrize('symlink_root', [False, True])
def test_reset_never_follows_symlinks(log_directory, tmp_path, monkeypatch, symlink_root):
  directory, _ = log_directory
  outside = tmp_path / 'outside'
  outside.mkdir()
  preserved = outside / 'keep.log'
  preserved.write_text('outside')
  link = tmp_path / 'log-link' if symlink_root else directory / '.cluster-pi-pending'
  try:
    link.symlink_to(outside, target_is_directory=True)
  except OSError:
    pytest.skip('Directory symlinks require Windows developer mode or elevated privileges')
  if symlink_root:
    monkeypatch.setattr(log_paths, 'LOG_DIR', link)
    with pytest.raises(RuntimeError, match='symlinked log directory'):
      log_paths.clear_all_logs()
  else:
    log_paths.clear_all_logs()
    assert not link.exists()
  assert preserved.read_text() == 'outside'


@pytest.mark.parametrize('blocked', ['crash', 'upload', 'reservation', 'job'])
def test_active_upload_timeout_keeps_all_logs(log_directory, monkeypatch, blocked):
  directory, _ = log_directory
  (directory / 'tmux_error.log').write_text('pending old error')
  job = directory / '.cluster-pi-pending/pending.legacy'
  job.mkdir(parents=True)
  (job / 'lock').touch()
  (job / 'saved.log').write_text('old original')
  paths = {'crash': log_paths.CRASH_UPLOAD_LOCK, 'upload': log_paths.LOG_UPLOAD_LOCK,
           'reservation': job.parent / 'reservation.lock', 'job': job / 'lock'}

  @contextmanager
  def busy(path, *args, **kwargs):
    if path == paths[blocked]:
      raise TimeoutError('active uploader')
    yield

  monkeypatch.setattr(log_paths, '_exclusive_lock', busy)
  with pytest.raises(TimeoutError, match='active uploader'):
    log_paths.clear_all_logs(0.01)
  assert (directory / 'tmux_error.log').read_text() == 'pending old error'
  assert (job / 'saved.log').read_text() == 'old original'


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf')])
def test_invalid_timeout_cannot_disable_bounded_reset(log_directory, timeout):
  directory, calls = log_directory
  (directory / 'saved.log').write_text('keep')
  with pytest.raises(ValueError, match='finite and greater than zero'):
    log_paths.clear_all_logs(timeout)
  assert calls == [] and (directory / 'saved.log').read_text() == 'keep'


@pytest.mark.parametrize('blocked', [False, True])
def test_lock_wait_is_bounded_and_releases_successful_lock(tmp_path, monkeypatch, blocked):
  calls = []

  def flock(file, mode):
    calls.append(mode)
    if blocked:
      raise BlockingIOError(errno.EAGAIN, 'busy')

  monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(flock=flock, LOCK_EX=1, LOCK_NB=2, LOCK_UN=4))
  if blocked:
    with pytest.raises(TimeoutError):
      with log_paths._exclusive_lock(tmp_path / 'test.lock', 0):
        pytest.fail('Busy lock was acquired')
  else:
    with log_paths._exclusive_lock(tmp_path / 'test.lock', 0):
      pass
  assert calls == ([3] if blocked else [3, 4])


@pytest.fixture
def restart_sandbox(tmp_path):
  if not BASH:
    pytest.skip('Bash is required to check the tmux handoff')
  root = tmp_path / 'checkout with spaces'
  scripts = root / 'scripts'
  scripts.mkdir(parents=True)
  log = tmp_path / 'calls'
  helper = root / 'openpilot/common/log_paths.py'
  helper.parent.mkdir(parents=True)
  helper.touch()
  script = (ROOT / 'scripts/restart.sh').read_text().replace('/data/openpilot', root.as_posix())
  (scripts / 'restart.sh').write_text(script, newline='\n')
  (root / 'launch_openpilot.sh').write_text('printf "launch\\n" >> "$CALL_LOG"\n', newline='\n')
  bin_dir = tmp_path / 'bin'
  bin_dir.mkdir()
  (bin_dir / 'tmux').write_text('#!/usr/bin/env bash\nprintf "tmux %s\\n" "$*" >> "$CALL_LOG"\n', newline='\n')
  (bin_dir / 'python3').write_text('#!/usr/bin/env bash\nprintf "reset %s\\n" "$*" >> "$CALL_LOG"\nexit "${RESET_EXIT:-0}"\n', newline='\n')
  for command in bin_dir.iterdir():
    command.chmod(0o755)
  return root, log


def run_restart(sandbox, *arguments, **extra_env):
  root, calls = sandbox
  env = {**os.environ, 'CALL_LOG': calls.as_posix(), **extra_env}
  result = subprocess.run([BASH, '-c', 'export PATH="../bin:/usr/bin:/bin"; exec bash scripts/restart.sh "$@"', 'test', *arguments],
                          cwd=root, env=env, capture_output=True, text=True, timeout=10, check=False)
  return result, calls.read_text().splitlines() if calls.exists() else []


def test_update_handoff_stops_old_session_then_resets_then_launches(restart_sandbox):
  result, calls = run_restart(restart_sandbox, '--handoff', '--clear-logs')
  assert result.returncode == 0, result.stderr
  assert calls[0] == 'tmux set-option -p remain-on-exit on'
  assert calls[1] == 'tmux kill-session -t comma'
  assert calls[2].startswith('reset ') and '--clear-all --timeout 150' in calls[2]
  assert calls[3:] == ['tmux rename-session -t tmp comma', 'launch']


def test_failed_reset_stays_visible_and_does_not_launch(restart_sandbox):
  result, calls = run_restart(restart_sandbox, '--handoff', '--clear-logs', RESET_EXIT='1')
  assert result.returncode == 1
  assert 'Replacement was not started' in result.stderr
  assert calls[0] == 'tmux set-option -p remain-on-exit on'
  assert not any(call == 'launch' or 'rename-session' in call for call in calls)


def test_normal_restart_handoff_preserves_pending_policy(restart_sandbox):
  result, calls = run_restart(restart_sandbox, '--handoff')
  assert result.returncode == 0, result.stderr
  assert calls == ['tmux kill-session -t comma', 'tmux rename-session -t tmp comma', 'launch']


def test_update_flag_is_forwarded_to_replacement_tmux_pane(restart_sandbox):
  result, calls = run_restart(restart_sandbox, '--clear-logs')
  assert result.returncode == 0, result.stderr
  assert calls[0] == 'tmux kill-session -t tmp'
  assert len(calls) == 2 and calls[1].startswith('tmux new-session ')
  assert '--handoff --clear-logs' in calls[1]


@pytest.mark.parametrize('mismatch', [False, True])
def test_gitpull_only_requests_log_reset_after_commit_verification(restart_sandbox, mismatch):
  root, calls = restart_sandbox
  logs = root / 'log'
  logs.mkdir()
  (logs / 'tmux_error.log').write_text('pre-update crash')
  script = (ROOT / 'scripts/gitpull.sh').read_text().replace('/data/openpilot', root.as_posix()).replace('/data/log', logs.as_posix())
  # Exercise the actual final commit/restart decision without making network or
  # repository changes; earlier steps have their own abort paths before here.
  (root / 'scripts/gitpull.sh').write_text(script.replace('main "$@"', 'compare_and_restart'), newline='\n')
  shutil.copyfile(ROOT / 'scripts/common_utils.sh', root / 'scripts/common_utils.sh')
  git = root.parent / 'bin/git'
  git.write_text('''#!/usr/bin/env bash
case "$*" in
  'rev-parse --abbrev-ref HEAD') printf 'test-branch\\n';;
  'rev-parse HEAD') printf 'aaaa\\n';;
  'rev-parse refs/remotes/origin/test-branch') printf '%s\\n' "${REMOTE_HASH:-aaaa}";;
  'show -s --format=%ct '*) printf '0\\n';;
  *) exit 1;;
esac
''', newline='\n')
  git.chmod(0o755)
  env = {**os.environ, 'CALL_LOG': calls.as_posix(), 'GITPULL_TMUX_CHILD': '1', 'REMOTE_HASH': 'bbbb' if mismatch else 'aaaa'}
  result = subprocess.run([BASH, '-c', 'export PATH="../bin:/usr/bin:/bin"; exec bash scripts/gitpull.sh'],
                          cwd=root, env=env, capture_output=True, text=True, timeout=10, check=False)
  assert result.returncode == (1 if mismatch else 0), result.stderr
  recorded = calls.read_text() if calls.exists() else ''
  assert ('--handoff --clear-logs' in recorded) is not mismatch
  assert (logs / 'tmux_error.log').read_text() == 'pre-update crash'
  assert (logs / 'git_pull_exit_flag').read_text().strip() == ('1' if mismatch else '0')
