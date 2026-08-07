import os
from pathlib import Path
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog


class ClusterConfig:
  def __init__(self):
    cloudlog.info("Loading Lightweight Cluster Configuration...")

    self.width = 1920
    self.height = 462
    self.fps = 15

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
