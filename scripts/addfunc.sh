#!/usr/bin/bash

cd /data/openpilot

if [ -f /EON ]; then
  cp -f /data/openpilot/installer/driver_monitor.py /data/openpilot/selfdrive/monitoring
elif [ -f /TICI ]; then
  sudo cp -f /data/openpilot/installer/driver_monitor.py /data/openpilot/selfdrive/monitoring
fi
