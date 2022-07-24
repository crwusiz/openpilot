#!/usr/bin/bash

cd /data/openpilot && scons -c;
rm /data/openpilot/.sconsign.dblite;
rm -rf /tmp/scons_cache;

echo -n "0" > /data/params/d/PutPrebuilt
sudo rm -f prebuilt

# Allows you to restart OpenPilot without rebooting the Comma 3
tmux kill-session -t comma;
rm -f /tmp/safe_staging_overlay.lock;
tmux new -s comma -d "/data/openpilot/launch_openpilot.sh"