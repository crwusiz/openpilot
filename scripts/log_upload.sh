#!/usr/bin/bash

TODAY=$(date +%y-%m-%d-%H:%M)
FILE="tmux_error.log"

if [ -f /data/${FILE} ]; then
  curl -T /data/${FILE} -u "openpilot:ruF3~Dt8" ftp://jmtechn.com:8022/tmux_log/${TODAY}_${FILE}
fi