#!/usr/bin/bash

if [ -z "$BASEDIR" ]; then
  BASEDIR="/data/openpilot"
fi

NEOS_PY="$BASEDIR/selfdrive/hardware/eon/neos.py"
MANIFEST="$BASEDIR/selfdrive/hardware/eon/op3t.json"

echo "Installing op3t NEOS update"
cp -f "$BASEDIR/selfdrive/hardware/eon/update.zip" "/sdcard/update.zip";
pm disable ai.comma.plus.offroad
killall _ui

$NEOS_PY --swap-if-ready $MANIFEST
$BASEDIR/selfdrive/hardware/eon/updater $NEOS_PY $MANIFEST
