#!/usr/bin/bash

if [ ! -f "/data/openpilot/selfdrive/modeld/models/supercombo.dlc" ]; then
    pushd /data/openpilot/selfdrive/modeld/models
    cat supercombo.dlca* > supercombo.dlc
    popd
fi

export PASSIVE="0"
exec ./launch_chffrplus.sh