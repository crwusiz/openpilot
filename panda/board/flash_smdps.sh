#!/usr/bin/env sh
set -e
cd /data/openpilot/panda/board;

pkill -2 -f boardd

PYTHONPATH=.. python3 -c "from python import Panda; Panda().flash('obj/panda_smdps.bin')"
sleep 1

echo ""
echo "  Smdps Panda Firmware Flash Done ..."
echo ""