#!/usr/bin/env bash
# Shared helpers; source this file from the entry scripts.
PACKAGE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ $(id -u) == 0 ]] || fail 'Run this script with sudo (or from a root shell).'
}

stop_if_installed() {
  local state
  state=$(systemctl show --property=LoadState --value "$1" 2>/dev/null) || return 0
  if [[ -n "$state" && "$state" != not-found ]]; then
    systemctl stop "$1"
  fi
}

hdmi_card_index() {
  local connector name
  for connector in "${1:-/sys/class/drm}"/card*-HDMI-A-*; do
    [[ -r "$connector/status" ]] || continue
    [[ $(< "$connector/status") == connected ]] || continue
    name=${connector##*/}
    name=${name%%-HDMI-A-*}
    printf '%s\n' "${name#card}"
    return 0
  done
  return 1
}

run_receiver() {
  cd -- "$PACKAGE_DIR"
  exec /usr/bin/python3 "$PACKAGE_DIR/cluster_receiver.py" \
    --interface wlan0 --width 480 --height 1920 --rotation 90 "$@"
}
