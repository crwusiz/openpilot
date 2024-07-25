#!/usr/bin/bash

TODAY=$(date +%y-%m-%d-%H:%M)
FILE="carParams.log"
CAR=$(cat /data/params/d/CarName)
ID=$(cat /data/params/d/DongleId)

python /data/openpilot/selfdrive/debug/dump.py carParams -c 5 -o /data/${FILE}

if [ -f /data/${FILE} ]; then
  curl -T /data/${FILE} -u "openpilot:ruF3~Dt8" ftp://jmtechn.com:8022/tmux_log/${TODAY}_${CAR}_${ID}_${FILE}
fi
