#!/usr/bin/env sh
set -e
pushd /data/openpilot/panda/board

scons -u -j$(nproc)
PYTHONPATH=.. python3 -c "from python import Panda; Panda().flash('obj/panda.bin.signed')"

sleep 10

./data/openpilot/selfdrive/boardd/pandad.py
