#!/usr/bin/env sh
set -e
cd /data/openpilot/panda/board;

pkill -2 -f boardd

DFU_UTIL="dfu-util"

PYTHONPATH=.. python3 -c "from python import Panda; Panda().reset(enter_bootstub=True); Panda().reset(enter_bootloader=True)" || true
sleep 1
$DFU_UTIL -d 0483:df11 -a 0 -s 0x08004000 -D obj/panda_smdps.bin
$DFU_UTIL -d 0483:df11 -a 0 -s 0x08000000:leave -D obj/bootstub.panda.bin

echo ""
echo "  Smdps Panda Firmware Recover Done ..."
echo ""