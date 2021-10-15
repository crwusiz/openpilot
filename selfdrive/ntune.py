import os
import fcntl
import signal
import json
import numpy as np

from selfdrive.hardware import TICI

CONF_PATH = '/data/ntune/'
CONF_LQR_FILE = '/data/ntune/lat_lqr.json'

ntunes = {}

def file_watch_handler(signum, frame):
  global ntunes
  for ntune in ntunes.values():
    ntune.handle()

class nTune():
  def __init__(self, CP=None, controller=None, group=None):

    self.invalidated = False
    self.CP = CP
    self.lqr = None
    self.group = group
    self.config = {}

    if "LatControlLQR" in str(type(controller)):
      self.lqr = controller
      self.file = CONF_LQR_FILE
      self.lqr.A = np.array([0., 1., -0.22619643, 1.21822268]).reshape((2, 2))
      self.lqr.B = np.array([-1.92006585e-04, 3.95603032e-05]).reshape((2, 1))
      self.lqr.C = np.array([1., 0.]).reshape((1, 2))
      self.lqr.K = np.array([-110., 451.]).reshape((1, 2))
      self.lqr.L = np.array([0.33, 0.318]).reshape((2, 1))
    else:
      self.file = CONF_PATH + group + ".json"

    if not os.path.exists(CONF_PATH):
      os.makedirs(CONF_PATH)

    self.read()

    try:
      signal.signal(signal.SIGIO, file_watch_handler)
      fd = os.open(CONF_PATH, os.O_RDONLY)
      fcntl.fcntl(fd, fcntl.F_SETSIG, 0)
      fcntl.fcntl(fd, fcntl.F_NOTIFY, fcntl.DN_MODIFY | fcntl.DN_CREATE | fcntl.DN_MULTISHOT)
    except Exception as ex:
      print("exception", ex)
      pass

  def handle(self):
    try:
      if os.path.getsize(self.file) > 0:
        with open(self.file, 'r') as f:
          self.config = json.load(f)

        if self.checkValid():
          self.write_config(self.config)

        self.invalidated = True

    except:
      pass

  def check(self):  # called by LatControlLQR.update
    if self.invalidated:
      self.invalidated = False
      self.update()

  def read(self):
    success = False
    try:
      if os.path.getsize(self.file) > 0:
        with open(self.file, 'r') as f:
          self.config = json.load(f)

        if self.checkValid():
          self.write_config(self.config)
          self.update()
        success = True
    except:
      pass

    if not success:
      try:
        self.write_default()
        with open(self.file, 'r') as f:
          self.config = json.load(f)
          if self.checkValid():
            self.write_config(self.config)
          self.update()
      except:
        pass

  def checkValue(self, key, min_, max_, default_):
    updated = False

    if key not in self.config:
      self.config.update({key: default_})
      updated = True
    elif min_ > self.config[key]:
      self.config.update({key: min_})
      updated = True
    elif max_ < self.config[key]:
      self.config.update({key: max_})
      updated = True

    return updated

  def checkValid(self):

    if self.lqr is not None:
      return self.checkValidLQR()
    elif self.group == "common":
      return self.checkValidCommon()
    else:
      return self.checkValidISCC()

  def update(self):

    if self.lqr is not None:
      self.updateLQR()

  def checkValidCommon(self):
    updated = False

    if self.checkValue("useLiveSteerRatio", 0., 1., 1.):
      updated = True

    if self.checkValue("steerRatio", 10.0, 20.0, 16.5):
      updated = True

    if self.checkValue("steerActuatorDelay", 0., 0.8, 0.1):
      updated = True

    if self.checkValue("steerRateCost", 0.1, 1.5, 0.4):
      updated = True

    if self.checkValue("cameraOffset", -1.0, 1.0, -0.04 if TICI else 0.06):
      updated = True

    return updated

  def checkValidLQR(self):
    updated = False

    if self.checkValue("scale", 500.0, 5000.0, 1800.0):
      updated = True

    if self.checkValue("ki", 0.0, 0.2, 0.01):
      updated = True

    if self.checkValue("dcGain", 0.002, 0.004, 0.0028):
      updated = True

    if self.checkValue("steerLimitTimer", 0.5, 3.0, 2.5):
      updated = True

    return updated

  def checkValidISCC(self):
    updated = False

    if self.checkValue("sccGasFactor", 0.5, 1.5, 1.0):
      updated = True

    if self.checkValue("sccBrakeFactor", 0.5, 1.5, 1.0):
      updated = True

    if self.checkValue("sccCurvatureFactor", 0.5, 1.5, 0.98):
      updated = True

    if self.checkValue("longitudinalActuatorDelayLowerBound", 0.1, 1.5, 0.15):
      updated = True

    if self.checkValue("longitudinalActuatorDelayUpperBound", 0.1, 1.5, 0.15):
      updated = True

    return updated

  def updateLQR(self):

    self.lqr.scale = float(self.config["scale"])
    self.lqr.ki = float(self.config["ki"])

    self.lqr.dc_gain = float(self.config["dcGain"])

    self.lqr.sat_limit = float(self.config["steerLimitTimer"])

    self.lqr.x_hat = np.array([[0], [0]])
    self.lqr.reset()

  def read_cp(self):

    try:
      if self.CP is not None:

        if self.CP.lateralTuning.which() == 'lqr' and self.lqr is not None:
          self.config["scale"] = round(self.CP.lateralTuning.lqr.scale, 2)
          self.config["ki"] = round(self.CP.lateralTuning.lqr.ki, 3)
          self.config["dcGain"] = round(self.CP.lateralTuning.lqr.dcGain, 6)
          self.config["steerLimitTimer"] = round(self.CP.steerLimitTimer, 2)
          self.config["steerMax"] = round(self.CP.steerMaxV[0], 2)
        else:
          self.config["useLiveSteerRatio"] = 1.
          self.config["steerRatio"] = round(self.CP.steerRatio, 2)
          self.config["steerActuatorDelay"] = round(self.CP.steerActuatorDelay, 2)
          self.config["steerRateCost"] = round(self.CP.steerRateCost, 2)

    except:
      pass

  def write_default(self):

    try:
      self.read_cp()
      self.checkValid()
      self.write_config(self.config)
    except:
      pass

  def write_config(self, conf):
    try:
      with open(self.file, 'w') as f:
        json.dump(conf, f, indent=2, sort_keys=False)
        os.chmod(self.file, 0o666)
    except IOError:

      try:
        if not os.path.exists(CONF_PATH):
          os.makedirs(CONF_PATH)

        with open(self.file, 'w') as f:
          json.dump(conf, f, indent=2, sort_keys=False)
          os.chmod(self.file, 0o666)
      except:
        pass

def ntune_get(group, key):
  global ntunes
  if group not in ntunes:
    ntunes[group] = nTune(group=group)

  ntune = ntunes[group]

  if ntune.config == None or key not in ntune.config:
    ntune.read()

  v = ntune.config[key]

  if v is None:
    ntune.read()
    v = ntune.config[key]

  return v

def ntune_common_get(key):
  return ntune_get("common", key)

def ntune_common_enabled(key):
  return ntune_common_get(key) > 0.5

def ntune_scc_get(key):
  return ntune_get("scc", key)
