from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.addon.cluster.cluster_config import ClusterConfig
from openpilot.selfdrive.addon.cluster.cluster_usb_display import TuringUsbDisplay
from openpilot.selfdrive.addon.cluster.cluster_usb_pipeline import ClusterUsbPipeline
from openpilot.selfdrive.addon.cluster.cluster_live_camera import ClusterLiveCamera
from openpilot.selfdrive.addon.cluster.cluster_models import ClusterModels
from openpilot.selfdrive.addon.cluster.cluster_renderer import ClusterRenderer


def cluster_main():
  cloudlog.info("Initializing Cluster Config...")
  config = ClusterConfig()

  cloudlog.info("Initializing Turing USB Display...")
  display = TuringUsbDisplay(config)
  if hasattr(display, 'open'):
    display.open()

  pipeline = ClusterUsbPipeline(display)
  pipeline.start()

  cloudlog.info("Initializing Camera & Models...")
  camera = ClusterLiveCamera(config)
  models = ClusterModels()

  cloudlog.info("Initializing Renderer...")
  renderer = ClusterRenderer(config)

  fps = getattr(config, 'fps', 20)
  rk = Ratekeeper(fps, print_delay_threshold=None)

  cloudlog.info(f"Starting Main Loop at {fps} FPS...")

  try:
    while True:
      camera.update()
      models.update()

      frame_image = renderer.render(camera, models)

      pipeline.push(frame_image)

      rk.keep_time()

  except KeyboardInterrupt:
    cloudlog.info("Cluster main loop stopped by user.")
  finally:
    cloudlog.info("Closing Cluster resources...")
    if hasattr(pipeline, 'close'):
      pipeline.close()
    if hasattr(display, 'close'):
      display.close()
    if hasattr(camera, 'close'):
      camera.close()
