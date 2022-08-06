#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PutPrebuilt
sudo rm -f prebuilt

BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch --all
REMOTE_HASH=$(git rev-parse --short --verify origin/$BRANCH)
git reset --hard $REMOTE_HASH

if [ -f "/data/openpilot/selfdrive/modeld/models/supercombo.dlcaa" ]; then
    rm /data/openpilot/selfdrive/modeld/models/supercombo.dlc
fi