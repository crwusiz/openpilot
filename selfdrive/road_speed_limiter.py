
import json
import threading
import time
import socket
from threading import Thread
from common.params import Params

from common.numpy_fast import interp

current_milli_time = lambda: int(round(time.time() * 1000))

class RoadSpeedLimiter:
  def __init__(self):
    self.json = None
    self.last_updated = 0
    self.slowing_down = False
    self.last_exception = None
    self.lock = threading.Lock()

    self.start_dist = 0

    self.longcontrol = Params().get('LongControlEnabled') == b'1'

    thread = Thread(target=self.udp_recv, args=[])
    thread.setDaemon(True)
    thread.start()

  def udp_recv(self):

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:

      try:
        sock.bind(('127.0.0.1', 843))

        while True:

          try:
            data, addr = sock.recvfrom(2048)
            json_obj = json.loads(data.decode())

            try:
              self.lock.acquire()
              self.json = json_obj
              self.last_updated = current_milli_time()
            finally:
              self.lock.release()

          except:

            try:
              self.lock.acquire()
              self.json = None
            finally:
              self.lock.release()


      except Exception as e:
        self.last_exception = e

  def get_val(self, key, default=None):

    if self.json is None:
      return default

    if key in self.json:
      return self.json[key]
    return default

  def get_max_speed(self, CS, v_cruise_kph):

    log = ""

    if current_milli_time() - self.last_updated > 1000 * 20:

      if self.last_exception is not None:
        log = str(self.last_exception)
      else:
        log = "expired: {:d}, {:d}".format(current_milli_time(), self.last_updated)

      self.slowing_down = False
      return 0, 0, 0, False, log

    try:

      road_limit_speed = self.get_val('road_limit_speed')
      is_highway = self.get_val('is_highway')

      cam_type = int(self.get_val('cam_type', 0))

      cam_limit_speed_left_dist = self.get_val('cam_limit_speed_left_dist')
      cam_limit_speed = self.get_val('cam_limit_speed')

      section_limit_speed = self.get_val('section_limit_speed')
      # section_avg_speed = self.get_val('section_avg_speed')
      section_left_dist = self.get_val('section_left_dist')
      # section_left_time = self.get_val('section_left_time')

      if is_highway is not None:
        if is_highway:
          MIN_LIMIT = 40
          MAX_LIMIT = 120
        else:
          MIN_LIMIT = 30
          MAX_LIMIT = 100
      else:
        MIN_LIMIT = 30
        MAX_LIMIT = 120

      #log = "RECV: " + str(is_highway)
      #log += ", " + str(cam_limit_speed)
      #log += ", " + str(cam_limit_speed_left_dist)
      #log += ", " + str(section_limit_speed)
      #log += ", " + str(section_left_dist)

      v_ego = CS.clu11["CF_Clu_Vanz"] / 3.6

      if cam_limit_speed_left_dist is not None and cam_limit_speed is not None and cam_limit_speed_left_dist > 0:

        diff_speed = v_ego*3.6 - cam_limit_speed

        if cam_type == 7:
          if self.longcontrol:
            sec = interp(diff_speed, [10., 30.], [15., 22.])
          else:
            sec = interp(diff_speed, [10., 30.], [16., 23.])
        else:
          if self.longcontrol:
            sec = interp(diff_speed, [10., 30.], [12., 18.])
          else:
            sec = interp(diff_speed, [10., 30.], [13., 20.])

        if MIN_LIMIT <= cam_limit_speed <= MAX_LIMIT and (self.slowing_down or cam_limit_speed_left_dist < v_ego * sec):

          if not self.slowing_down:
            self.start_dist = cam_limit_speed_left_dist * 1.2
            self.slowing_down = True
            first_started = True
          else:
            first_started = False

          base = self.start_dist / 1.2 * 0.65

          td = self.start_dist - base
          d = cam_limit_speed_left_dist - base

          if d > 0 and td > 0. and diff_speed > 0:
            pp = d / td
          else:
            pp = 0

          return cam_limit_speed + int(pp * diff_speed), cam_limit_speed, cam_limit_speed_left_dist, first_started, log

        self.slowing_down = False
        return 0, cam_limit_speed, cam_limit_speed_left_dist, False, log

      elif section_left_dist is not None and section_limit_speed is not None and section_left_dist > 0:
        if MIN_LIMIT <= section_limit_speed <= MAX_LIMIT:

          if not self.slowing_down:
            self.slowing_down = True
            first_started = True
          else:
            first_started = False

          return section_limit_speed, section_limit_speed, section_left_dist, first_started, log

        self.slowing_down = False
        return 0, section_limit_speed, section_left_dist, False, log

    except Exception as e:
      log = "Ex: " + str(e)
      pass

    self.slowing_down = False
    return 0, 0, 0, False, log


road_speed_limiter = None


def road_speed_limiter_get_max_speed(CS, v_cruise_kph):
  global road_speed_limiter
  if road_speed_limiter is None:
    road_speed_limiter = RoadSpeedLimiter()

  try:
    road_speed_limiter.lock.acquire()
    return road_speed_limiter.get_max_speed(CS, v_cruise_kph)
  finally:
    road_speed_limiter.lock.release()
