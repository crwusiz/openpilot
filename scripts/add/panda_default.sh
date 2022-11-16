#!/usr/bin/bash

pushd /data/openpilot/scripts/add

cp -f ./main.c /data/openpilot/panda/board/
cp -f ./safety_defaults.h /data/openpilot/panda/board/safety/
cp -f ./bxcan.h /data/openpilot/panda/board/drivers/

if [ ! -f "/data/PANDA_DEFAULT" ]; then
  touch /data/PANDA_DEFAULT
fi

if [ -f "/data/PANDA_MDPS" ]; then
  rm /data/PANDA_MDPS
fi
