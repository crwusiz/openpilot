#!/usr/bin/bash

cd /data/openpilot

pkill -f boardd

python ./selfdrive/debug/clear_dtc.py

sleep 30

pkill -f openpilot;./launch_openpilot.sh