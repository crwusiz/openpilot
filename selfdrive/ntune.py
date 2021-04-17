import os
import fcntl
import signal
import json
import numpy as np
from common.realtime import DT_CTRL

CONF_PATH = '/data/ntune/'
CONF_COMMON_FILE = '/data/ntune/common.json'
CONF_LQR_FILE = '/data/ntune/lat_lqr.json'
CONF_INDI_FILE = '/data/ntune/lat_indi.json'


class nTune():
  def __init__(self, CP=None, controller=None):

    self.invalidated = False

    self.CP = CP

    self.lqr = None
    self.indi = None

    if "LatControlLQR" in str(type(controller)):
      self.lqr = controller
      self.file = CONF_LQR_FILE
      self.lqr.A = np.array([0., 1., -0.22619643, 1.21822268]).reshape((2, 2))
      self.lqr.B = np.array([-1.92006585e-04, 3.95603032e-05]).reshape((2, 1))
      self.lqr.C = np.array([1., 0.]).reshape((1, 2))
      self.lqr.K = np.array([-110., 451.]).reshape((1, 2))
      self.lqr.L = np.array([0.33, 0.318]).reshape((2, 1))
    elif "LatControlINDI" in str(type(controller)):
      self.indi = controller
      self.file = CONF_INDI_FILE
    else:
      self.file = CONF_COMMON_FILE

    if not os.path.exists(CONF_PATH):
      os.makedirs(CONF_PATH)

    self.read()

    try:
      signal.signal(signal.SIGIO, self.handler)
      fd = os.open(CONF_PATH, os.O_RDONLY)
      fcntl.fcntl(fd, fcntl.F_SETSIG, 0)
      fcntl.fcntl(fd, fcntl.F_NOTIFY, fcntl.DN_MODIFY | fcntl.DN_CREATE | fcntl.DN_MULTISHOT)
    except Exception as ex:
      print("exception", ex)
      pass

  def handler(self, signum, frame):
    try:
      if os.path.isfile(self.file):
        with open(self.file, 'r') as f:
          self.config = json.load(f)
          if self.checkValid():
            self.write_config(self.config)

    except Exception as ex:
      print("exception", ex)
      pass

    self.invalidated = True

  def check(self):  # called by LatControlLQR.update
    if self.invalidated:
      self.invalidated = False
      self.update()

  def read(self):

    try:
      if os.path.isfile(self.file):
        with open(self.file, 'r') as f:
          self.config = json.load(f)
          if self.checkValid():
            self.write_config(self.config)

          self.update()
      else:
        self.write_default()

        with open(self.file, 'r') as f:
          self.config = json.load(f)
          if self.checkValid():
            self.write_config(self.config)
          self.update()

    except:
      return False

    return True

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
    elif self.indi is not None:
      return self.checkValidINDI()
    else:
      return self.checkValidCommon()

  def update(self):

    if self.lqr is not None:
      self.updateLQR()
    elif self.indi is not None:
      self.updateINDI()

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

    if self.checkValue("cameraOffset", -1.0, 1.0, 0.06):
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

  def checkValidINDI(self):
    updated = False

    if self.checkValue("innerLoopGain", 0.5, 10.0, 3.3):
      updated = True

    if self.checkValue("outerLoopGain", 0.5, 10.0, 2.7):
      updated = True

    if self.checkValue("timeConstant", 0.1, 5.0, 2.0):
      updated = True

    if self.checkValue("actuatorEffectiveness", 0.1, 5.0, 1.7):
      updated = True

    if self.checkValue("steerLimitTimer", 0.5, 3.0, 0.8):
      updated = True

    return updated

  def updateLQR(self):

    self.lqr.scale = float(self.config["scale"])
    self.lqr.ki = float(self.config["ki"])

    self.lqr.dc_gain = float(self.config["dcGain"])

    self.lqr.sat_limit = float(self.config["steerLimitTimer"])

    self.lqr.x_hat = np.array([[0], [0]])
    self.lqr.reset()

  def updateINDI(self):

    self.indi.RC = float(self.config["timeConstant"])
    self.indi.G = float(self.config["actuatorEffectiveness"])
    self.indi.outer_loop_gain = float(self.config["outerLoopGain"])
    self.indi.inner_loop_gain = float(self.config["innerLoopGain"])
    self.indi.alpha = 1. - DT_CTRL / (self.indi.RC + DT_CTRL)

    self.indi.sat_limit = float(self.config["steerLimitTimer"])

    self.indi.reset()

  def read_cp(self):

    self.config = {}

    try:
      if self.CP is not None:

        if self.CP.lateralTuning.which() == 'lqr' and self.lqr is not None:
          self.config["scale"] = round(self.CP.lateralTuning.lqr.scale, 2)
          self.config["ki"] = round(self.CP.lateralTuning.lqr.ki, 3)
          self.config["dcGain"] = round(self.CP.lateralTuning.lqr.dcGain, 6)
          self.config["steerLimitTimer"] = round(self.CP.steerLimitTimer, 2)
          self.config["steerMax"] = round(self.CP.steerMaxV[0], 2)

        elif self.CP.lateralTuning.which() == 'indi' and self.indi is not None:
          self.config["innerLoopGain"] = round(self.CP.lateralTuning.indi.innerLoopGain, 2)
          self.config["outerLoopGain"] = round(self.CP.lateralTuning.indi.outerLoopGain, 2)
          self.config["timeConstant"] = round(self.CP.lateralTuning.indi.timeConstant, 2)
          self.config["actuatorEffectiveness"] = round(self.CP.lateralTuning.indi.actuatorEffectiveness, 2)
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
        os.chmod(self.file, 0o764)
    except IOError:

      try:
        if not os.path.exists(CONF_PATH):
          os.makedirs(CONF_PATH)

        with open(self.file, 'w') as f:
          json.dump(conf, f, indent=2, sort_keys=False)
          os.chmod(self.file, 0o764)
      except:
        pass

ntune = None
def ntune_get(key):
  global ntune
  if ntune == None:
    ntune = nTune()

  if ntune.config == None or key not in ntune.config:
    ntune.read()

  v = ntune.config[key]

  if v is None:
    ntune.read()
    v = ntune.config[key]

  return v

def ntune_isEnabled(key):
  return ntune_get(key) > 0.5
