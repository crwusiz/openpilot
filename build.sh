#!/usr/bin/bash

BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch --all
REMOTE_HASH=$(git rev-parse --short --verify origin/$BRANCH)
git reset --hard $REMOTE_HASH
scons -i