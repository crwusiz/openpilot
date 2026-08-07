import time
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
  # 파일 초기화
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

  msg = f"[CLUSTER_MAIN] Starting Main Loop at {fps} FPS..."
  print(msg)
  flog(msg)

  loop_count = 0
  try:
    while True:
      camera.update()
      models.update()

      frame_image = renderer.render(camera, models)
      pipeline.push(frame_image)

      # 1초마다(fps*1 프레임) 터미널 대신 파일에 생존 신고
      loop_count += 1
      if loop_count % fps == 0:
        flog(
          f"[CLUSTER_HEARTBEAT] Loop: {loop_count} | Camera Ready: {camera.has_frame()} | USB Connected: {display.connected}")

      rk.keep_time()

  except KeyboardInterrupt:
    flog("[CLUSTER_MAIN] Interrupted by user.")
  finally:
    flog("[CLUSTER_MAIN] Closing resources...")
    if hasattr(pipeline, 'close'): pipeline.close()
    if hasattr(display, 'close'): display.close()
    if hasattr(camera, 'close'): camera.close()
