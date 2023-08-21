#!/usr/bin/env python3
import time

from openpilot.common.gpio import gpio_set, gpio_init
from openpilot.system.hardware.tici.pins import GPIO

#recover_internal_panda
print("  GPIO init\n")
gpio_init(GPIO.STM_RST_N, True)
gpio_init(GPIO.STM_BOOT0, True)

print("  GPIO recover Start...\n")
gpio_set(GPIO.STM_RST_N, 1)
gpio_set(GPIO.STM_BOOT0, 1)

time.sleep(2)

gpio_set(GPIO.STM_RST_N, 0)
gpio_set(GPIO.STM_BOOT0, 0)
print("  GPIO recover Finish...\n")