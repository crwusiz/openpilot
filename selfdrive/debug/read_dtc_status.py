#!/usr/bin/env python3
import sys
import argparse
from subprocess import check_output, CalledProcessError, run
from panda import Panda
from panda.python.uds import UdsClient, SESSION_TYPE, DTC_REPORT_TYPE, DTC_STATUS_MASK_TYPE
from panda.python.uds import get_dtc_num_as_str, get_dtc_status_names


def parse_arguments():
	parser = argparse.ArgumentParser(description="Read DTC status")
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


def read_dtc(panda, addr, bus, debug):
	uds_client = UdsClient(panda, addr, bus=bus, debug=debug)
	print("  extended diagnostic session ...\n")

	uds_client.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)
	print("  read diagnostic codes ...\n")

	data = uds_client.read_dtc_information(DTC_REPORT_TYPE.DTC_BY_STATUS_MASK, DTC_STATUS_MASK_TYPE.ALL)
	print("  status availability:", " ".join(get_dtc_status_names(data[0])))
	print("  DTC status:\n")

	for i in range(1, len(data), 4):
		dtc_num = get_dtc_num_as_str(data[i:i + 3])
		dtc_status = " ".join(get_dtc_status_names(data[i + 3]))
		print(dtc_num, dtc_status)


def main():
	args = parse_arguments()
	check_and_kill_pandad()

	panda = Panda()
	panda.set_safety_mode(Panda.SAFETY_ELM327)
	read_dtc(panda, args.addr, args.bus, args.debug)


if __name__ == "__main__":
	main()
