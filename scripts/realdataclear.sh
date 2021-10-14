#!/usr/bin/bash

cd /data/openpilot

if [ -f /EON ]; then
  rm -rf /sdcard/realdata/*
elif [ -f /TICI ];
  sudo rm -rf /sdcard/realdata/*
fi
