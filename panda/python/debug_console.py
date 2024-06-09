#!/usr/bin/env python3

import os
import sys
import time
import select
import codecs
from panda import Panda

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

# Constants for terminal colors
SETCOLOR = ["\033[1;32;40m", "\033[1;31;40m"]
UNSETCOLOR = "\033[00m"

# Environment variable configurations
PORT_NUMBER = int(os.getenv("PORT", "0"))
CLAIM = os.getenv("CLAIM") is not None
NO_COLOR = os.getenv("NO_COLOR") is not None
NO_RECONNECT = os.getenv("NO_RECONNECT") is not None
SERIAL = os.getenv("SERIAL")
BAUD = os.getenv("BAUD")


def get_pandas(serials):
  """Initialize Panda objects."""
  return [Panda(x, claim=CLAIM) for x in serials]


def setup_baud_rate(pandas):
  """Set UART baud rate for pandas."""
  if BAUD is not None:
    for panda in pandas:
      panda.set_uart_baud(PORT_NUMBER, int(BAUD))


def read_from_panda(panda, decoder, idx):
  """Read data from Panda and write to stdout."""
  while True:
    ret = panda.serial_read(PORT_NUMBER)
    if len(ret) > 0:
      decoded = decoder.decode(ret)
      if NO_COLOR:
        sys.stdout.write(decoded)
      else:
        sys.stdout.write(SETCOLOR[idx] + decoded + UNSETCOLOR)
      sys.stdout.flush()
    else:
      break


def handle_user_input(panda):
  """Handle user input and write to Panda."""
  if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
    ln = sys.stdin.readline()
    if CLAIM:
      panda.serial_write(PORT_NUMBER, ln)


def main():
  """Main function to manage pandas and read/write operations."""
  while True:
    try:
      serials = Panda.list()
      if SERIAL:
        serials = [x for x in serials if x == SERIAL]

      pandas = get_pandas(serials)
      decoders = [codecs.getincrementaldecoder('utf-8')() for _ in pandas]

      if not pandas:
        print("  no pandas found\n")
        if NO_RECONNECT:
          sys.exit(0)
        time.sleep(1)
        continue

      setup_baud_rate(pandas)

      while True:
        for i, panda in enumerate(pandas):
          read_from_panda(panda, decoders[i], i)
          handle_user_input(panda)
        time.sleep(0.01)
    except KeyboardInterrupt:
      break
    except Exception as e:
      print("  panda disconnected!\n", e)
      time.sleep(0.5)


if __name__ == "__main__":
  main()
