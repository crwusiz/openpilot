"""Compose the USB cluster and feed a bounded hardware H.264 pipeline."""

import signal
import time
import json
from collections.abc import Mapping
from time import thread_time

import cv2

from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.cluster_display_pipeline import ClusterDisplayPipeline
from openpilot.selfdrive.addon.cluster.cluster_health_guard import ClusterHealthGuard
from openpilot.selfdrive.addon.cluster.cluster_live_camera import ClusterLiveCamera
from openpilot.selfdrive.addon.cluster.cluster_models import ClusterModels
from openpilot.selfdrive.addon.cluster.cluster_policy import enforce_cluster_transport
from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer
from openpilot.selfdrive.addon.cluster.cluster_runtime_metrics import ClusterRuntimeMetrics


def run_composed_cluster(config, display, *, wait_for_frame, check_runtime):
  """Compose frames with a backend-owned clock and bounded delivery pipeline."""
  # Camera preprocessing, path drawing and encoding already overlap. Native
  # OpenCV pools would add more workers on the same three auxiliary CPU cores.
  cv2.setNumThreads(1)
  cv2.ocl.setUseOpenCL(False)

  flog(f"[CLUSTER_MODE] codec=h264 | transport={config.display_transport} | compose=c4")
  if hasattr(display, 'open'):
    flog(f"[CLUSTER_MAIN] Opening {config.display_transport}/h264 display...")
    display.open()

  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()

  health_guard = ClusterHealthGuard()
  flog("[CLUSTER_MAIN] Preparing camera, vehicle inputs and renderer...")
  # The camera already retains at most one unconsumed RGB image. Allow its
  # replacement after a render consumes it, even while the encoder is busy.
  # A short USB-capacity window can fall between 20 Hz camera arrivals and
  # otherwise leave the visible camera at its 5 Hz blocked refresh rate.
  camera = ClusterLiveCamera(config, frame_needed=lambda: not health_guard.degraded and display.connected)
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
  flog(msg)

  loop_count = 0
  perf_started = time.monotonic()
  perf_render_time = 0.0
  perf_render_cpu_time = 0.0
  perf_render_stages = dict.fromkeys(("camera_copy", "snapshot", "path", "path_cpu", "hud"), 0.0)
  perf_frames = 0
  last_camera_frame = -1
  runtime_metrics = ClusterRuntimeMetrics()
  next_policy_check = 0.0
  next_offline_frame = 0.0
  last_status_at = perf_started
  blocked_frames = 0
  health_skipped = 0
  try:
    while True:
      last_camera_frame = wait_for_frame(camera, last_camera_frame)
      now = time.monotonic()
      if now >= next_policy_check:
        next_policy_check = now + 1.0
        if not enforce_cluster_transport(config.params, config.display_transport):
          flog("[CLUSTER_MAIN] Display stopped by transport policy.")
          break
        requested_transport = config.params.get("ClusterDisplayTransport") or config.display_transport
        if requested_transport in ("network", "usb") and requested_transport != config.display_transport:
          flog(
            f"[CLUSTER_MAIN] Transport changed: {config.display_transport} -> {requested_transport}; restarting...",
          )
          break
        # Surface fatal encoder errors even when backpressure pauses rendering.
        check_runtime()

      health = models.get_health_data()
      guard_log = health_guard.update(health, now)
      if guard_log is not None:
        flog(guard_log)
      if isinstance(health, Mapping):
        for event in health.get('diagnostic_events', ()):
          flog('[CLUSTER_INPUT_DIAG] ' + json.dumps(event, ensure_ascii=True, separators=(',', ':')))
      resource_log = runtime_metrics.sample(health)
      if resource_log is not None:
        flog(resource_log)
      # Use occasional bootstrap images when disconnected.
      # Pause rendering/preprocessing when the lookahead frame is full;
      # sending one frame may still overlap preparation of the next.
      if not display.connected:
        render_ready = now >= next_offline_frame
        if render_ready:
          next_offline_frame = now + 1.0
      else:
        render_ready = pipeline.has_render_capacity()
      if render_ready and not health_guard.allow_render(now):
        health_skipped += 1
        render_ready = False
      if render_ready:
        render_started = time.monotonic()
        render_cpu_started = thread_time()
        frame_image = renderer.render(camera, models)
        perf_render_cpu_time += thread_time() - render_cpu_started
        perf_render_time += time.monotonic() - render_started
        for stage in perf_render_stages:
          perf_render_stages[stage] += renderer.last_frame_timings.get(stage, 0.0)
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
          + f"render_avg={perf_render_time * 1000 / max(perf_frames, 1):.1f}ms | "
          + f"render_cpu_avg={perf_render_cpu_time * 1000 / max(perf_frames, 1):.1f}ms | {stages} | "
          + f"target={fps} | transport_skipped={blocked_frames - health_skipped} | health_skipped={health_skipped}",
        )
        perf_started = now
        perf_render_time = 0.0
        perf_render_cpu_time = 0.0
        perf_render_stages = dict.fromkeys(perf_render_stages, 0.0)
        perf_frames = 0
        blocked_frames = 0
        health_skipped = 0

  except KeyboardInterrupt:
    flog("[CLUSTER_MAIN] Interrupted by user.")
  finally:
    flog("[CLUSTER_MAIN] Closing resources...")
    # Join workers before closing the hardware encoder and clearing the panel.
    pipeline_stopped = pipeline.close() if hasattr(pipeline, 'close') else True
    if pipeline_stopped:
      if hasattr(display, 'close'):
        display.close()
    else:
      flog("[CLUSTER_MAIN] Skipping display cleanup while transport worker is still active.")
    if hasattr(camera, 'close'):
      camera.close()
    if hasattr(models, 'close'):
      models.close()
