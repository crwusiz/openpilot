#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Usage: sudo bash scripts/run_console.sh [receiver options]\n'
  printf 'Closes the desktop session and stops cluster-hdmi before running KMSDRM.\n'
  exit 0
fi
require_root
card=${SDL_KMSDRM_DEVICE_INDEX:-}
if [[ -z "$card" ]]; then
  card=$(hdmi_card_index) || fail 'No connected HDMI connector found; run scripts/diagnose.sh.'
fi
[[ "$card" =~ ^[0-9]+$ && -c /dev/dri/card$card ]] || fail "Invalid DRM card: $card"
printf 'Stopping cluster-hdmi and the desktop session; using /dev/dri/card%s.\n' "$card"
stop_if_installed cluster-hdmi.service
stop_if_installed display-manager.service
export PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONUNBUFFERED=1
export SDL_VIDEODRIVER=kmsdrm SDL_RENDER_DRIVER=opengles2 SDL_KMSDRM_DEVICE_INDEX="$card"
run_receiver "$@"
