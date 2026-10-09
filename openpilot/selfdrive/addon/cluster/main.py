import signal
import time

import cv2
import numpy as np

from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.addon.cluster.cluster_config import ClusterConfig
from openpilot.selfdrive.addon.cluster.cluster_logging import close_log, flog, initialize_log
from openpilot.selfdrive.addon.cluster.cluster_display_pipeline import ClusterDisplayPipeline
from openpilot.selfdrive.addon.cluster.cluster_frame_clock import ClusterFrameClock
from openpilot.selfdrive.addon.cluster.cluster_live_camera import ClusterLiveCamera
from openpilot.selfdrive.addon.cluster.cluster_models import ClusterModels
from openpilot.selfdrive.addon.cluster.cluster_policy import enforce_cluster_transport
from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer
from openpilot.selfdrive.addon.cluster.cluster_runtime_metrics import ClusterRuntimeMetrics


def create_cluster_display(config):
  if config.display_transport == "network":
    from openpilot.selfdrive.addon.cluster.hdmi_display.network_display import ClusterNetworkDisplay
    return ClusterNetworkDisplay(config)
  from openpilot.selfdrive.addon.cluster.usb_display.turing_usb_display import TuringUsbDisplay
  return TuringUsbDisplay(config)


def cluster_main():
  initialize_log()
  # Camera preprocessing, path drawing and encoding already overlap. Native
  # OpenCV pools would add more workers on the same three auxiliary CPU cores.
  cv2.setNumThreads(1)
  cv2.ocl.setUseOpenCL(False)

  cloudlog.info("Initializing Cluster Config...")
  config = ClusterConfig()

  if not enforce_cluster_transport(config.params, config.display_transport):
    close_log()
    return

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
  perf_render_stages = dict.fromkeys(("camera_copy", "snapshot", "path", "hud"), 0.0)
  perf_frames = 0
  last_camera_frame = -1
  frame_clock = ClusterFrameClock(fps)
  runtime_metrics = ClusterRuntimeMetrics()
  next_policy_check = 0.0
  next_offline_frame = 0.0
  next_screen_off_frame = 0.0
  last_status_at = perf_started
  blocked_frames = 0
  try:
    while True:
      if config.display_transport == "network":
        frame_clock.wait()
      else:
        last_camera_frame = camera.wait_for_frame(last_camera_frame, 1.0 / fps)
      now = time.monotonic()
      if now >= next_policy_check:
        next_policy_check = now + 1.0
        if not enforce_cluster_transport(config.params, config.display_transport):
          flog("[CLUSTER_MAIN] USB cluster stopped for Chestnut eGPU.")
          break
        requested_transport = config.params.get("ClusterDisplayTransport") or config.display_transport
        if requested_transport in ("network", "usb") and requested_transport != config.display_transport:
          flog(
            f"[CLUSTER_MAIN] Transport changed: {config.display_transport} -> {requested_transport}; restarting...",
          )
          break

      health = models.get_health_data()
      resource_log = runtime_metrics.sample(health)
      if resource_log is not None:
        flog(resource_log)
      # A missing Pi needs only an occasional fresh connection bootstrap image.
      # When connected, leave rendering/encoding idle while transport is full.
      render_ready = True
      if config.display_transport == "network":
        if not display.connected:
          render_ready = now >= next_offline_frame
          if render_ready:
            next_offline_frame = now + 1.0
        elif getattr(display, "screen_off", False):
          render_ready = now >= next_screen_off_frame and pipeline.has_render_capacity()
          if render_ready:
            next_screen_off_frame = now + 1.0 / getattr(config, "network_screen_off_fps", 5)
        else:
          render_ready = pipeline.has_render_capacity()
      if render_ready:
        render_started = time.monotonic()
        frame_image = renderer.render(camera, models)
        perf_render_time += time.monotonic() - render_started
        for stage in perf_render_stages:
          perf_render_stages[stage] += renderer.last_frame_timings[stage]
        pipeline.push(frame_image, created_at=render_started)
        loop_count += 1
        perf_frames += 1
      else:
        blocked_frames += 1
      now = time.monotonic()
      if now - last_status_at >= status_interval_frames / fps:
        stats = pipeline.get_stats()
        flog(
          f"[CLUSTER_HEARTBEAT] Loop: {loop_count} | Camera Ready: {camera.has_frame()} | "
          + f"Transport: {config.display_transport} | Connected: {display.connected} | Sent: {stats['sent']} | "
          + f"Dropped: raw={stats['dropped_raw']}, encoded={stats['dropped_prepared']} | "
          + f"Stale drops: {stats['dropped_stale']} | "
          + f"Send failures: {stats['send_failures']}",
        )
        last_status_at = now

      if now - perf_started >= 10.0:
        elapsed = max(now - perf_started, 1e-6)
        stages = " | ".join(f"{stage}_avg={duration * 1000 / max(perf_frames, 1):.1f}ms"
                            for stage, duration in perf_render_stages.items())
        flog(
          f"[CLUSTER_MAIN_PERF] fps={perf_frames / elapsed:.2f} | "
          + f"render_avg={perf_render_time * 1000 / max(perf_frames, 1):.1f}ms | {stages} | "
          + f"target={fps} | transport_skipped={blocked_frames}",
        )
        perf_started = now
        perf_render_time = 0.0
        perf_render_stages = dict.fromkeys(perf_render_stages, 0.0)
        perf_frames = 0
        blocked_frames = 0

  except KeyboardInterrupt:
    flog("[CLUSTER_MAIN] Interrupted by user.")
  finally:
    flog("[CLUSTER_MAIN] Closing resources...")
    # Stop pending/background transport writes before sending the final black frame.
    # This prevents an older queued frame from racing with shutdown cleanup.
    pipeline_stopped = pipeline.close() if hasattr(pipeline, 'close') else True
    if pipeline_stopped:
      try:
        if display.connected and enforce_cluster_transport(config.params, config.display_transport):
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
