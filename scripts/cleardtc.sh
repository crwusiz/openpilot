#!/usr/bin/bash

pushd /data/openpilot

python ./selfdrive/debug/clear_dtc.py

sleep 2

exec /data/openpilot/scripts/restart.sh