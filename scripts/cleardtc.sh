#!/usr/bin/bash

pushd /data/openpilot

pkill -2 -f boardd

python ./selfdrive/debug/clear_dtc.py

sleep 5

exec /data/openpilot/scripts/restart.sh