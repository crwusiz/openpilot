#!/usr/bin/env python3
"""Keep the display responsive while launch_chffrplus installs add-on packages."""
import signal
import time

from openpilot.common.spinner import Spinner


running = True


def stop_spinner(signum, frame):
  del signum, frame
  global running
  running = False


signal.signal(signal.SIGTERM, stop_spinner)
signal.signal(signal.SIGINT, stop_spinner)


with Spinner() as spinner:
  spinner.update("Installing add-on dependencies...")
  while running:
    time.sleep(0.5)
