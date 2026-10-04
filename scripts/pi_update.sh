#!/usr/bin/env bash
# Run on C4 to update the Orange Pi receiver over SSH.
set -euo pipefail

OPENPILOT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
DEPLOY_SCRIPT="$OPENPILOT_ROOT/openpilot/selfdrive/addon/cluster/hdmi_display/orange_pi/scripts/deploy.sh"

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  cat <<'HELP'
Usage: bash scripts/pi_update.sh [PI_HOST] [options]
Run on C4; upload this checkout's receiver files to the Orange Pi.
Omit PI_HOST to use the currently connected Orange Pi address.
  --host PI_HOST       Explicit Orange Pi IP, hostname or SSH alias
  --user USER          Pi SSH account (default: root; other accounts need sudo)
  --port PORT          Pi SSH port (default: 22)
  --identity FILE      SSH private key on C4
  --ask-password       Enter the SSH password manually instead of using orangepi
  --rollback           Restore the previous receiver version without uploading
  --dry-run            Print the target and file list without using SSH
  -h, --help           Show this help
Password override: set CLUSTER_PI_PASSWORD in the environment.
HELP
  exit 0
fi

if [[ ! -f "$DEPLOY_SCRIPT" ]]; then
  printf 'ERROR: Pi deployment script not found: %s\n' "$DEPLOY_SCRIPT" >&2
  exit 1
fi

exec bash "$DEPLOY_SCRIPT" "$@"
