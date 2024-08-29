#!/usr/bin/env bash

LOG=${1}

TODAY=$(date +%y-%m-%d-%H:%M)
CAR=$(cat /data/params/d/CarName)
ID=$(cat /data/params/d/DongleId)

if [ -f /data/${LOG} ]; then
  if ping -c 3 8.8.8.8 > /dev/null 2>&1; then
    curl -T /data/${LOG} -u "openpilot:ruF3~Dt8" ftp://jmtechn.com:8022/tmux_log/${TODAY}_${CAR}_${ID}_${LOG}
  fi
fi
