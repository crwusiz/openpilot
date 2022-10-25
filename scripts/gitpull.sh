#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PutPrebuilt
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

exec /data/openpilot/scripts/restart.sh