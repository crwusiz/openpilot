#!/usr/bin/env python3
import time

from common.gpio import gpio_set, gpio_init
from system.hardware.tici.pins import GPIO

def enter_dfu():
  # Enter DOS DFU
  gpio_init(GPIO.STM_RST_N, True)
  gpio_init(GPIO.STM_BOOT0, True)

  gpio_set(GPIO.STM_RST_N, True)
  gpio_set(GPIO.STM_BOOT0, True)
  time.sleep(2)
  gpio_set(GPIO.STM_RST_N, False)


if __name__ == "__main__":
  enter_dfu()