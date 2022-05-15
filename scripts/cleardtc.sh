#!/usr/bin/bash

cd /data/openpilot

pkill -2 -f boardd

python ./selfdrive/debug/clear_dtc.py

sleep 10

./selfdrive/boardd/boardd