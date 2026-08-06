from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog


class ClusterModels:
  def __init__(self):
    cloudlog.info("Initializing ClusterModels (Lightweight)...")

    self.sm = messaging.SubMaster(['modelV2', 'carState', 'selfdriveState'])

    self.v_ego = 0.0  # m/s 단위 속도
    self.enabled = False  # 인게이지 여부
    self.left_blinker = False
    self.right_blinker = False

    self.model_valid = False
    self.path_x = []
    self.path_y = []
    self.left_lane_x = []
    self.left_lane_y = []
    self.right_lane_x = []
    self.right_lane_y = []

  def update(self):
    self.sm.update(0)

    if self.sm.updated['carState']:
      cs = self.sm['carState']
      self.v_ego = cs.vEgo
      self.left_blinker = cs.leftBlinker
      self.right_blinker = cs.rightBlinker

    if self.sm.updated['selfdriveState']:
      ss = self.sm['selfdriveState']
      self.enabled = ss.enabled

    if self.sm.updated['modelV2']:
      md = self.sm['modelV2']

      if len(md.position.x) > 0:
        self.path_x = list(md.position.x)
        self.path_y = list(md.position.y)
        self.model_valid = True
      else:
        self.model_valid = False

      if len(md.laneLines) == 4:
        self.left_lane_x = list(md.laneLines[1].x)
        self.left_lane_y = list(md.laneLines[1].y)

        self.right_lane_x = list(md.laneLines[2].x)
        self.right_lane_y = list(md.laneLines[2].y)

  def is_valid(self):
    return self.model_valid

  def get_hud_data(self):
    return {
      "v_ego": self.v_ego,
      "enabled": self.enabled,
      "left_blinker": self.left_blinker,
      "right_blinker": self.right_blinker
    }

  def get_path_data(self):
    return {
      "path_x": self.path_x,
      "path_y": self.path_y,
      "left_lane_x": self.left_lane_x,
      "left_lane_y": self.left_lane_y,
      "right_lane_x": self.right_lane_x,
      "right_lane_y": self.right_lane_y
    }
