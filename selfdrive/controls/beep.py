#!/usr/bin/env python3
import subprocess
import time
import threading
import argparse

from cereal import car, messaging
from openpilot.common.realtime import Ratekeeper

AudibleAlert = car.CarControl.HUDControl.AudibleAlert

class Beepd:
  def __init__(self):
    self.current_alert = AudibleAlert.none
    self.enable_gpio()
    self.startup_beep()

  def enable_gpio(self):
    # 尝试 export，忽略已 export 的错误
    try:
      subprocess.run("echo 42 | sudo tee /sys/class/gpio/export",
                     shell=True,
                     stderr=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,
                     encoding='utf8')
    except Exception:
      pass
    subprocess.run("echo \"out\" | sudo tee /sys/class/gpio/gpio42/direction",
                   shell=True,
                   stderr=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL,
                   encoding='utf8')

  def _beep(self, on):
    val = "1" if on else "0"
    subprocess.run(f"echo \"{val}\" | sudo tee /sys/class/gpio/gpio42/value",
                   shell=True,
                   stderr=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL,
                   encoding='utf8')

  def _single_beep_for_duration(self, duration: float):
    self._beep(True)
    time.sleep(duration)
    self._beep(False)

  def _dual_beep_for_duration(self, beep_duration: float, inter_delay: float = 0.01):
    for _ in range(2):
      self._single_beep_for_duration(beep_duration)
      time.sleep(inter_delay)

  def _triple_beep_for_duration(self, beep_duration: float, inter_delay: float = 0.01):
    for _ in range(3):
      self._single_beep_for_duration(beep_duration)
      time.sleep(inter_delay)

  def engage(self):
    self._single_beep_for_duration(0.05)

  def disengage(self):
    self._dual_beep_for_duration(0.01)

  def warning(self):
    self._triple_beep_for_duration(0.01)

  def startup_beep(self):
    self._single_beep_for_duration(0.1)

  def ding(self):
    self._single_beep_for_duration(0.02)

  def dong(self):
    self._single_beep_for_duration(0.03)

  def beep(self):
    self._single_beep_for_duration(0.04)

  def dispatch_beep(self, func):
    threading.Thread(target=func, daemon=True).start()

  def update_alert(self, new_alert):
    if new_alert != self.current_alert:
      self.current_alert = new_alert
      print(f"[BEEP] New alert: {new_alert}")
      if new_alert == AudibleAlert.engage:
        self.dispatch_beep(self.engage)
      elif new_alert == AudibleAlert.disengage:
        self.dispatch_beep(self.disengage)
      elif new_alert in [AudibleAlert.refuse, AudibleAlert.prompt, AudibleAlert.warningImmediate,AudibleAlert.warningSoft]:
        self.dispatch_beep(self.warning)
      elif new_alert == AudibleAlert.ready:
        self.dispatch_beep(self.startup_beep)
      elif new_alert == AudibleAlert.ding:
        self.dispatch_beep(self.ding)
      elif new_alert == AudibleAlert.dong:
        self.dispatch_beep(self.dong)
      elif new_alert == AudibleAlert.beep:
        self.dispatch_beep(self.beep)

  def get_audible_alert(self, sm):
    if sm.updated['selfdriveState']:
      new_alert = sm['selfdriveState'].alertSound.raw
      self.update_alert(new_alert)

  def test_beepd_thread(self):
    frame = 0
    rk = Ratekeeper(20)
    pm = messaging.PubMaster(['selfdriveState'])
    while frame < 200:
      cs = messaging.new_message('selfdriveState')
      if frame == 20:
        cs.selfdriveState.alertSound = AudibleAlert.ready
        print("[TEST_BEEPD] AudibleAlert.ready")
      if frame == 40:
        cs.selfdriveState.alertSound = AudibleAlert.engage
        print("[TEST_BEEPD] AudibleAlert.engage")
      if frame == 60:
        cs.selfdriveState.alertSound = AudibleAlert.disengage
        print("[TEST_BEEPD] AudibleAlert.disengage")
      if frame == 80:
        cs.selfdriveState.alertSound = AudibleAlert.prompt
        print("[TEST_BEEPD] AudibleAlert.prompt")
      if frame == 100:
        cs.selfdriveState.alertSound = AudibleAlert.ding
        print("[TEST_BEEPD] AudibleAlert.ding")
      if frame == 120:
        cs.selfdriveState.alertSound = AudibleAlert.dong
        print("[TEST_BEEPD] AudibleAlert.dong")

      pm.send("selfdriveState", cs)
      frame += 1
      rk.keep_time()
    print("[TEST_BEEPD] Test sequence complete. Thread exiting.")

  def beepd_thread(self, test=False):
    if test:
      threading.Thread(target=self.test_beepd_thread, daemon=True).start()

    sm = messaging.SubMaster(['selfdriveState'])
    rk = Ratekeeper(20)

    while True:
      sm.update(0)
      self.get_audible_alert(sm)
      rk.keep_time()

def main():
  parser = argparse.ArgumentParser(description="Run beepd or beepd test mode.")
  parser.add_argument('-test', action='store_true', help='Enable simulation test data generation.')
  args = parser.parse_args()

  s = Beepd()
  s.beepd_thread(test=args.test)

if __name__ == "__main__":
  main()
