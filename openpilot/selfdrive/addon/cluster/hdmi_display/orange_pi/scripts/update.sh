#!/usr/bin/env bash
# Standalone so the staged copy can recover even when live files are broken.
set -euo pipefail
umask 077
TARGET=/opt/cluster-receiver
STATE=/var/lib/cluster-receiver/updates
UNIT=cluster-hdmi.service
FILES=()
REMOVE=()
pending=0
resume=0
recovering=0
recovery_goal=''
backup=''

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
valid_file() {
  [[ "$1" =~ ^([a-zA-Z0-9_]+\.py|README\.md|requirements\.txt|cluster-hdmi\.service|scripts/[a-zA-Z0-9_-]+\.sh)$ ]]
}
read_manifest() {
  local directory=$1 line name hash required
  FILES=()
  [[ -f "$directory/SHA256SUMS" && ! -L "$directory/SHA256SUMS" && ! -L "$directory/scripts" ]] || fail 'Missing or unsafe payload manifest.'
  while IFS= read -r line; do
    hash=${line:0:64}
    name=${line:66}
    [[ "$hash" =~ ^[a-f0-9]{64}$ && ( "${line:64:2}" == '  ' || "${line:64:2}" == ' *' ) ]] || fail 'Invalid checksum entry.'
    valid_file "$name" || fail "Unexpected payload path: $name"
    [[ -f "$directory/$name" && ! -L "$directory/$name" ]] || fail "Missing or symlink payload file: $name"
    [[ " ${FILES[*]} " != *" $name "* ]] || fail "Duplicate payload path: $name"
    FILES+=("$name")
  done < "$directory/SHA256SUMS"
  for required in cluster_receiver.py hdmi_display.py scripts/common.sh cluster-hdmi.service; do
    [[ " ${FILES[*]} " == *" $required "* ]] || fail "Payload is missing $required."
  done
  (cd -- "$directory" && sha256sum --check --strict SHA256SUMS) || fail 'Payload checksum verification failed.'
}
copy_file() {
  local source=$1 destination=$2 temporary
  install -d -m 755 -- "${destination%/*}" || return 1
  temporary=$(mktemp "${destination%/*}/.cluster-file.XXXXXXXX") || return 1
  if ! install -m 644 -- "$source" "$temporary"; then
    rm -f -- "$temporary"
    return 1
  fi
  # Rename keeps the running script's original inode intact during self-update.
  if ! mv -f -- "$temporary" "$destination"; then
    rm -f -- "$temporary"
    return 1
  fi
}
clear_bytecode() {
  local cache
  [[ ! -L "$TARGET/__pycache__" ]] || return 1
  for cache in "$TARGET"/__pycache__/*.pyc; do
    [[ -e "$cache" || -L "$cache" ]] || continue
    rm -f -- "$cache" || return 1
  done
}
check_ready() {
  local marker=$1 invocation current journal successes=0 attempt
  invocation=$(systemctl show --property=InvocationID --value "$UNIT") || return 1
  [[ "$invocation" =~ ^[a-f0-9]{32}$ ]] || return 1
  for attempt in {1..20}; do
    systemctl is-active --quiet "$UNIT" || return 1
    current=$(systemctl show --property=InvocationID --value "$UNIT") || return 1
    [[ "$current" == "$invocation" ]] || return 1
    journal=$(journalctl -u "$UNIT" "_SYSTEMD_INVOCATION_ID=$invocation" --no-pager -o cat) || return 1
    if [[ "$journal" == *"$marker"* ]]; then
      successes=$((successes + 1))
      [[ $successes -lt 3 ]] || return 0
    else
      successes=0
    fi
    sleep 1
  done
  return 1
}
start_receiver() {
  local message
  if ! message=$(LC_ALL=C systemctl reset-failed "$UNIT" 2>&1); then
    [[ "$message" == *"Unit $UNIT not loaded."* ]] || { printf '%s\n' "$message" >&2; return 1; }
  fi
  systemctl start "$UNIT"
}
restore_backup() {
  local name
  systemctl stop "$UNIT" || return 1
  while IFS= read -r name; do
    valid_file "$name" || return 1
    if [[ ! -f "$backup/previous/$name" ]]; then
      rm -f -- "$TARGET/$name" || return 1
    fi
  done < "$backup/applied-files"
  while IFS= read -r name; do
    copy_file "$backup/previous/$name" "$TARGET/$name" || return 1
  done < "$backup/previous-files"
  clear_bytecode || return 1
  if [[ $resume == 1 ]]; then
    start_receiver || return 1
    check_ready 'Orange Pi HDMI display ready' || return 1
  fi
}
finish() {
  local result=$?
  trap - EXIT INT TERM HUP
  if [[ $pending == 1 ]]; then
    printf 'Update failed; restoring receiver files from %s.\n' "$backup" >&2
    if restore_backup; then
      if [[ $recovering == 0 ]]; then rm -f -- "$STATE/recovery-needed"; fi
      printf 'Previous receiver files restored.\n' >&2
    else
      printf 'Automatic recovery failed. Backup retained at %s; run rollback after inspecting service.sh logs.\n' "$STATE/$recovery_goal" >&2
    fi
    [[ $result != 0 ]] || result=1
  fi
  exit "$result"
}

case ${1:-} in
  --help|-h|'')
    printf 'Usage: sudo bash scripts/update.sh {apply <staged-payload>|rollback}\n'
    printf 'From C4: bash scripts/deploy.sh [ORANGE_PI_IP]\n'
    exit 0;;
  apply) [[ $# == 2 ]] || fail 'apply requires the staged payload directory.'; source_dir=$(cd -- "$2" && pwd -P); marker='Cluster receiver ready';;
  rollback) [[ $# == 1 ]] || fail 'rollback takes no arguments.'; marker='Orange Pi HDMI display ready';;
  *) fail 'Unknown update action.';;
esac
[[ $(id -u) == 0 ]] || fail 'Run with sudo or from a root shell.'
[[ -d "$TARGET" && ! -L "$TARGET" && ! -L "$TARGET/scripts" && -f "$TARGET/cluster_receiver.py" ]] || fail 'Install the receiver first; the target must be a real directory.'
[[ -f /etc/systemd/system/cluster-hdmi.service ]] || fail 'Install the receiver service with service.sh enable first.'
install -d -m 700 -- "$STATE"
exec 9> "$STATE/lock"
flock -n 9 || fail 'Another receiver update is in progress.'
if [[ $1 == apply && -s "$STATE/recovery-needed" ]]; then
  fail 'An earlier update needs recovery. Run rollback before another update.'
fi
if [[ $1 == rollback ]]; then
  if [[ -s "$STATE/recovery-needed" ]]; then
    read -r latest < "$STATE/recovery-needed"
    recovering=1
    recovery_goal=$latest
  else
    [[ -s "$STATE/latest" ]] || fail 'No previous receiver update is available.'
    read -r latest < "$STATE/latest"
  fi
  [[ "$latest" =~ ^[0-9]{8}T[0-9]{6}Z\.[a-zA-Z0-9]{8}$ ]] || fail 'Invalid saved backup identifier.'
  source_dir="$STATE/$latest/previous"
  while IFS= read -r name; do
    valid_file "$name" || fail 'Invalid saved payload path.'
    [[ -f "$source_dir/$name" ]] || REMOVE+=("$name")
  done < "$STATE/$latest/applied-files"
fi
[[ "$source_dir" != "$TARGET" ]] || fail 'Use a separate staging directory.'
read_manifest "$source_dir"
if [[ $1 == apply ]]; then
  [[ " ${FILES[*]} " == *' scripts/update.sh '* ]] || fail 'Payload is missing scripts/update.sh.'
fi
for name in "${FILES[@]}"; do
  [[ "$name" != scripts/*.sh ]] || bash -n "$source_dir/$name"
done
# Compile and import without initializing HDMI or writing bytecode. Run this
# before stopping the old receiver, using the same OS Python as the service.
PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 - "$source_dir" <<'PY'
from pathlib import Path
import sys

directory = Path(sys.argv[1])
for path in directory.glob("*.py"):
    compile(path.read_bytes(), str(path), "exec")
sys.path.insert(0, str(directory))
import pygame
import hdmi_display
import cluster_receiver
PY
active=$(systemctl show --property=ActiveState --value "$UNIT")
case "$active" in active|activating|reloading|failed) resume=1;; inactive) resume=0;; *) fail "Unexpected service state: $active";; esac
if [[ $recovering == 1 ]]; then
  # An interrupted update may have left a previously running service stopped.
  read -r resume < "$STATE/$latest/resume"
  [[ "$resume" == 0 || "$resume" == 1 ]] || fail 'Invalid saved service state.'
fi
# Keep a standalone recovery entry point even when the previous version did not
# contain update.sh. It also works after rollback removes that new runtime file.
install -m 700 -- "${BASH_SOURCE[0]}" "$STATE/update.sh.tmp"
mv -f -- "$STATE/update.sh.tmp" "$STATE/update.sh"
backup=$(mktemp -d "$STATE/$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX")
printf '%s\n' "$resume" > "$backup/resume"
install -d -m 700 -- "$backup/previous/scripts"
shopt -s nullglob
for path in "$TARGET"/*.py "$TARGET"/scripts/*.sh "$TARGET/README.md" "$TARGET/requirements.txt" "$TARGET/cluster-hdmi.service"; do
  [[ -e "$path" || -L "$path" ]] || continue
  [[ ! -L "$path" && -f "$path" ]] || fail "Unsafe installed file: $path"
  name=${path#"$TARGET/"}
  valid_file "$name" || fail "Unexpected installed filename: $name"
  install -m 644 -- "$path" "$backup/previous/$name"
  printf '%s\n' "$name" >> "$backup/previous-files"
  (cd -- "$backup/previous" && sha256sum --binary -- "$name") >> "$backup/previous/SHA256SUMS"
done
printf '%s\n' "${FILES[@]}" > "$backup/applied-files"
read_manifest "$backup/previous"
read_manifest "$source_dir"
printf 'Backup: %s\n' "$backup"
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
[[ -n "$recovery_goal" ]] || recovery_goal=${backup##*/}
# Persist the recovery target before any runtime changes, including stopping
# the service. SIGKILL/power loss cannot run the EXIT trap.
printf '%s\n' "$recovery_goal" > "$STATE/recovery-needed.tmp"
mv -f -- "$STATE/recovery-needed.tmp" "$STATE/recovery-needed"
pending=1
systemctl stop "$UNIT"
for name in "${FILES[@]}"; do
  copy_file "$source_dir/$name" "$TARGET/$name"
done
for name in "${REMOVE[@]}"; do
  rm -f -- "$TARGET/$name"
done
# Same-sized files copied within one second can otherwise reuse a stale .pyc,
# including when a failed update is immediately rolled back.
clear_bytecode
if [[ $resume == 1 ]]; then
  start_receiver
  check_ready "$marker" || fail 'Receiver did not become ready in the current service invocation.'
  systemctl status "$UNIT" --no-pager
else
  printf 'The receiver was stopped; leaving it stopped.\n'
fi
printf '%s\n' "${backup##*/}" > "$STATE/latest.tmp"
mv -f -- "$STATE/latest.tmp" "$STATE/latest"
rm -f -- "$STATE/recovery-needed"
pending=0
printf 'Receiver %s complete. Installed unit and boot settings preserved.\n' "$1"
