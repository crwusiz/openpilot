#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 0 ]] || fail 'Usage: sudo bash scripts/install.sh'
require_root
TARGET=/opt/cluster-receiver

apt-get update
apt-get install -y python3 python3-pygame iproute2 iw network-manager \
  libdrm2 libgbm1 libegl1 libgles2 libgl1 libinput-tools
bash "$PACKAGE_DIR/scripts/prepare_h264.sh"

install -d "$TARGET"
source "$PACKAGE_DIR/scripts/package_sources.sh"
package_collect_files
for index in "${!files[@]}"; do
  package_safe_parents "$TARGET" "${files[index]}" || fail 'Unsafe installed package path.'
  relative=${files[index]}
  directory=$TARGET
  while [[ "$relative" == */* ]]; do
    directory="$directory/${relative%%/*}"
    install -d -m 755 -- "$directory"
    relative=${relative#*/}
  done
  if [[ "$PACKAGE_DIR" != "$(cd "$TARGET" && pwd -P)" ]]; then
    install -D -m 644 -- "${file_sources[index]}" "$TARGET/${files[index]}"
  else
    # Repair an unpacked standalone bundle in place as well as copied installs.
    chmod 644 -- "$TARGET/${files[index]}"
  fi
done
for retired in cluster_protocol.py frame_stream.py tcp_diagnostics.py; do
  package_safe_parents "$TARGET" "$retired" || fail 'Unsafe retired module path.'
  rm -f -- "$TARGET/$retired"
done
bash "$TARGET/scripts/fan_setup.sh" install
if [[ -f /etc/systemd/system/cluster-hdmi.service ]]; then
  # Repair a previously installed receiver without rewriting its custom unit,
  # account, HDMI options or boot target, and without restarting its connection.
  bash "$TARGET/scripts/wifi_boot_setup.sh" install
  systemctl daemon-reload
  if ! /usr/bin/python3 "$TARGET/touch_status.py" --install-gpu-access </dev/null; then
    printf '[CLUSTER_GPU_ACCESS] GPU reader setup unavailable; continuing receiver installation.\n' >&2
  fi
  timeout --kill-after=2s 8s bash "$TARGET/scripts/wifi_power_save.sh" || \
    printf '[CLUSTER_WIFI_POWER] Live power-save preparation failed or timed out.\n' >&2
fi
printf '\nInstalled in %s using the OS /usr/bin/python3.\n' "$TARGET"
printf 'Receiver account, HDMI options, service state and desktop boot target were preserved.\n'
PYGAME_HIDE_SUPPORT_PROMPT=1 /usr/bin/python3 "$TARGET/diagnose_display.py"
