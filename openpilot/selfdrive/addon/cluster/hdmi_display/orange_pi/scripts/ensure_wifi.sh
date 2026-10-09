#!/usr/bin/env bash
# Prepare a persistent vehicle hotspot profile without requiring a visible AP.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Usage: sudo bash scripts/ensure_wifi.sh [interface]\n'
  printf 'Save the Android hotspot profile and enable automatic connection.\n'
  exit 0
fi
[[ $# -le 1 ]] || fail 'Usage: sudo bash scripts/ensure_wifi.sh [interface]'
require_root
export LC_ALL=C
interface=${1:-wlan0}
ssid=${CLUSTER_WIFI_SSID:-Android}
password=${CLUSTER_WIFI_PASSWORD:-12345678}
[[ "$interface" =~ ^[a-zA-Z0-9_.:-]+$ ]] || fail 'Invalid Wi-Fi interface.'
[[ ${#ssid} -ge 1 && ${#ssid} -le 32 ]] || fail 'SSID must be between 1 and 32 bytes.'
[[ ( ${#password} -ge 8 && ${#password} -le 63 ) || "$password" =~ ^[a-fA-F0-9]{64}$ ]] || fail 'Invalid Wi-Fi passphrase length.'
LOCK_FILE=/run/lock/cluster-wifi.lock
exec 9> "$LOCK_FILE"
flock --wait 5 9 || fail 'Another vehicle Wi-Fi setup is in progress.'

profile=''
profiles=$(nmcli --terse --fields UUID,TYPE connection show)
while IFS=: read -r uuid type; do
  [[ "$type" == wifi || "$type" == 802-11-wireless ]] || continue
  saved_ssid=$(nmcli --escape no --get-values 802-11-wireless.ssid connection show uuid "$uuid")
  [[ "$saved_ssid" == "$ssid" ]] || continue
  saved_interface=$(nmcli --get-values connection.interface-name connection show uuid "$uuid")
  [[ -z "$saved_interface" || "$saved_interface" == -- || "$saved_interface" == "$interface" ]] || continue
  profile=$uuid
  break
done <<< "$profiles"

settings=(
  connection.autoconnect yes
  connection.autoconnect-priority 100
  connection.autoconnect-retries 0
  connection.permissions ''
  802-11-wireless.powersave 2
  wifi-sec.key-mgmt wpa-psk
  wifi-sec.psk "$password"
  wifi-sec.psk-flags 0
)
if [[ -n "$profile" ]]; then
  # Reuse the UUID even if the profile name differs from the SSID. Store the
  # configured PSK in the system profile so no desktop password agent is needed.
  nmcli --wait 5 connection modify uuid "$profile" "${settings[@]}"
  printf 'Vehicle Wi-Fi profile reused: SSID=%s, interface=%s.\n' "$ssid" "$interface"
else
  nmcli --wait 5 connection add type wifi ifname "$interface" \
    con-name "cluster-vehicle-$interface" ssid "$ssid" \
    ipv4.method auto ipv6.method auto "${settings[@]}"
  printf 'Vehicle Wi-Fi profile saved: SSID=%s, interface=%s.\n' "$ssid" "$interface"
fi
nmcli radio wifi on
# Apply to the current interface too, without dropping the existing SSH link.
# The saved profile also disables power saving on subsequent connections.
if command -v iw >/dev/null; then
  if iw dev "$interface" set power_save off; then
    printf 'Wi-Fi power saving disabled on %s.\n' "$interface"
  else
    printf 'Cannot change live Wi-Fi power saving on %s; saved profile applies on reconnection.\n' "$interface" >&2
  fi
fi
# NetworkManager autoconnects when the hotspot becomes available. Avoid "up"
# here: it would wait for an AP and could replace the current SSH connection.
printf 'Automatic connection enabled; waiting for the vehicle hotspot if unavailable.\n'
