#!/usr/bin/bash

tmux kill-session -t comma;
rm -f /tmp/safe_staging_overlay.lock;

if [ -f /EON ]; then
  echo $$ > /dev/cpuset/app/tasks;
  echo $PPID > /dev/cpuset/app/tasks;
fi

tmux new -s comma -d "/data/openpilot/launch_openpilot.sh"