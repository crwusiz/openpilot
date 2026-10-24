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
reboot_after_update=0
full_upload=0
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
  --reboot             Reboot the Pi 5 seconds after a successful update
  --full               Upload every file (default: transfer changed files only)
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
    --reboot) reboot_after_update=1; shift;;
    --full) full_upload=1; shift;;
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
  [[ ! -L "$PACKAGE_DIR/scripts" && ! -L "$PACKAGE_DIR/scripts/package_sources.sh" ]] || fail 'Unsafe package source helper.'
  source "$PACKAGE_DIR/scripts/package_sources.sh"
  package_collect_files
  printf 'Update %s@%s:%s with %s receiver files from C4.\n' "$ssh_user" "$pi_host" "$ssh_port" "${#files[@]}"
else
  printf 'Restore the previous receiver files on %s@%s:%s.\n' "$ssh_user" "$pi_host" "$ssh_port"
fi
if (( dry_run )); then
  if (( ! rollback )); then printf '  %s\n' "${files[@]}"; fi
  if (( reboot_after_update && ! rollback )); then printf 'After a successful update: reboot Orange Pi in about 5 seconds.\n'; fi
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
    if ! ssh "${ssh_options[@]}" -o BatchMode=yes "$pi_host" "rm -rf -- '$remote_directory'"; then
      printf 'Remote staging cleanup failed: %s\n' "$remote_directory" >&2
      [[ ${1:-} != --strict ]] || return 1
    fi
  fi
  remote_directory=''
}
request_reboot() {
  # Clean up while SSH is still available, and let the remote systemd timer
  # survive this connection. A successful SSH reply confirms scheduling only.
  cleanup_remote --strict
  printf 'Receiver update complete; scheduling Orange Pi reboot.\n'
  if ssh "${ssh_options[@]}" -tt "$pi_host" 'if [ $(id -u) -eq 0 ]; then systemd-run --quiet --collect --on-active=5s --timer-property=AccuracySec=1s /usr/bin/systemctl reboot; else sudo systemd-run --quiet --collect --on-active=5s --timer-property=AccuracySec=1s /usr/bin/systemctl reboot; fi'; then
    printf 'Orange Pi reboot scheduled in about 5 seconds; HDMI/Wi-Fi will reconnect after boot.\n'
  else
    local result=$?
    printf 'ERROR: Receiver update succeeded, but Orange Pi reboot scheduling failed.\n' >&2
    return "$result"
  fi
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
: > "$payload/SHA256SUMS"
for index in "${!files[@]}"; do
  relative=${files[index]}
  if [[ "$relative" == */* ]]; then mkdir -p -- "$payload/${relative%/*}"; fi
  if [[ "$relative" == assets/* ]]; then
    # Hash large binary assets in place; copy only those selected for upload.
    digest=$(sha256sum -- "${file_sources[index]}")
  else
    # Normalize only text. PNG/font bytes must retain their original checksums.
    sed 's/\r$//' "${file_sources[index]}" > "$payload/$relative"
    digest=$(sha256sum -- "$payload/$relative")
  fi
  printf '%s  %s\n' "${digest:0:64}" "$relative" >> "$payload/SHA256SUMS"
done
LC_ALL=C sort -o "$payload/SHA256SUMS" "$payload/SHA256SUMS"
remote_directory=$(ssh "${ssh_options[@]}" "$pi_host" 'mktemp -d /tmp/cluster-update.XXXXXXXX')
if [[ ! "$remote_directory" =~ ^/tmp/cluster-update\.[a-zA-Z0-9]{8}$ ]]; then
  remote_directory=''
  fail 'SSH did not return a valid staging directory; check remote shell startup output.'
fi
scp_host=$pi_host
if [[ "$scp_host" == *:* ]]; then scp_host="[$scp_host]"; fi
reused_files=()
if (( ! full_upload )); then
  # Bootstrap from this checkout, including when the installed updater is old.
  scp "${scp_options[@]}" "$payload/SHA256SUMS" "$payload/scripts/incremental.sh" "$ssh_user@$scp_host:$remote_directory/"
  ssh "${ssh_options[@]}" "$pi_host" "mkdir '$remote_directory/payload'; mv '$remote_directory/SHA256SUMS' '$remote_directory/payload/SHA256SUMS'; bash '$remote_directory/incremental.sh' plan '$remote_directory/payload' /opt/cluster-receiver" > "$local_directory/reused"
  reused=0
  while IFS= read -r relative; do
    package_valid_file "$relative" && [[ " ${files[*]} " == *" $relative "* ]] || fail 'Invalid incremental response.'
    [[ " ${reused_files[*]} " != *" $relative "* ]] || fail 'Duplicate incremental response.'
    reused_files+=("$relative")
    if [[ -f "$payload/$relative" ]]; then rm -- "$payload/$relative"; fi
    reused=$((reused + 1))
  done < "$local_directory/reused"
  printf 'Incremental upload: %s changed, %s unchanged files.\n' "$(( ${#files[@]} - reused ))" "$reused"
fi
for index in "${!files[@]}"; do
  relative=${files[index]}
  if [[ "$relative" == assets/* && " ${reused_files[*]} " != *" $relative "* ]]; then
    cp -- "${file_sources[index]}" "$payload/$relative"
  fi
done
(cd -- "$local_directory" && scp "${scp_options[@]}" -r payload "$ssh_user@$scp_host:$remote_directory/")
if (( ! full_upload )); then
  ssh "${ssh_options[@]}" "$pi_host" "bash '$remote_directory/incremental.sh' hydrate '$remote_directory/payload' /opt/cluster-receiver"
fi
ssh "${ssh_options[@]}" -tt "$pi_host" "if [ \$(id -u) -eq 0 ]; then bash '$remote_directory/payload/scripts/update.sh' apply '$remote_directory/payload'; else sudo bash '$remote_directory/payload/scripts/update.sh' apply '$remote_directory/payload'; fi" | tee "$local_directory/apply.log"
if grep -q '^CLUSTER_PI_UNCHANGED' "$local_directory/apply.log"; then reboot_after_update=0; fi
if (( ssh_port == 22 )); then
  # Finish staging cleanup before changing the listener. The existing SSH
  # session can then complete without opening another connection to port 22.
  if (( reboot_after_update )); then cleanup_remote --strict; else cleanup_remote; fi
  ssh "${ssh_options[@]}" -tt "$pi_host" 'if [ $(id -u) -eq 0 ]; then bash /opt/cluster-receiver/scripts/ssh_port.sh; else sudo bash /opt/cluster-receiver/scripts/ssh_port.sh; fi'
fi
if (( reboot_after_update )); then request_reboot; fi
