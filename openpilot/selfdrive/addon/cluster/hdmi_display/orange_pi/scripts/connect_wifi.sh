#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# -ge 1 && $# -le 2 ]] || fail 'Usage: sudo bash scripts/connect_wifi.sh "SSID" [interface]'
require_root
interface=${2:-wlan0}
# Let NetworkManager ask for the password instead of putting it in shell history.
nmcli --ask device wifi connect "$1" ifname "$interface"
ip -4 addr show dev "$interface"
