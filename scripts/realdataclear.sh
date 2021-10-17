#!/usr/bin/bash

cd /data/openpilot

if [ -f /EON ]; then
  rm -rf /sdcard/realdata/*
elif [ -f /TICI ]; then
  sudo rm -rf /sdcard/realdata/*
fi
