#!/usr/bin/env bash
# Install receiver-owned boot preparation and a reconnect power-save guard.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ ${1:-} == install && $# == 1 ]] || fail 'Usage: sudo bash scripts/wifi_boot_setup.sh install'
require_root
WIFI_CONF=/etc/systemd/system/cluster-hdmi.service.d/wifi.conf
WIFI_DISPATCHER=/etc/NetworkManager/dispatcher.d/90-cluster-wifi-power
TARGET=/opt/cluster-receiver
[[ -f "$TARGET/scripts/ensure_wifi.sh" && -f "$TARGET/scripts/wifi_power_save.sh" ]] || fail 'Update the complete Wi-Fi helper package first.'
[[ ! -L "$WIFI_CONF" && ! -L "$WIFI_DISPATCHER" ]] || fail 'Wi-Fi setup files must not be symlinks.'
install -d -m 755 -- "${WIFI_CONF%/*}" "${WIFI_DISPATCHER%/*}"
temporary=''
trap '[[ -z "$temporary" ]] || rm -f -- "$temporary"' EXIT
temporary=$(mktemp "${WIFI_CONF%/*}/.cluster-wifi.XXXXXXXX")
if [[ -f "$WIFI_CONF" ]]; then
  # Preserve existing/custom preparation and add only the missing guard.
  cat -- "$WIFI_CONF" > "$temporary"
else
  cat > "$temporary" <<'UNIT'
[Unit]
Wants=NetworkManager.service
After=NetworkManager.service

[Service]
# + runs only preparation as root; - lets HDMI start after Wi-Fi errors.
ExecStartPre=-+/usr/bin/timeout --kill-after=2s 10s /bin/bash /opt/cluster-receiver/scripts/ensure_wifi.sh wlan0
UNIT
fi
if ! grep -Eq '^ExecStartPre=.*scripts/wifi_power_save\.sh([[:space:]]|$)' "$temporary"; then
  cat >> "$temporary" <<'UNIT'

[Service]
ExecStartPre=-+/usr/bin/timeout --kill-after=2s 8s /bin/bash /opt/cluster-receiver/scripts/wifi_power_save.sh
UNIT
fi
chmod 644 "$temporary"
mv -f -- "$temporary" "$WIFI_CONF"
temporary=$(mktemp "${WIFI_DISPATCHER%/*}/.cluster-wifi.XXXXXXXX")
cat > "$temporary" <<'DISPATCHER'
#!/bin/sh
# NetworkManager executes this root-owned regular file after activation/reapply.
case ${2:-} in up|reapply) ;; *) exit 0;; esac
helper=/opt/cluster-receiver/scripts/wifi_power_save.sh
[ -f "$helper" ] || exit 0
exec /usr/bin/timeout --kill-after=2s 8s /bin/bash "$helper" --dispatch "$1" "$2"
DISPATCHER
chmod 755 "$temporary"
mv -f -- "$temporary" "$WIFI_DISPATCHER"
temporary=''
printf '[CLUSTER_WIFI_POWER] boot and reconnect guard installed.\n'
