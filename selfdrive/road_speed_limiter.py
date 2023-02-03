#!/usr/bin/env python3
import json
import os

import select
import threading
import time
import socket
import fcntl
import struct
from threading import Thread
from cereal import messaging
from common.numpy_fast import clip, interp
from common.realtime import sec_since_boot, Ratekeeper
from common.conversions import Conversions as CV

CAMERA_SPEED_FACTOR = 1.05


class Port:
  BROADCAST_PORT = 2899
  RECEIVE_PORT = 2843
  LOCATION_PORT = BROADCAST_PORT


class RoadLimitSpeedServer:
  def __init__(self):
    self.lock = threading.Lock()
    self.json_road_limit = None
    self.last_exception = None
    self.remote_addr = None
    self.active = 0
    self.last_updated = 0
    self.last_updated_active = 0

    broadcast = Thread(target=self.broadcast_thread, args=[])
    broadcast.daemon = True
    broadcast.start()

    self.remote_gps_addr = None
    self.location = None
    self.gps_sm = messaging.SubMaster(['gpsLocationExternal'], poll=['gpsLocationExternal'])
    self.gps_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.gps_event = threading.Event()

    #gps_thread = Thread(target=self.gps_thread, args=[])
    #gps_thread.daemon = True
    #gps_thread.start()


  def gps_thread(self):
    rk = Ratekeeper(3.0, print_delay_threshold=None)
    while True:
      self.gps_timer()
      rk.keep_time()


  def gps_timer(self):
    try:
      if self.remote_gps_addr is not None:
        self.gps_sm.update(0)
        if self.gps_sm.updated['gpsLocationExternal']:
          self.location = self.gps_sm['gpsLocationExternal']

        if self.location is not None:
          json_location = json.dumps({"location": [
            self.location.latitude,
            self.location.longitude,
            self.location.altitude,
            self.location.speed,
            self.location.bearingDeg,
            self.location.accuracy,
            self.location.unixTimestampMillis,
            # self.location.source,
            # self.location.vNED,
            self.location.verticalAccuracy,
            self.location.bearingAccuracyDeg,
            self.location.speedAccuracy,
          ]})

          address = (self.remote_gps_addr[0], Port.LOCATION_PORT)
          self.gps_socket.sendto(json_location.encode(), address)
    except:
      self.remote_gps_addr = None


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
            #print('broadcast_address', broadcast_address)
            if broadcast_address is not None:
              address = (broadcast_address, Port.BROADCAST_PORT)
              sock.sendto('EON:ROAD_LIMIT_SERVICE:v1'.encode(), address)
          except:
            pass

          time.sleep(5.)
          frame += 1
      except:
        pass


  def send_sdp(self, sock):
    try:
      sock.sendto('EON:ROAD_LIMIT_SERVICE:v1'.encode(), (self.remote_addr[0], Port.BROADCAST_PORT))
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
        if 'cmd' in json_obj:
          try:
            os.system(json_obj['cmd'])
            ret = False
          except:
            pass

        #if 'request_gps' in json_obj:
        #  try:
        #    if json_obj['request_gps'] == 1:
        #      self.remote_gps_addr = self.remote_addr
        #    else:
        #      self.remote_gps_addr = None
        #    ret = False
        #  except:
        #    pass

        if 'echo' in json_obj:
          try:
            echo = json.dumps(json_obj["echo"])
            sock.sendto(echo.encode(), (self.remote_addr[0], Port.BROADCAST_PORT))
            ret = False
          except:
            pass

        try:
          self.lock.acquire()
          try:
            if 'active' in json_obj:
              self.active = json_obj['active']
              self.last_updated_active = sec_since_boot()
          except:
            pass

          if 'road_limit' in json_obj:
            self.json_road_limit = json_obj['road_limit']
            self.last_updated = sec_since_boot()
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
    now = sec_since_boot()
    if now - self.last_updated > 6.:
      try:
        self.lock.acquire()
        self.json_road_limit = None
      finally:
        self.lock.release()
    if now - self.last_updated_active > 6.:
      self.active = 0


  def get_limit_val(self, key, default=None):
    return self.get_json_val(self.json_road_limit, key, default)


  def get_json_val(self, json, key, default=None):
    try:
      if json is None:
        return default
      if key in json:
        return json[key]
    except:
      pass
    return default


def main():
  server = RoadLimitSpeedServer()
  roadLimitSpeed = messaging.pub_sock('roadLimitSpeed')
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    try:
      sock.bind(('0.0.0.0', Port.RECEIVE_PORT))
      sock.setblocking(False)
      while True:
        server.udp_recv(sock)
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
        dat.roadLimitSpeed.sectionAvgSpeed = server.get_limit_val("section_avg_speed", 0)
        dat.roadLimitSpeed.sectionLeftTime = server.get_limit_val("section_left_time", 0)
        dat.roadLimitSpeed.sectionAdjustSpeed = server.get_limit_val("section_adjust_speed", False)
        dat.roadLimitSpeed.camSpeedFactor = server.get_limit_val("cam_speed_factor", CAMERA_SPEED_FACTOR)
        roadLimitSpeed.send(dat.to_bytes())
        server.send_sdp(sock)
        server.check()
    except Exception as e:
      server.last_exception = e


class RoadSpeedLimiter:
  def __init__(self):
    self.slowing_down = False
    self.started_dist = 0
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


  def get_max_speed(self, cluster_speed, is_metric):
    self.recv()
    if self.roadLimitSpeed is None:
      return 0, 0, 0, False
    try:
      road_limit_speed = self.roadLimitSpeed.roadLimitSpeed
      is_highway = self.roadLimitSpeed.isHighway
      cam_type = int(self.roadLimitSpeed.camType)
      cam_limit_speed_left_dist = self.roadLimitSpeed.camLimitSpeedLeftDist
      cam_limit_speed = self.roadLimitSpeed.camLimitSpeed
      section_limit_speed = self.roadLimitSpeed.sectionLimitSpeed
      section_left_dist = self.roadLimitSpeed.sectionLeftDist
      section_avg_speed = self.roadLimitSpeed.sectionAvgSpeed
      section_left_time = self.roadLimitSpeed.sectionLeftTime
      section_adjust_speed = self.roadLimitSpeed.sectionAdjustSpeed
      camSpeedFactor = clip(self.roadLimitSpeed.camSpeedFactor, 1.0, 1.1)

      if is_highway is not None:
        if is_highway:
          MIN_LIMIT = 40
          MAX_LIMIT = 120
        else:
          MIN_LIMIT = 20
          MAX_LIMIT = 100
      else:
        MIN_LIMIT = 20
        MAX_LIMIT = 120

      if cam_type == 22:  # speed bump
        MIN_LIMIT = 10

      if cam_limit_speed_left_dist is not None and cam_limit_speed is not None and cam_limit_speed_left_dist > 0:
        v_ego = cluster_speed * (CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS)
        diff_speed = cluster_speed - (cam_limit_speed * camSpeedFactor)
        #cam_limit_speed_ms = cam_limit_speed * (CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS)

        starting_dist = v_ego * 30.

        if cam_type == 22:
          safe_dist = v_ego * 3.
        else:
          safe_dist = v_ego * 6.

        if MIN_LIMIT <= cam_limit_speed <= MAX_LIMIT and (self.slowing_down or cam_limit_speed_left_dist < starting_dist):
          if not self.slowing_down:
            self.started_dist = cam_limit_speed_left_dist
            self.slowing_down = True
            first_started = True
          else:
            first_started = False

          td = self.started_dist - safe_dist
          d = cam_limit_speed_left_dist - safe_dist

          if d > 0. and td > 0. and diff_speed > 0. and (section_left_dist is None or section_left_dist < 10 or cam_type == 2):
            pp = (d / td) ** 0.6
          else:
            pp = 0

          return cam_limit_speed * camSpeedFactor + int(pp * diff_speed), cam_limit_speed, cam_limit_speed_left_dist, first_started

        self.slowing_down = False
        return 0, cam_limit_speed, cam_limit_speed_left_dist, False

      elif section_left_dist is not None and section_limit_speed is not None and section_left_dist > 0:
        if MIN_LIMIT <= section_limit_speed <= MAX_LIMIT:
          if not self.slowing_down:
            self.slowing_down = True
            first_started = True
          else:
            first_started = False

          speed_diff = 0
          if section_adjust_speed is not None and section_adjust_speed:
            speed_diff = (section_limit_speed - section_avg_speed) / 2.
            speed_diff *= interp(section_left_dist, [500, 1000], [0., 1.])

          return section_limit_speed * camSpeedFactor + speed_diff, section_limit_speed, section_left_dist, first_started

        self.slowing_down = False
        return 0, section_limit_speed, section_left_dist, False

    except:
      pass

    self.slowing_down = False
    return 0, 0, 0, False

road_speed_limiter = None


def road_speed_limiter_get_active():
  global road_speed_limiter
  if road_speed_limiter is None:
    road_speed_limiter = RoadSpeedLimiter()
  return road_speed_limiter.get_active()


def road_speed_limiter_get_max_speed(cluster_speed, is_metric):
  global road_speed_limiter
  if road_speed_limiter is None:
    road_speed_limiter = RoadSpeedLimiter()
  return road_speed_limiter.get_max_speed(cluster_speed, is_metric)


def get_road_speed_limiter():
  global road_speed_limiter
  if road_speed_limiter is None:
    road_speed_limiter = RoadSpeedLimiter()
  return road_speed_limiter


if __name__ == "__main__":
  main()