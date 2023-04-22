#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PrebuiltEnable
sudo rm -f prebuilt

BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch --all
REMOTE_HASH=$(git rev-parse --short --verify origin/$BRANCH)
echo ""
git reset --hard $REMOTE_HASH

echo ""
echo "  Git Fetch and Reset HEAD commit ..."
echo ""
echo "  current branch is [ $BRANCH ]  "
echo ""

cp -f /data/openpilot/panda/board/main.c /data/openpilot/scripts/add/main.c
cp -f /data/openpilot/panda/board/safety/safety_defaults.h /data/openpilot/scripts/add/safety_defaults.h
cp -f /data/openpilot/panda/board/safety/bxcan.h /data/openpilot/scripts/add/bxcan.h
cp -f /data/openpilot/selfdrive/controls/lib/events.py /data/openpilot/scripts/add/events.py

exec /data/openpilot/scripts/restart.sh