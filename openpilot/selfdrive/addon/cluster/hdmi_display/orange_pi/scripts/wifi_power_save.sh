#!/usr/bin/env bash
# Apply only power saving settings; never reconnect or replace Wi-Fi credentials.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
export LC_ALL=C
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Usage: sudo bash scripts/wifi_power_save.sh [interface]\n'
  exit 0
fi
if [[ ${1:-} == --dispatch ]]; then
  [[ $# == 3 ]] || fail 'Invalid Wi-Fi dispatcher arguments.'
  case "$3" in up|reapply) ;; *) exit 0;; esac
  interface=$2
  set -- "$interface"
fi
[[ $# -le 1 ]] || fail 'Usage: sudo bash scripts/wifi_power_save.sh [interface]'
require_root

apply_power_save() {
  local interface=$1 type uuid actual=unavailable profile=unavailable
  [[ "$interface" =~ ^[a-zA-Z0-9_.:-]+$ ]] || fail 'Invalid Wi-Fi interface.'
  type=$(nmcli --wait 2 --get-values GENERAL.TYPE device show "$interface" 2>/dev/null) || type=''
  [[ "$type" == wifi || "$type" == 802-11-wireless ]] || return 0
  uuid=$(nmcli --wait 2 --get-values GENERAL.CON-UUID device show "$interface" 2>/dev/null) || uuid=''
  if [[ "$uuid" =~ ^[a-fA-F0-9]{8}(-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$ ]]; then
    # The active profile can belong to another SSID. Change this one property
    # only, preserving its PSK, address configuration and autoconnect policy.
    if nmcli --wait 2 connection modify uuid "$uuid" 802-11-wireless.powersave 2 >/dev/null; then
      profile=disabled
    fi
  else
    profile=inactive
  fi
  if command -v iw >/dev/null; then
    if ! timeout --kill-after=1s 2s iw dev "$interface" set power_save off; then
      printf '[CLUSTER_WIFI_POWER] interface=%s desired=off apply=failed; saved profile applies on reconnection.\n' "$interface" >&2
    fi
    actual=$(timeout --kill-after=1s 2s iw dev "$interface" get power_save 2>/dev/null) || actual=unavailable
    case "$actual" in
      'Power save: off') actual=off;;
      'Power save: on') actual=on;;
      *) actual=unavailable;;
    esac
  fi
  printf '[CLUSTER_WIFI_POWER] interface=%s desired=off actual=%s active_profile=%s\n' "$interface" "$actual" "$profile"
  if [[ "$actual" != off ]]; then
    printf '[CLUSTER_WIFI_POWER] interface=%s could not verify disabled power saving.\n' "$interface" >&2
  fi
}

if [[ $# == 1 ]]; then
  apply_power_save "$1"
else
  # Device names come from NetworkManager, including wlan1/USB Wi-Fi adapters.
  # A bounded caller protects service startup even if NetworkManager is stuck.
  if devices=$(nmcli --wait 2 --terse --fields DEVICE,TYPE device status); then
    while IFS=: read -r interface type; do
      [[ "$type" == wifi || "$type" == 802-11-wireless ]] || continue
      apply_power_save "$interface"
    done <<< "$devices"
  else
    printf '[CLUSTER_WIFI_POWER] Cannot discover active Wi-Fi devices.\n' >&2
  fi
fi
