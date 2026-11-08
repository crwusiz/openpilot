import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
GIT_BASH = Path('C:/Program Files/Git/bin/bash.exe')
BASH = str(GIT_BASH) if os.name == 'nt' and GIT_BASH.exists() else shutil.which('bash')
LOCAL_COMMIT = 'a' * 40
REMOTE_COMMIT = 'b' * 40


@pytest.fixture
def commit_sandbox(tmp_path):
  if not BASH:
    pytest.skip('Bash is required to run the commit comparison sandbox')
  root = tmp_path / 'checkout with spaces'
  scripts = root / 'scripts'
  scripts.mkdir(parents=True)
  params = root / 'params'
  params.mkdir()
  flag = root / 'log/commit_check_exit_flag'
  script = (ROOT / 'scripts/commit_compare.sh').read_text()
  script = script.replace('/data/params/d/CommitCompare', f'"{(params / "CommitCompare").as_posix()}"')
  script = script.replace('/data/log/commit_check_exit_flag', f'"{flag.as_posix()}"')
  (scripts / 'commit_compare.sh').write_text(script, newline='\n')
  bin_dir = tmp_path / 'bin'
  bin_dir.mkdir()
  git = bin_dir / 'git'
  git.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$*" in
  'symbolic-ref --quiet --short HEAD')
    [ "${BRANCH_EXIT:-0}" = '0' ] || exit "$BRANCH_EXIT"
    printf '%s\\n' 'feature/test';;
  'rev-parse --verify HEAD')
    [ "${LOCAL_EXIT:-0}" = '0' ] || exit "$LOCAL_EXIT"
    printf '%s\\n' "$LOCAL_COMMIT";;
  'ls-remote --exit-code --refs origin refs/heads/feature/test')
    [ "${REMOTE_EXIT:-0}" = '0' ] || exit "$REMOTE_EXIT"
    [ "${REMOTE_SLEEP:-0}" = '0' ] || sleep "$REMOTE_SLEEP"
    if [ "${REMOTE_MODE:-normal}" = 'suffix' ]; then
      printf '%s\\t%s\\n' "$LOCAL_COMMIT" 'refs/tags/refs/heads/feature/test'
    else
      printf '%s\\t%s\\n' "$LOCAL_COMMIT" 'refs/tags/feature/test'
      printf '%s\\t%s\\n' "$REMOTE_COMMIT" 'refs/heads/feature/test'
      printf '%s\\t%s\\n' "$LOCAL_COMMIT" 'refs/heads/prefix/feature/test'
    fi;;
  *) exit 99;;
esac
''', newline='\n')
  git.chmod(0o755)
  return root, params / 'CommitCompare', flag


def run_commit_check(sandbox, shell='sh', **extra_env):
  root, _, _ = sandbox
  calls = root / 'git-calls'
  env = {**os.environ, 'CALL_LOG': calls.as_posix(), 'LOCAL_COMMIT': LOCAL_COMMIT,
         'REMOTE_COMMIT': REMOTE_COMMIT, **extra_env}
  result = subprocess.run([BASH, '-c', 'export PATH="../bin:/usr/bin:/bin"; exec "$1" scripts/commit_compare.sh', 'test', shell],
                          cwd=root, env=env, capture_output=True, text=True, timeout=5, check=False)
  return result, calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize('shell', ['sh', 'bash'])
@pytest.mark.parametrize('same', [True, False])
def test_compares_exact_branch_and_writes_completion(commit_sandbox, shell, same):
  remote = LOCAL_COMMIT if same else REMOTE_COMMIT
  result, calls = run_commit_check(commit_sandbox, shell, REMOTE_COMMIT=remote)
  _, params, flag = commit_sandbox
  assert result.returncode == 0, result.stderr
  operator = '==' if same else '!='
  assert params.read_text().strip() == f'"{LOCAL_COMMIT}" {operator} "{remote}"'
  assert flag.read_text().strip() == '0'
  assert calls[-1] == 'ls-remote --exit-code --refs origin refs/heads/feature/test'


def test_compares_full_hashes_with_identical_short_prefix(commit_sandbox):
  remote = LOCAL_COMMIT[:7] + 'b' * 33
  result, _ = run_commit_check(commit_sandbox, REMOTE_COMMIT=remote)
  _, params, _ = commit_sandbox
  assert result.returncode == 0, result.stderr
  assert params.read_text().strip() == f'"{LOCAL_COMMIT}" != "{remote}"'


@pytest.mark.parametrize('extra_env, status', [({'BRANCH_EXIT': '1'}, 1), ({'LOCAL_EXIT': '128'}, 128),
                                             ({'REMOTE_EXIT': '128'}, 128), ({'REMOTE_EXIT': '2'}, 2),
                                             ({'REMOTE_MODE': 'suffix'}, 1), ({'LOCAL_COMMIT': ''}, 1), ({'REMOTE_COMMIT': ''}, 1)])
def test_failures_clear_stale_result_and_write_exit_status(commit_sandbox, extra_env, status):
  _, params, flag = commit_sandbox
  params.write_text(f'"{LOCAL_COMMIT}" != "{REMOTE_COMMIT}"')
  result, _ = run_commit_check(commit_sandbox, **extra_env)
  assert result.returncode == status, result.stderr
  assert flag.read_text().strip() == str(status)
  assert not params.exists()


def test_remote_timeout_writes_failure_and_clears_stale_result(commit_sandbox):
  root, params, flag = commit_sandbox
  script = root / 'scripts/commit_compare.sh'
  script.write_text(script.read_text().replace('timeout 10s', 'timeout 0.1s'), newline='\n')
  params.write_text('stale result')
  result, _ = run_commit_check(commit_sandbox, REMOTE_SLEEP='2')
  assert result.returncode == 124, result.stderr
  assert flag.read_text().strip() == '124'
  assert not params.exists()


def test_result_write_failure_is_reported(commit_sandbox):
  _, params, flag = commit_sandbox
  params.parent.rmdir()
  result, _ = run_commit_check(commit_sandbox)
  assert result.returncode != 0
  assert flag.read_text().strip() == str(result.returncode)
  assert not params.exists()
