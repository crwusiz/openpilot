#!/usr/bin/bash

pushd /data/openpilot/scripts/add

cp -f ./main_mdps.c /data/openpilot/panda/board/main.c
cp -f ./safety_defaults_mdps.h /data/openpilot/panda/board/safety/safety_defaults.h
cp -f ./bxcan_mdps.h /data/openpilot/panda/board/drivers/bxcan.h

if [ ! -f "/data/PANDA_MDPS" ]; then
  touch /data/PANDA_MDPS
fi

if [ -f "/data/PANDA_DEFAULT" ]; then
  rm /data/PANDA_DEFAULT
fi

exec /data/openpilot/scripts/restart.sh