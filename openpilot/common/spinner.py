import os
import subprocess
import threading
from openpilot.common.basedir import BASEDIR


class Spinner:
  RESTART_INTERVAL = 1.0

  def __init__(self, keep_alive: bool = False):
    self.spinner_proc = None
    self._text: str | None = None
    self._closed = False
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._monitor_thread: threading.Thread | None = None
    self._start_process()
    if keep_alive:
      self._monitor_thread = threading.Thread(target=self._monitor, name="spinner-monitor", daemon=True)
      self._monitor_thread.start()

  def _start_process(self):
    try:
      self.spinner_proc = subprocess.Popen(["./spinner.py"],
                                           stdin=subprocess.PIPE,
                                           cwd=os.path.join(BASEDIR, "openpilot/system", "ui"),
                                           close_fds=True)
    except OSError as e:
      self.spinner_proc = None
      print(f"Unable to start spinner: {e}", flush=True)

  def _write_text(self):
    if self.spinner_proc is not None and self._text is not None:
      try:
        self.spinner_proc.stdin.write(self._text.encode('utf8') + b"\n")
        self.spinner_proc.stdin.flush()
      except (OSError, ValueError):
        # The monitor restores the last text if the display process exits.
        pass

  def _stop_process(self):
    proc, self.spinner_proc = self.spinner_proc, None
    if proc is not None:
      if proc.poll() is None:
        try:
          proc.kill()
        except ProcessLookupError:
          pass
      try:
        proc.communicate(timeout=2.)
      except subprocess.TimeoutExpired:
        print("WARNING: failed to kill spinner", flush=True)

  def _monitor(self):
    # Compilation can produce no progress for minutes, so don't rely on update().
    while not self._stop.wait(self.RESTART_INTERVAL):
      with self._lock:
        if self._closed:
          return
        if self.spinner_proc is not None and self.spinner_proc.poll() is None:
          continue
        print("Restarting spinner after it exited", flush=True)
        self._stop_process()
        self._start_process()
        self._write_text()

  def __enter__(self):
    return self

  def update(self, spinner_text: str):
    with self._lock:
      if not self._closed:
        self._text = spinner_text
        self._write_text()

  def update_progress(self, cur: float, total: float):
    self.update(str(round(100 * cur / total)))

  def close(self):
    self._stop.set()
    with self._lock:
      self._closed = True
      self._stop_process()
    if self._monitor_thread is not None:
      self._monitor_thread.join()

  def __del__(self):
    self.close()

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()


if __name__ == "__main__":
  import time
  with Spinner() as s:
    s.update("Spinner text")
    time.sleep(5.0)
  print("gone")
  time.sleep(5.0)
