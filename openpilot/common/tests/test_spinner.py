import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from openpilot.common.spinner import Spinner


def wait_until(predicate, timeout=5.0):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.01)
  assert predicate(), "Spinner process did not reach the expected state"


class SpinnerProcesses:
  def __init__(self, popen, root):
    self.popen = popen
    self.root = root
    self.children = []
    self.attempts = 0
    self.failures = 0

  def start(self, *args, **kwargs):
    self.attempts += 1
    if self.failures:
      self.failures -= 1
      raise OSError("Display process temporarily unavailable")
    transcript = self.root / f"spinner-{len(self.children)}.txt"
    script = """import pathlib, sys
with pathlib.Path(sys.argv[1]).open('w', encoding='utf-8') as output:
  for line in sys.stdin:
    output.write(line)
    output.flush()
"""
    child = self.popen([sys.executable, "-u", "-c", script, str(transcript)],
                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    self.children.append((child, transcript))
    return child

  def received(self, index, text):
    try:
      return self.children[index][1].read_text(encoding="utf-8") == text + "\n"
    except (IndexError, OSError):
      return False

  def cleanup(self):
    for child, _ in self.children:
      if child.poll() is None:
        child.kill()
      child.wait(timeout=5)
      if child.stdin is not None and not child.stdin.closed:
        child.stdin.close()


@pytest.fixture
def spinner_processes(monkeypatch, tmp_path):
  processes = SpinnerProcesses(subprocess.Popen, tmp_path)
  monkeypatch.setattr("openpilot.common.spinner.subprocess.Popen", processes.start)
  monkeypatch.setattr(Spinner, "RESTART_INTERVAL", 0.05)
  try:
    yield processes
  finally:
    processes.cleanup()


def test_restarts_repeatedly_without_progress_updates_and_restores_text(spinner_processes):
  text = "Compiling model - 37%"
  with Spinner(keep_alive=True) as spinner:
    spinner.update(text)
    wait_until(lambda: spinner_processes.received(0, text))
    for index in range(2):
      child = spinner_processes.children[index][0]
      child.kill()
      child.wait(timeout=5)
      # No update() call follows the crash: the monitor must work while a
      # compiler is silent, and each replacement must retain the last status.
      wait_until(lambda index=index: spinner_processes.received(index + 1, text))
      assert child.stdin.closed
      assert spinner.spinner_proc is spinner_processes.children[index + 1][0]


def test_retries_initial_spawn_failure_without_update(spinner_processes):
  spinner_processes.failures = 1
  with Spinner(keep_alive=True) as spinner:
    wait_until(lambda: len(spinner_processes.children) == 1 and spinner.spinner_proc is not None)
    assert spinner_processes.attempts == 2
    assert spinner.spinner_proc is spinner_processes.children[0][0]
    assert spinner.spinner_proc.poll() is None


def test_close_stops_monitor_and_does_not_respawn(spinner_processes):
  spinner = Spinner(keep_alive=True)
  try:
    child = spinner_processes.children[0][0]
    child.kill()
    child.wait(timeout=5)
    spinner.close()
    attempts = spinner_processes.attempts
    spinner.update("Already closed")
    spinner.close()
    time.sleep(3 * Spinner.RESTART_INTERVAL)
    assert spinner_processes.attempts == attempts
    assert spinner.spinner_proc is None
    assert not spinner._monitor_thread.is_alive()
    assert all(proc.poll() is not None for proc, _ in spinner_processes.children)
  finally:
    spinner.close()


def test_default_spinner_does_not_restart(spinner_processes):
  with Spinner() as spinner:
    spinner.update("Building")
    wait_until(lambda: spinner_processes.received(0, "Building"))
    child = spinner_processes.children[0][0]
    child.kill()
    child.wait(timeout=5)
    spinner.update_progress(1, 2)
    time.sleep(3 * Spinner.RESTART_INTERVAL)
    assert spinner_processes.attempts == 1
    assert spinner._monitor_thread is None


@pytest.mark.parametrize("operation", ["write", "flush"])
@pytest.mark.parametrize("error", [BrokenPipeError, ValueError])
def test_update_tolerates_closed_pipe(monkeypatch, operation, error):
  child = Mock()
  child.poll.return_value = None
  getattr(child.stdin, operation).side_effect = error("Display process closed its input")
  monkeypatch.setattr("openpilot.common.spinner.subprocess.Popen", lambda *args, **kwargs: child)
  with Spinner() as spinner:
    spinner.update("Building")
  child.kill.assert_called_once()
  child.communicate.assert_called_once_with(timeout=2.)
