#!/usr/bin/bash

export LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib
export HOME=/data/data/com.termux/files/home
export PATH=/usr/local/bin:/data/data/com.termux/files/usr/bin:/data/data/com.termux/files/usr/sbin:/data/data/com.termux/files/usr/bin/applets:/bin:/sbin:/vendor/bin:/system/sbin:/system/bin:/system/xbin:/data/data/com.termux/files/usr/bin/python
export PYTHONPATH=/data/openpilot

cd /data/openpilot
echo -n "0" > /data/params/d/PutPrebuilt
rm -f prebuilt
BRANCH=$(git rev-parse --abbrev-ref HEAD)
/data/data/com.termux/files/usr/bin/git fetch --all
#/data/data/com.termux/files/usr/bin/git pull
REMOTE_HASH=$(git rev-parse --short --verify origin/$BRANCH)
/data/data/com.termux/files/usr/bin/git reset --hard $REMOTE_HASH