#!/usr/bin/env python3
import time
from panda import Panda, PandaDFU
from openpilot.common.gpio import gpio_set, gpio_init
from openpilot.system.hardware.tici.pins import GPIO

def reset_usb_hub():
  """Reset the USB hub."""
  gpio_set(GPIO.HUB_RST_N, 0)
  time.sleep(0.5)
  gpio_set(GPIO.HUB_RST_N, 1)

def enter_dfu_mode():
  """Reset into DFU mode for flashing."""
  gpio_set(GPIO.STM_RST_N, 1)
  gpio_set(GPIO.STM_BOOT0, 1)
  time.sleep(1)
  gpio_set(GPIO.STM_RST_N, 0)
  gpio_set(GPIO.STM_BOOT0, 0)
  time.sleep(1)

def flash_bootstub():
  """Flash the bootstub."""
  print("flashing bootstub")
  PandaDFU(None).recover()

def reset_device():
  """Reset the device."""
  gpio_set(GPIO.STM_RST_N, 1)
  time.sleep(0.5)
  gpio_set(GPIO.STM_RST_N, 0)
  time.sleep(1)

def flash_application():
  """Flash the application firmware."""
  print("flashing app")
  p = Panda()
  assert p.bootstub, "Device is not in bootstub mode"
  p.flash()

if __name__ == "__main__":
  # Initialize GPIO pins
  for pin in (GPIO.STM_RST_N, GPIO.STM_BOOT0, GPIO.HUB_RST_N):
    gpio_init(pin, True)

  # Reset the USB hub
  reset_usb_hub()

  # Enter DFU mode and flash bootstub
  print("resetting into DFU")
  enter_dfu_mode()
  flash_bootstub()

  # Reset the device and flash the application firmware
  reset_device()
  flash_application()
