import os
from pathlib import Path
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

USB_TIMEOUT_MS = 2500       # 명령어 통신용 넉넉한 타임아웃 (ms)
USB_IMAGE_TIMEOUT_MS = 1000 # 기기 크래시(Errno 19)를 방지하기 위해 100ms -> 1000ms로 완화
USB_TARGET_FPS = 20
USB_CLEAR_HALT_ON_TIMEOUT = True

class ClusterConfig:
  def __init__(self):
    cloudlog.info("Loading Lightweight Cluster Configuration...")

    self.width = 1920
    self.height = 462
    self.fps = 10
    # Rendering remains at 10 FPS; the display receives the latest frame at 8 FPS.
    self.usb_fps = 8
    # JPEG uploads visibly clear the panel between frames. Use the 9.2" model's
    # H.264 playback stream for live video, with automatic JPEG fallback.
    self.use_h264_stream = True
    # The left half is a dedicated live-camera viewport. The right half remains
    # available for cluster information widgets.
    self.camera_panel_width = self.width // 2
    self.draw_model_overlay = False

    # USB 전용 설정 값
    self.usb_timeout_ms = USB_TIMEOUT_MS
    self.usb_image_timeout_ms = USB_IMAGE_TIMEOUT_MS

    self.colors = {
      "bg": (0, 0, 0),  # 기본 배경 (검정)
      "engaged": (23, 134, 68),  # 인게이지 상태 (comma 초록색)
      "disengaged": (23, 51, 73),  # 대기 상태 (어두운 파랑/회색 톤)
      "warning": (218, 48, 43),  # 경고 상태 (빨강)
      "override": (145, 155, 149),  # 사용자가 핸들을 잡았을 때 (회색)
      "lane_line": (255, 255, 255),  # 인식된 차선
      "path_active": (23, 134, 68),  # 인게이지 시 주행 경로 색상
    }

    self.BASEDIR = Path(__file__).resolve().parents[3]
    self.font_bold = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Bold.ttf")
    self.font_regular = os.path.join(self.BASEDIR, "selfdrive", "assets", "fonts", "Inter-Regular.ttf")

    self.params = Params()
    self.is_metric = self.params.get_bool("IsMetric")

    self.speed_unit = "km/h" if self.is_metric else "mph"

    cloudlog.info(f"Cluster Config Loaded: {self.width}x{self.height} @ {self.fps}fps, Unit: {self.speed_unit}")
