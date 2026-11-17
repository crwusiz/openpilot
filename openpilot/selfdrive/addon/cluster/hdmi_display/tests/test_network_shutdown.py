"""Exercise registered shutdown handlers while the real file logger holds its lock."""

from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


SHUTDOWN_SCRIPT = textwrap.dedent("""
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

from openpilot.selfdrive.addon.cluster import cluster_logging
from openpilot.selfdrive.addon.cluster.hdmi_display import network_h264

class Models:
    def __init__(self, *, defer_render_updates):
        assert defer_render_updates is True

    def close(self):
        print('models_closed=1', flush=True)

sys.modules['openpilot.selfdrive.addon.cluster.cluster_models'] = SimpleNamespace(ClusterModels=Models)
cluster_logging.LOG_FILE = sys.argv[1]
cluster_logging.CRASH_LOG_FILE = str(Path(sys.argv[1]).with_name('cluster_crash.log'))

class InterruptedFile:
    def __init__(self):
        self.handle = Path(cluster_logging.LOG_FILE).open('a', buffering=1)
        self.delivered = False

    def write(self, text):
        if not self.delivered:
            self.delivered = True
            assert cluster_logging._log_lock.locked()
            print('signal_delivered_with_log_lock=1', flush=True)
            if sys.argv[2] == 'posix_signal':
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                handler = signal.getsignal(signal.SIGTERM)
                assert callable(handler)
                handler(signal.SIGTERM, None)
            raise AssertionError('The registered handler must interrupt this write')
        return self.handle.write(text)

    def close(self):
        self.handle.close()

async def serve(_config, _models):
    try:
        cluster_logging.flog('[CLUSTER_TEST] interrupted write')
    finally:
        print('serve_cleaned=1', flush=True)

cluster_logging._log_handle = InterruptedFile()
network_h264.serve_h264 = serve
network_h264.run_h264_cluster(SimpleNamespace())
cluster_logging.close_log()
cluster_logging.close_crash_log()
print('runner_returned=1', flush=True)
""")


@pytest.mark.parametrize("delivery", [
  "registered_handler",
  pytest.param("posix_signal", marks=pytest.mark.skipif(sys.platform == "win32", reason="Windows SIGTERM terminates without Python dispatch")),
])
def test_sigterm_during_a_log_write_unwinds_the_held_lock_and_closes_models(tmp_path, delivery):
  debug_log = tmp_path / "cluster_debug.log"
  result = subprocess.run([sys.executable, "-B", "-c", SHUTDOWN_SCRIPT, str(debug_log), delivery],
                          cwd=Path(__file__).resolve().parents[6], capture_output=True, text=True, timeout=5)
  assert result.returncode == 0, result.stderr
  assert result.stdout.splitlines() == [
    "signal_delivered_with_log_lock=1", "serve_cleaned=1", "models_closed=1", "runner_returned=1",
  ]
  assert "H.264 interrupted." in debug_log.read_text()
