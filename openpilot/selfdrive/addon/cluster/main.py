import time
import signal
import numpy as np
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.addon.cluster.cluster_config import ClusterConfig
from openpilot.selfdrive.addon.cluster.cluster_usb_display import TuringUsbDisplay
from openpilot.selfdrive.addon.cluster.cluster_usb_pipeline import ClusterUsbPipeline
from openpilot.selfdrive.addon.cluster.cluster_live_camera import ClusterLiveCamera
from openpilot.selfdrive.addon.cluster.cluster_models import ClusterModels
from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer

LOG_FILE = "/data/openpilot/openpilot/selfdrive/addon/cluster/cluster_debug.log"


def flog(msg):
  try:
    with open(LOG_FILE, "a") as f:
      f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
  except:
    pass


def cluster_main():
  try:
    with open(LOG_FILE, "w") as f:
      f.write(f"=== Cluster Session Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
  except:
    pass

  cloudlog.info("Initializing Cluster Config...")
  config = ClusterConfig()

  display = TuringUsbDisplay(config)
  if hasattr(display, 'open'):
    display.open()

  pipeline = ClusterUsbPipeline(display)
  pipeline.start()

  camera = ClusterLiveCamera(config)
  models = ClusterModels()
  renderer = ClusterRenderer(config)

  fps = getattr(config, 'fps', 20)
  rk = Ratekeeper(fps, print_delay_threshold=None)

  def _stop_signal(signum, _frame):
    flog(f"[CLUSTER_MAIN] Stop signal received: {signum}")
    raise KeyboardInterrupt

  signal.signal(signal.SIGTERM, _stop_signal)
  signal.signal(signal.SIGINT, _stop_signal)

  msg = f"[CLUSTER_MAIN] Starting Main Loop at {fps} FPS..."
  print(msg)
  flog(msg)

  loop_count = 0
  perf_started = time.monotonic()
  perf_render_time = 0.0
  perf_frames = 0
  try:
    while True:
      camera.update()
      models.update()

      render_started = time.monotonic()
      frame_image = renderer.render(camera, models)
      perf_render_time += time.monotonic() - render_started
      pipeline.push(frame_image)

      loop_count += 1
      perf_frames += 1
      if loop_count % fps == 0:
        flog(
          f"[CLUSTER_HEARTBEAT] Loop: {loop_count} | Camera Ready: {camera.has_frame()} | USB Connected: {display.connected}")

      rk.keep_time()

      if perf_frames >= fps * 10:
        now = time.monotonic()
        elapsed = max(now - perf_started, 1e-6)
        flog(f"[CLUSTER_MAIN_PERF] fps={perf_frames / elapsed:.2f} | "
             f"render_avg={perf_render_time * 1000 / perf_frames:.1f}ms")
        perf_started = now
        perf_render_time = 0.0
        perf_frames = 0

  except KeyboardInterrupt:
    flog("[CLUSTER_MAIN] Interrupted by user.")
  finally:
    flog("[CLUSTER_MAIN] Closing resources...")
    try:
      if display.connected:
        display.send_image(np.zeros((config.height, config.width, 3), dtype=np.uint8))
    except Exception as e:
      flog(f"[CLUSTER_MAIN] Failed to clear display: {e}")
    if hasattr(pipeline, 'close'): pipeline.close()
    if hasattr(display, 'close'): display.close()
    if hasattr(camera, 'close'): camera.close()
    if hasattr(models, 'close'): models.close()
