#!/usr/bin/env python3
import sys
import argparse
import logging
from subprocess import check_output, CalledProcessError, run
from panda import Panda
from panda.python.uds import UdsClient, MessageTimeoutError, SESSION_TYPE, DTC_GROUP_TYPE

def parse_arguments():
  parser = argparse.ArgumentParser(description="Clear DTC status")
  parser.add_argument("addr", type=lambda x: int(x, 0), nargs="?", default=0x7DF, help="ECU address (default: 0x7DF)")
  parser.add_argument("--bus", type=int, default=0, help="CAN bus number (default: 0)")
  parser.add_argument('--debug', action='store_true', help="Enable debug mode")
  return parser.parse_args()

def check_and_kill_pandad():
  try:
    check_output(["pidof", "pandad"])
    print("  pandad process is running...\n")
    run(["pkill", "-f", "pandad"])
    print("  pandad process force terminated.\n")
  except CalledProcessError:
    pass  # pandad process not running

def setup_logging(debug):
  logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
  return logging.getLogger(__name__)

def clear_dtc(panda, addr, bus, debug, logger):
  uds_client = UdsClient(panda, addr, bus, debug)
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

  check_and_kill_pandad()

  panda = Panda()
  panda.set_safety_mode(Panda.SAFETY_ELM327)

  clear_dtc(panda, args.addr, args.bus, args.debug, logger)

  logger.info("  You may need to power cycle your vehicle now\n")

if __name__ == "__main__":
  main()
