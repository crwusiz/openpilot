#!/usr/bin/env python3
import sys
import argparse
import logging
from subprocess import check_output, CalledProcessError
from panda import Panda
from panda.python.uds import UdsClient, MessageTimeoutError, SESSION_TYPE, DTC_GROUP_TYPE

def parse_arguments():
  parser = argparse.ArgumentParser(description="Clear DTC status")
  parser.add_argument("addr", type=lambda x: int(x, 0), nargs="?", default=0x7DF, help="ECU address (default: 0x7DF)")
  parser.add_argument("--bus", type=int, default=0, help="CAN bus number (default: 0)")
  parser.add_argument('--debug', action='store_true', help="Enable debug mode")
  return parser.parse_args()

def check_boardd_running():
  try:
    check_output(["pidof", "boardd"])
    print("  boardd is running, please kill openpilot before running this script! (aborted)\n")
    sys.exit(1)
  except CalledProcessError as e:
    if e.returncode != 1:  # 1 == no process found (boardd not running)
      raise e

def setup_logging(debug):
  logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
  return logging.getLogger(__name__)

def clear_dtc(panda, addr, bus, logger):
  uds_client = UdsClient(panda, addr, bus=bus, debug=args.debug)
  logger.info("  Starting extended diagnostic session ...")
  try:
    uds_client.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
  except MessageTimeoutError:
    if addr != 0x7DF:
      logger.error("  Timeout during extended diagnostic session")
      raise
  logger.info("  Clearing diagnostic information ...")
  try:
    uds_client.clear_diagnostic_information(DTC_GROUP_TYPE.ALL)
  except MessageTimeoutError:
    if addr != 0x7DF:
      logger.error("  Timeout during clearing diagnostic information")
      pass

def main():
  args = parse_arguments()
  logger = setup_logging(args.debug)

  check_boardd_running()

  panda = Panda()
  panda.set_safety_mode(Panda.SAFETY_ELM327)

  clear_dtc(panda, args.addr, args.bus, logger)

  logger.info("  You may need to power cycle your vehicle now\n")

if __name__ == "__main__":
  main()
