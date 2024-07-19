#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PrebuiltEnable
sudo rm -f prebuilt

git fetch --all --prune
git reset --hard HEAD

if [ ! -f "/data/params/d/SelectedBranch" ]; then
  git branch --show-current > /data/params/d/SelectedBranch
  #git status | grep "origin" | awk -F'/' '{print $2}' | sed -e 's/..$//' > /data/params/d/SelectedBranch
fi

BRANCH=$(cat /data/params/d/SelectedBranch)

git checkout $BRANCH

echo ""
echo "  Git Checkout [ $BRANCH ]  "
echo ""

exec /data/openpilot/scripts/gitpull.sh
