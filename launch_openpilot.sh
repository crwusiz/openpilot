#!/usr/bin/bash

if [ -f /EON ]; then
  exec ./scripts/init_eon.sh
fi

export PASSIVE="0"
exec ./launch_chffrplus.sh