#!/usr/bin/bash

pushd /data/openpilot

git reset --hard HEAD^

exec /data/openpilot/scripts/restart.sh
