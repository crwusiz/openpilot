#!/usr/bin/env python3
import time
import numpy as np
from PIL import Image, ImageChops
from cereal import messaging, log
from cereal.messaging import PubMaster
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.system.athena.registration import UNREGISTERED_DONGLE_ID
from openpilot.system.version import terms_version, training_version
from selfdrive.ui.tests.test_ui.raylib_screenshots import TestUI, SCREENSHOTS_DIR, VERSION

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus

def click_center(click, t: TestUI):
  click(t.ui.width // 2, t.ui.height // 2)

def images_are_identical(img1_path, img2_raw):
  if not img1_path.exists():
    return False
  img1 = Image.open(img1_path).convert('RGB')
  img2 = img2_raw.convert('RGB')
  diff = ImageChops.difference(img1, img2)
  return not diff.getbbox()

def screenshot_until_end(t, prefix, max_pages=15):
  last_img_path = None

  for i in range(1, max_pages + 1):
    current_name = f"{prefix}_page{i}"
    current_path = SCREENSHOTS_DIR / f"{current_name}.png"

    t.screenshot(current_name)

    if last_img_path:
      current_raw = Image.open(current_path)
      if images_are_identical(last_img_path, current_raw):
        print(f"End of scroll detected at page {i}. Removing duplicate.")
        current_path.unlink()
        break

    last_img_path = current_path

    if i < max_pages:
      t.swipe_left()
      time.sleep(0.6)

def setup_mici_homescreen(click, pm: PubMaster, *args):
  pass

def setup_mici_settings(click, pm: PubMaster, *args):
  click(1, 1)
  time.sleep(0.8)

"""
def setup_mici_settings_toggles(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  click_center(click, t)

def setup_mici_settings_network(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  click(t.ui.width - 50, t.ui.height // 2)

def setup_mici_settings_wifi(click, pm: PubMaster, t: TestUI):
  setup_mici_settings_network(click, pm, t)
  click_center(click, t)
"""

def setup_mici_settings_scrolled(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  screenshot_until_end(t, "mici_settings")
  return False

def setup_mici_settings_toggles_scrolled(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  click_center(click, t)
  time.sleep(0.8)
  screenshot_until_end(t, "mici_settings_toggle")
  return False

def setup_mici_settings_network_scrolled(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  t.swipe_left()
  time.sleep(0.5)
  click_center(click, t)
  time.sleep(0.8)
  screenshot_until_end(t, "mici_settings_network")
  return False

def setup_mici_settings_device_scrolled(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  for _ in range(2):
    t.swipe_left()
    time.sleep(0.3)
  time.sleep(0.5)
  click_center(click, t)
  time.sleep(0.8)
  screenshot_until_end(t, "mici_settings_device")
  return False

def setup_mici_settings_developer_scrolled(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  for _ in range(5):
    t.swipe_left()
    time.sleep(0.3)
  time.sleep(0.5)
  click_center(click, t)
  time.sleep(0.8)
  screenshot_until_end(t, "mici_settings_developer")
  return False

def setup_mici_settings_community_scrolled(click, pm: PubMaster, t: TestUI):
  setup_mici_settings(click, pm)
  for _ in range(6):
    t.swipe_left()
    time.sleep(0.3)
  time.sleep(0.5)
  click_center(click, t)
  time.sleep(0.8)
  screenshot_until_end(t, "mici_settings_community")
  return False

def setup_onroad(click, pm: PubMaster, t: TestUI):
  ds = messaging.new_message('deviceState')
  ds.deviceState.started = True
  ps = messaging.new_message('pandaStates', 1)
  ps.pandaStates[0].pandaType = log.PandaState.PandaType.cuatro
  ps.pandaStates[0].ignitionLine = True
  driverState = messaging.new_message('driverStateV2')
  driverState.driverStateV2.leftDriverData.faceOrientation = [0, 0, 0]
  for _ in range(5):
    pm.send('deviceState', ds)
    pm.send('pandaStates', ps)
    pm.send('driverStateV2', driverState)
    ds.clear_write_flag()
    ps.clear_write_flag()
    driverState.clear_write_flag()
    time.sleep(0.05)
  t.swipe_left()
  time.sleep(0.5)

def setup_onroad_alert(click, pm: PubMaster, size: log.SelfdriveState.AlertSize, text1: str, text2: str, status: log.SelfdriveState.AlertStatus, t: TestUI):
  setup_onroad(click, pm, t)
  alert = messaging.new_message('selfdriveState')
  ss = alert.selfdriveState
  ss.alertSize = size
  ss.alertText1 = text1
  ss.alertText2 = text2
  ss.alertStatus = status
  for _ in range(5):
    pm.send('selfdriveState', alert)
    alert.clear_write_flag()
    time.sleep(0.05)

def setup_onroad_small_alert(click, pm: PubMaster, t: TestUI):
  setup_onroad_alert(click, pm, AlertSize.small, "Small Alert", "This is a small alert", AlertStatus.normal, t)

def setup_onroad_medium_alert(click, pm: PubMaster, t: TestUI):
  setup_onroad_alert(click, pm, AlertSize.mid, "Medium Alert", "This is a medium alert", AlertStatus.userPrompt, t)

def setup_onroad_full_alert(click, pm: PubMaster, t: TestUI):
  setup_onroad_alert(click, pm, AlertSize.full, "DISENGAGE IMMEDIATELY", "Driver Distracted", AlertStatus.critical, t)

def setup_onroad_full_alert_multiline(click, pm: PubMaster, t: TestUI):
  setup_onroad_alert(click, pm, AlertSize.full, "Reverse\nGear", "", AlertStatus.normal, t)

def setup_onroad_full_alert_long_text(click, pm: PubMaster, t: TestUI):
  setup_onroad_alert(click, pm, AlertSize.full, "TAKE CONTROL IMMEDIATELY", "Calibration Invalid: Remount Device & Recalibrate", AlertStatus.userPrompt, t)


CASES = {
  "homescreen": setup_mici_homescreen,
  #"settings": setup_mici_settings,
  #"settings_toggles": setup_mici_settings_toggles,
  #"settings_network": setup_mici_settings_network,
  #"settings_wifi": setup_mici_settings_wifi,
  "settings_scrolled": setup_mici_settings_scrolled,
  "settings_toggles_scrolled": setup_mici_settings_toggles_scrolled,
  "settings_network_scrolled": setup_mici_settings_network_scrolled,
  "settings_device_scrolled": setup_mici_settings_device_scrolled,
  "settings_developer_scrolled": setup_mici_settings_developer_scrolled,
  "settings_community_scrolled": setup_mici_settings_community_scrolled,
  "onroad": setup_onroad,
  "onroad_small_alert": setup_onroad_small_alert,
  "onroad_medium_alert": setup_onroad_medium_alert,
  "onroad_full_alert": setup_onroad_full_alert,
  "onroad_full_alert_multiline": setup_onroad_full_alert_multiline,
  "onroad_full_alert_long_text": setup_onroad_full_alert_long_text,
}


def create_screenshots():
  SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

  t = TestUI(big_ui=False)
  for name, setup in CASES.items():
    with OpenpilotPrefix():
      params = Params()
      params.put("DongleId", UNREGISTERED_DONGLE_ID)

      params.put("LanguageSetting", "ko")

      # Set branch name
      params.put("UpdaterCurrentDescription", VERSION)
      params.put("UpdaterNewDescription", VERSION)

      # Set terms and training version (to skip onboarding)
      params.put("HasAcceptedTerms", terms_version)
      params.put("CompletedTrainingVersion", training_version)

      t.test_ui(f"mici_{name}", setup)


if __name__ == "__main__":
  create_screenshots()
