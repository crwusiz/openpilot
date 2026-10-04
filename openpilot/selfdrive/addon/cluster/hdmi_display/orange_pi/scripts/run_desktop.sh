#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Usage: bash scripts/run_desktop.sh [receiver options]\n'
  printf 'Run from a terminal in the logged-in X11 desktop session.\n'
  exit 0
fi
[[ -n ${DISPLAY:-} ]] || fail 'Open a terminal in the Orange Pi desktop and run this script there (without sudo).'
export PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONUNBUFFERED=1
export SDL_VIDEODRIVER=x11 SDL_RENDER_DRIVER=software SDL_FRAMEBUFFER_ACCELERATION=0
run_receiver "$@"
