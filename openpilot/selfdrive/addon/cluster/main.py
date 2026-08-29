import signal
import time

import numpy as np

from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.addon.cluster.cluster_config import ClusterConfig
from openpilot.selfdrive.addon.cluster.cluster_logging import close_log, flog, initialize_log
from openpilot.selfdrive.addon.cluster.cluster_display_pipeline import ClusterDisplayPipeline
from openpilot.selfdrive.addon.cluster.cluster_live_camera import ClusterLiveCamera
from openpilot.selfdrive.addon.cluster.cluster_models import ClusterModels
from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer


def create_cluster_display(config):
  if config.display_transport == "network":
    from openpilot.selfdrive.addon.cluster.hdmi_display.network_display import ClusterNetworkDisplay
    return ClusterNetworkDisplay(config)
  from openpilot.selfdrive.addon.cluster.usb_display.turing_usb_display import TuringUsbDisplay
  return TuringUsbDisplay(config)


def cluster_main():
  initialize_log()

  cloudlog.info("Initializing Cluster Config...")
  config = ClusterConfig()

  display = create_cluster_display(config)
  if hasattr(display, 'open'):
    display.open()

  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()

  camera = ClusterLiveCamera(config)
  models = ClusterModels()
  renderer = ClusterRenderer(config)

  fps = getattr(config, 'fps', 20)
  status_interval_frames = max(1, int(getattr(config, 'status_interval_frames', fps * 10)))
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
  last_camera_frame = -1
  try:
    while True:
      # Drive rendering from new camera frames. Two independent 20 Hz loops can
      # otherwise sample the same frame twice and then skip the next one.
      last_camera_frame = camera.wait_for_frame(last_camera_frame, 1.0 / fps)
      render_started = time.monotonic()
      frame_image = renderer.render(camera, models)
      perf_render_time += time.monotonic() - render_started
      pipeline.push(frame_image)

      loop_count += 1
      perf_frames += 1
      if loop_count % status_interval_frames == 0:
        stats = pipeline.get_stats()
        flog(
          f"[CLUSTER_HEARTBEAT] Loop: {loop_count} | Camera Ready: {camera.has_frame()} | "
          + f"Transport: {config.display_transport} | Connected: {display.connected} | Sent: {stats['sent']} | "
          + f"Dropped: raw={stats['dropped_raw']}, encoded={stats['dropped_prepared']} | "
          + f"Send failures: {stats['send_failures']}",
        )

      if perf_frames >= fps * 10:
        now = time.monotonic()
        elapsed = max(now - perf_started, 1e-6)
        flog(
          f"[CLUSTER_MAIN_PERF] fps={perf_frames / elapsed:.2f} | render_avg={perf_render_time * 1000 / perf_frames:.1f}ms",
        )
        perf_started = now
        perf_render_time = 0.0
        perf_frames = 0

  except KeyboardInterrupt:
    flog("[CLUSTER_MAIN] Interrupted by user.")
  finally:
    flog("[CLUSTER_MAIN] Closing resources...")
    # Stop pending/background transport writes before sending the final black frame.
    # This prevents an older queued frame from racing with shutdown cleanup.
    pipeline_stopped = pipeline.close() if hasattr(pipeline, 'close') else True
    if pipeline_stopped:
      try:
        if display.connected:
          display.send_image(np.zeros((config.height, config.width, 3), dtype=np.uint8))
      except Exception as e:
        flog(f"[CLUSTER_MAIN] Failed to clear display: {e}")
      if hasattr(display, 'close'):
        display.close()
    else:
      flog("[CLUSTER_MAIN] Skipping display cleanup while transport worker is still active.")
    if hasattr(camera, 'close'):
      camera.close()
    if hasattr(models, 'close'):
      models.close()
    close_log()
