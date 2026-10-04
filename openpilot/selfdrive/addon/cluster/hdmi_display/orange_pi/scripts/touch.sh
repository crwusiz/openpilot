#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_root
if [[ $# == 0 ]]; then
  exec libinput list-devices
fi
[[ $# == 1 && -c "$1" && "$1" == /dev/input/event* ]] || fail 'Usage: sudo bash scripts/touch.sh [/dev/input/eventN]'
exec libinput debug-events --device "$1"
