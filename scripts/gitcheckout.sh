#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PrebuiltEnable
sudo rm -f prebuilt

git fetch --all --prune
git remote set-head origin -a

BRANCH=$(cat /data/params/d/SelectedBranch)
git reset --hard HEAD
git checkout $BRANCH

echo ""
echo "  Git Checkout [ $BRANCH ]  "
echo ""

cp -f /data/openpilot/selfdrive/controls/lib/events.py /data/openpilot/scripts/add/events.py
cp -f /data/openpilot/selfdrive/ui/ui.h /data/openpilot/scripts/add/ui.h

exec /data/openpilot/scripts/restart.sh