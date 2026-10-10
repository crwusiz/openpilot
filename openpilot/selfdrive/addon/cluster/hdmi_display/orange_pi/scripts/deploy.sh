#!/usr/bin/env bash
# Run on C4 after connecting to C4 with an SSH client such as MobaXterm.
set -euo pipefail
umask 077
PACKAGE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
pi_host=''
ssh_user=root
ssh_port=9122
port_explicit=0
identity=''
ask_password=0
rollback=0
dry_run=0
local_directory=''
remote_directory=''

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'HELP'
Usage: bash scripts/deploy.sh [PI_HOST] [options]
Run on C4; upload this C4 checkout's receiver files to the Orange Pi.
Omit PI_HOST to use the currently connected Orange Pi address.
  --host PI_HOST       Explicit Orange Pi IP, hostname or SSH alias
  --user USER          Pi SSH account (default: root; other accounts need sudo)
  --port PORT          Use this SSH port only (default: 9122, then 22 if unavailable)
  --identity FILE      SSH private key on C4
  --ask-password       Enter the SSH password manually instead of using orangepi
  --rollback           Restore the previous receiver version without uploading
  --dry-run            Print the target and file list without using SSH
  -h, --help           Show this help
Password override: set CLUSTER_PI_PASSWORD in the environment.
An update over port 22 also migrates the Pi SSH listener to port 9122.
HELP
}
while (( $# )); do
  case "$1" in
    --host|--user|--port|--identity)
      (( $# >= 2 )) || fail "Missing value for $1."
      case "$1" in
        --host) [[ -z "$pi_host" ]] || fail 'Specify only one Pi host.'; pi_host=$2;;
        --user) ssh_user=$2;;
        --port) ssh_port=$2; port_explicit=1;;
        --identity) identity=$2;;
      esac
      shift 2;;
    --rollback) rollback=1; shift;;
    --ask-password) ask_password=1; shift;;
    --dry-run) dry_run=1; shift;;
    -h|--help) usage; exit 0;;
    -*) fail "Unknown option: $1";;
    *) [[ -z "$pi_host" ]] || fail 'Specify only one Pi host.'; pi_host=$1; shift;;
  esac
done
[[ "$ssh_user" =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || fail 'Invalid SSH account.'
[[ "$ssh_port" =~ ^[0-9]{1,5}$ ]] || fail 'Invalid SSH port.'
ssh_port=$((10#$ssh_port))
(( ssh_port >= 1 && ssh_port <= 65535 )) || fail 'Invalid SSH port.'
if [[ -n "$identity" ]]; then
  [[ -f "$identity" ]] || fail "SSH key does not exist: $identity"
  identity=$(realpath -- "$identity")
fi
if [[ -z "$pi_host" ]]; then
  status_helper="$PACKAGE_DIR/../network_status.py"
  [[ -f "$status_helper" ]] || fail 'Specify the Pi IP explicitly; C4 connection status helper is missing.'
  pi_host=$("${CLUSTER_PYTHON:-python3}" "$status_helper" --ip) || fail 'No connected Orange Pi. Use: bash scripts/deploy.sh PI_IP'
fi
[[ "$pi_host" =~ ^[a-zA-Z0-9][a-zA-Z0-9._:-]*$ ]] || fail 'Invalid Pi host.'

files=()
if (( ! rollback )); then
  [[ ! -L "$PACKAGE_DIR/scripts" ]] || fail 'Package scripts must not be a symlink.'
  files=(README.md requirements.txt cluster-hdmi.service)
  for source in "$PACKAGE_DIR"/*.py "$PACKAGE_DIR"/scripts/*.sh; do
    [[ -f "$source" ]] || continue
    files+=("${source#"$PACKAGE_DIR/"}")
  done
  for relative in "${files[@]}"; do
    [[ "$relative" =~ ^([a-zA-Z0-9_]+\.py|README\.md|requirements\.txt|cluster-hdmi\.service|scripts/[a-zA-Z0-9_-]+\.sh)$ ]] || fail "Unexpected package filename: $relative"
    [[ -f "$PACKAGE_DIR/$relative" && ! -L "$PACKAGE_DIR/$relative" ]] || fail "Missing or symlink package file: $relative"
  done
  for required in cluster_receiver.py hdmi_display.py frame_stream.py display_controls.py texture_presenter.py scripts/common.sh \
    scripts/update.sh scripts/ssh_port.sh scripts/ensure_wifi.sh scripts/wifi_boot_setup.sh scripts/wifi_power_save.sh; do
    [[ " ${files[*]} " == *" $required "* ]] || fail "Package is missing $required."
  done
  printf 'Update %s@%s:%s with %s receiver files from C4.\n' "$ssh_user" "$pi_host" "$ssh_port" "${#files[@]}"
else
  printf 'Restore the previous receiver files on %s@%s:%s.\n' "$ssh_user" "$pi_host" "$ssh_port"
fi
if (( dry_run )); then
  if (( ! rollback )); then printf '  %s\n' "${files[@]}"; fi
  printf 'Dry run: no SSH connection or file changes.\n'
  exit 0
fi
command -v ssh >/dev/null || fail 'Install the OpenSSH client on C4 (ssh is missing).'
if (( ! rollback )); then
  command -v scp >/dev/null || fail 'Install the OpenSSH client on C4 (scp is missing).'
  command -v sha256sum >/dev/null || fail 'sha256sum is missing on C4.'
fi
temp_parent=$(cd -- "${TMPDIR:-/tmp}" && pwd -P)
local_directory=$(mktemp -d "$temp_parent/cluster-deploy.XXXXXXXX")
# Reuse one authenticated SSH connection for staging, SCP, apply and cleanup.
control_path="$local_directory/ssh-control"
control_path=${control_path//\\/\\\\}
control_path=${control_path//\"/\\\"}
control_path=${control_path//%/%%}
options=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3
         -o ControlMaster=auto -o ControlPersist=60 -o "ControlPath=\"$control_path\"")
if [[ -n "$identity" ]]; then options+=(-i "$identity"); fi
set_port_options() {
  ssh_options=(-p "$ssh_port" -l "$ssh_user" "${options[@]}")
  scp_options=(-P "$ssh_port" "${options[@]}")
}
set_port_options
cleanup_remote() {
  if [[ "$remote_directory" =~ ^/tmp/cluster-update\.[a-zA-Z0-9]{8}$ ]]; then
    ssh "${ssh_options[@]}" -o BatchMode=yes "$pi_host" "rm -rf -- '$remote_directory'" || printf 'Remote staging cleanup failed: %s\n' "$remote_directory" >&2
  fi
  remote_directory=''
}
cleanup() {
  local result=$?
  trap - EXIT
  set +e
  cleanup_remote
  ssh "${ssh_options[@]}" -o BatchMode=yes -O exit "$pi_host" >/dev/null 2>&1
  if [[ "$local_directory" == "$temp_parent"/cluster-deploy.* && "${local_directory##*/}" =~ ^cluster-deploy\.[a-zA-Z0-9]{8}$ ]]; then
    rm -rf -- "$local_directory"
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

if (( ! ask_password )); then
  command -v setsid >/dev/null || fail 'setsid is missing on C4; use --ask-password for manual authentication.'
  askpass="$local_directory/askpass"
  cat > "$askpass" <<'ASKPASS'
#!/bin/sh
case ${1:-} in
  *[Pp]assword:*) printf '%s\n' "$CLUSTER_PI_PASSWORD";;
  *) exit 1;;
esac
ASKPASS
  chmod 700 -- "$askpass"
  # Detach only authentication from the terminal for older OpenSSH releases.
  # Subsequent commands keep stdin so a non-root Pi account can still use sudo.
  # The helper reads the password from the environment, never a command argument.
fi
authenticate() {
  if (( ask_password )); then
    LC_ALL=C ssh "${ssh_options[@]}" -o BatchMode=no -o NumberOfPasswordPrompts=1 -n "$pi_host" true
  else
    CLUSTER_PI_PASSWORD=${CLUSTER_PI_PASSWORD-orangepi} SSH_ASKPASS="$askpass" \
      SSH_ASKPASS_REQUIRE=force DISPLAY=${DISPLAY:-:0} LC_ALL=C \
      setsid --wait ssh "${ssh_options[@]}" -o BatchMode=no -o NumberOfPasswordPrompts=1 -n "$pi_host" true
  fi
}
ssh_error="$local_directory/ssh-error"
if authenticate 2> "$ssh_error"; then
  cat -- "$ssh_error" >&2
else
  result=$?
  cat -- "$ssh_error" >&2
  # An authentication/host-key failure means 9122 is reachable. Retry only
  # transport failures for that port, including a stalled SSH banner exchange.
  if (( port_explicit || result != 255 )) || ! grep -Eq \
    '^ssh: connect to host .* port 9122: (Connection refused|Connection timed out|Operation timed out|No route to host|Network is unreachable)|^Connection to .* port 9122 timed out$|^Connection (closed|reset) by .* port 9122$' "$ssh_error"; then
    exit "$result"
  fi
  printf 'SSH port 9122 is unavailable; retrying %s@%s:22.\n' "$ssh_user" "$pi_host"
  ssh_port=22
  set_port_options
  authenticate
fi
# Reuse the authenticated master; if it is lost, fail without a new prompt.
ssh_options+=(-o BatchMode=yes)
scp_options+=(-o BatchMode=yes)

if (( rollback )); then
  ssh "${ssh_options[@]}" -tt "$pi_host" 'if [ $(id -u) -eq 0 ]; then bash /var/lib/cluster-receiver/updates/update.sh rollback; else sudo bash /var/lib/cluster-receiver/updates/update.sh rollback; fi'
  exit 0
fi
payload="$local_directory/payload"
mkdir -p -- "$payload/scripts"
for relative in "${files[@]}"; do
  # Normalize a checkout made with CRLF; the package is UTF-8 text.
  sed 's/\r$//' "$PACKAGE_DIR/$relative" > "$payload/$relative"
done
(cd -- "$payload" && sha256sum -- "${files[@]}" > SHA256SUMS)
remote_directory=$(ssh "${ssh_options[@]}" "$pi_host" 'mktemp -d /tmp/cluster-update.XXXXXXXX')
if [[ ! "$remote_directory" =~ ^/tmp/cluster-update\.[a-zA-Z0-9]{8}$ ]]; then
  remote_directory=''
  fail 'SSH did not return a valid staging directory; check remote shell startup output.'
fi
scp_host=$pi_host
if [[ "$scp_host" == *:* ]]; then scp_host="[$scp_host]"; fi
(cd -- "$local_directory" && scp "${scp_options[@]}" -r payload "$ssh_user@$scp_host:$remote_directory/")
ssh "${ssh_options[@]}" -tt "$pi_host" "if [ \$(id -u) -eq 0 ]; then bash '$remote_directory/payload/scripts/update.sh' apply '$remote_directory/payload'; else sudo bash '$remote_directory/payload/scripts/update.sh' apply '$remote_directory/payload'; fi"
if (( ssh_port == 22 )); then
  # Finish staging cleanup before changing the listener. The existing SSH
  # session can then complete without opening another connection to port 22.
  cleanup_remote
  ssh "${ssh_options[@]}" -tt "$pi_host" 'if [ $(id -u) -eq 0 ]; then bash /opt/cluster-receiver/scripts/ssh_port.sh; else sudo bash /opt/cluster-receiver/scripts/ssh_port.sh; fi'
fi
