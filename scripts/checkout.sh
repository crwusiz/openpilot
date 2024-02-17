#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PrebuiltEnable
sudo rm -f prebuilt

git fetch --all --prune

BRANCH=$(cat /data/params/d/SelectedBranch)

git reset --hard HEAD
git checkout $BRANCH

echo ""
echo "  Git Checkout [ $BRANCH ]  "
echo ""

exec /data/openpilot/scripts/restart.sh