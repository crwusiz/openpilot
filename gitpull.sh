#!/usr/bin/bash

cd /data/openpilot
echo -n "0" > /data/params/d/PutPrebuilt
rm -f prebuilt
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch --all
REMOTE_HASH=$(git rev-parse --short --verify origin/$BRANCH)
git reset --hard $REMOTE_HASH