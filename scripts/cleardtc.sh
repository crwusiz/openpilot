#!/usr/bin/bash

cd /data/openpilot

pkill -f boardd

python ./selfdrive/debug/clear_dtc.py

sleep 10

tmux kill-server
./launch_openpilot.sh
