#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PrebuiltEnable
sudo rm -f prebuilt

git fetch --all --prune

BRANCH=$(git rev-parse --abbrev-ref HEAD)
REMOTE_HASH=$(git rev-parse --short --verify origin/$BRANCH)
BRANCH_GONE=$(git branch -vv | grep ': gone]' | awk '{print $1}')

if [ "${BRANCH_GONE}" != "" ]; then
  echo $BRANCH_GONE | xargs git branch -D
fi

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