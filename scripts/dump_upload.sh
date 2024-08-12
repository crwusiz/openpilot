#!/usr/bin/bash

SERVICES=${1}

TODAY=$(date +%y-%m-%d-%H:%M)
CAR=$(cat /data/params/d/CarName)
ID=$(cat /data/params/d/DongleId)

upload_log() {
  FILE="${1}.log"
  python /data/openpilot/selfdrive/debug/dump.py ${SERVICES} -c 5 -o /data/${FILE}

  if ping -c 3 8.8.8.8 > /dev/null 2>&1; then
    if [ -f /data/${FILE} ]; then
      curl -T /data/${FILE} -u "openpilot:ruF3~Dt8" ftp://jmtechn.com:8022/tmux_log/${TODAY}_${CAR}_${ID}_${FILE}
    fi
  fi
}

if [ -z "$SERVICES" ]; then
  upload_log "carParams"
  upload_log "carState"
  upload_log "carControl"
  upload_log "controlsState"
  upload_log "deviceState"
  upload_log "pandaStates"
else
  upload_log "$SERVICES"
fi
