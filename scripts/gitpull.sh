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

cp -f /data/openpilot/selfdrive/controls/lib/events.py /data/openpilot/scripts/add/events.py
cp -f /data/openpilot/selfdrive/ui/ui.h /data/openpilot/scripts/add/ui.h

exec /data/openpilot/scripts/restart.sh