#!/usr/bin/env bash

pushd /data/openpilot

git reset --hard HEAD^

exec /data/openpilot/scripts/restart.sh
