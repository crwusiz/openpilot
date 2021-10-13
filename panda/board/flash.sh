#!/usr/bin/env sh
set -e
cd /data/openpilot/panda/board;

scons -u
PYTHONPATH=.. python3 -c "from python import Panda; Panda().flash('obj/panda.bin.signed')"
