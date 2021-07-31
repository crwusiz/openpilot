import json
import select
import threading
import time
import socket
import fcntl
import struct
from threading import Thread
from cereal import messaging
from common.params import Params
from common.numpy_fast import interp


CAMERA_SPEED_FACTOR = 1.05

class Port:
  BROADCAST_PORT = 2899
  RECEIVE_PORT = 843
  LOCATION_PORT = 2911

class RoadLimitSpeedServer:
  def __init__(self):
    self.json_road_limit = None
    self.active = 0
    self.last_updated = 0
    self.last_updated_active = 0
    self.last_exception = None
    self.lock = threading.Lock()

    self.remote_addr = None

    broadcast = Thread(target=self.broadcast_thread, args=[])
    broadcast.setDaemon(True)
    broadcast.start()

    #gps = Thread(target=self.gps_thread, args=[])
    #gps.setDaemon(True)
    #gps.start()

  def gps_thread(self):

    sm = messaging.SubMaster(['gpsLocationExternal'], poll=['gpsLocationExternal'])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      while True:
        try:
          sm.update()
          if self.remote_addr is not None and sm.updated['gpsLocationExternal']:
            location = sm['gpsLocationExternal']
            json_location = json.dumps([
              location.latitude,
              location.longitude,
              location.altitude,
              location.speed,
              location.bearingDeg,
              location.accuracy,
              location.timestamp,
              location.source,
              location.vNED,
              location.verticalAccuracy,
              location.bearingAccuracyDeg,
              location.speedAccuracy,
            ])

            address = (self.remote_addr[0], Port.LOCATION_PORT)
            sock.sendto(json_location.encode(), address)
          else:
            time.sleep(1.)
        except Exception as e:
          print("exception", e)
          time.sleep(1.)

  def get_broadcast_address(self):
    try:
      s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      ip = fcntl.ioctl(
        s.fileno(),
        0x8919,
        struct.pack('256s', 'wlan0'.encode('utf-8'))
      )[20:24]

      return socket.inet_ntoa(ip)
    except:
      return None

  def broadcast_thread(self):

    broadcast_address = None
    frame = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while True:

          try:

            if broadcast_address is None or frame % 10 == 0:
              broadcast_address = self.get_broadcast_address()

            print('broadcast_address', broadcast_address)

            if broadcast_address is not None:
              address = (broadcast_address, Port.BROADCAST_PORT)
              sock.sendto('EON:ROAD_LIMIT_SERVICE:v1'.encode(), address)
          except:
            pass

          time.sleep(5.)
          frame += 1

      except:
        pass

  def udp_recv(self, sock):
    ret = False
    try:
      ready = select.select([sock], [], [], 1.)
      ret = bool(ready[0])
      if ret:
        data, self.remote_addr = sock.recvfrom(2048)
        json_obj = json.loads(data.decode())

        try:
          self.lock.acquire()
          try:
            if 'active' in json_obj:
              self.active = json_obj['active']
              self.last_updated_active = time.monotonic()
          except:
            pass

          if 'road_limit' in json_obj:
            self.json_road_limit = json_obj['road_limit']
            self.last_updated = time.monotonic()

        finally:
          self.lock.release()

    except:

      try:
        self.lock.acquire()
        self.json_road_limit = None
      finally:
        self.lock.release()

    return ret

  def check(self):
    now = time.monotonic()
    if now - self.last_updated > 20.:
      try:
        self.lock.acquire()
        self.json_road_limit = None
      finally:
        self.lock.release()

    if now - self.last_updated_active > 10.:
      self.active = 0

  def get_limit_val(self, key, default=None):

    if self.json_road_limit is None:
      return default

    if key in self.json_road_limit:
      return self.json_road_limit[key]
    return default




def main():
  server = RoadLimitSpeedServer()
  roadLimitSpeed = messaging.pub_sock('roadLimitSpeed')

  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      try:
        sock.bind(('0.0.0.0', Port.RECEIVE_PORT))
        sock.setblocking(False)

        while True:

          if server.udp_recv(sock):
            dat = messaging.new_message()
            dat.init('roadLimitSpeed')
            dat.roadLimitSpeed.active = server.active
            dat.roadLimitSpeed.roadLimitSpeed = server.get_limit_val("road_limit_speed", 0)
            dat.roadLimitSpeed.isHighway = server.get_limit_val("is_highway", False)
            dat.roadLimitSpeed.camType = server.get_limit_val("cam_type", 0)
            dat.roadLimitSpeed.camLimitSpeedLeftDist = server.get_limit_val("cam_limit_speed_left_dist", 0)
            dat.roadLimitSpeed.camLimitSpeed = server.get_limit_val("cam_limit_speed", 0)
            dat.roadLimitSpeed.sectionLimitSpeed = server.get_limit_val("section_limit_speed", 0)
            dat.roadLimitSpeed.sectionLeftDist = server.get_limit_val("section_left_dist", 0)
            roadLimitSpeed.send(dat.to_bytes())

          server.check()

      except Exception as e:
        server.last_exception = e

class RoadSpeedLimiter:
  def __init__(self):
    self.slowing_down = False
    self.start_dist = 0

    self.longcontrol = Params().get_bool('LongControlEnabled')
    self.sock = messaging.sub_sock("roadLimitSpeed")
    self.roadLimitSpeed = None

  def recv(self):
    try:
      dat = messaging.recv_sock(self.sock, wait=False)
      if dat is not None:
        self.roadLimitSpeed = dat.roadLimitSpeed
    except:
      pass

  def get_active(self):
    self.recv()
    if self.roadLimitSpeed is not None:
      return self.roadLimitSpeed.active
    return 0

  def get_max_speed(self, CS, v_cruise_speed):

    log = ""
    self.recv()

    if self.roadLimitSpeed is None:
      return 0, 0, 0, False, ""

    try:

      road_limit_speed = self.roadLimitSpeed.roadLimitSpeed
      is_highway = self.roadLimitSpeed.isHighway

      cam_type = int(self.roadLimitSpeed.camType)

      cam_limit_speed_left_dist = self.roadLimitSpeed.camLimitSpeedLeftDist
      cam_limit_speed = self.roadLimitSpeed.camLimitSpeed

      section_limit_speed = self.roadLimitSpeed.sectionLimitSpeed
      section_left_dist = self.roadLimitSpeed.sectionLeftDist

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

      # log = "RECV: " + str(is_highway)
      # log += ", " + str(cam_limit_speed)
      # log += ", " + str(cam_limit_speed_left_dist)
      # log += ", " + str(section_limit_speed)
      # log += ", " + str(section_left_dist)

      v_ego = CS.clu11["CF_Clu_Vanz"] / 3.6

      if cam_limit_speed_left_dist is not None and cam_limit_speed is not None and cam_limit_speed_left_dist > 0:

        diff_speed = v_ego * 3.6 - cam_limit_speed

        if self.longcontrol:
          sec = interp(diff_speed, [10., 30.], [12., 17.])
        else:
          sec = interp(diff_speed, [10., 30.], [14., 19.])

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

          if d > 0 and td > 0. and diff_speed > 0 and (section_left_dist is None or section_left_dist < 10):
            pp = d / td
          else:
            pp = 0

          return cam_limit_speed * CAMERA_SPEED_FACTOR + int(
            pp * diff_speed), cam_limit_speed, cam_limit_speed_left_dist, first_started, log

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


def road_speed_limiter_get_active():
  global road_speed_limiter
  if road_speed_limiter is None:
    road_speed_limiter = RoadSpeedLimiter()

  return road_speed_limiter.get_active()


def road_speed_limiter_get_max_speed(CS, v_cruise_speed):
  global road_speed_limiter
  if road_speed_limiter is None:
    road_speed_limiter = RoadSpeedLimiter()

  return road_speed_limiter.get_max_speed(CS, v_cruise_speed)


if __name__ == "__main__":
  main()