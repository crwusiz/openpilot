#!/usr/bin/env python3
import time
from openpilot.common.gpio import gpio_set, gpio_init
from openpilot.system.hardware.tici.pins import GPIO

# Constants for GPIO state
HIGH = 1
LOW = 0
SLEEP_DURATION = 2  # seconds


def initialize_gpio():
	"""Initialize GPIO pins."""
	print("  GPIO init\n")
	gpio_init(GPIO.STM_RST_N, True)
	gpio_init(GPIO.STM_BOOT0, True)


def recover_internal_panda():
	"""Perform GPIO recovery for internal Panda."""
	print("  GPIO recover Start...\n")
	gpio_set(GPIO.STM_RST_N, HIGH)
	gpio_set(GPIO.STM_BOOT0, HIGH)

	time.sleep(SLEEP_DURATION)

	gpio_set(GPIO.STM_RST_N, LOW)
	gpio_set(GPIO.STM_BOOT0, LOW)
	print("  GPIO recover Finish...\n")


def main():
	initialize_gpio()
	recover_internal_panda()


if __name__ == "__main__":
	main()
