#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 0 ]] || fail 'Usage: sudo bash scripts/install.sh'
require_root

apt-get update
apt-get install -y python3 python3-pygame iproute2 iw network-manager \
  libdrm2 libgbm1 libegl1 libgles2 libgl1 libinput-tools fonts-noto-cjk

install -d /opt/cluster-receiver
if [[ "$PACKAGE_DIR" != "$(cd /opt/cluster-receiver && pwd -P)" ]]; then
  # Copy only the standalone receiver, not a PC virtualenv or the openpilot tree.
  install -m 644 "$PACKAGE_DIR"/*.py "$PACKAGE_DIR/requirements.txt" \
    "$PACKAGE_DIR/README.md" "$PACKAGE_DIR/cluster-hdmi.service" /opt/cluster-receiver/
  install -d /opt/cluster-receiver/scripts
  install -m 644 "$PACKAGE_DIR"/scripts/*.sh /opt/cluster-receiver/scripts/
fi
printf '\nInstalled in /opt/cluster-receiver using the OS /usr/bin/python3.\n'
printf 'No service or desktop settings were changed.\n'
PYGAME_HIDE_SUPPORT_PROMPT=1 /usr/bin/python3 /opt/cluster-receiver/diagnose_display.py
