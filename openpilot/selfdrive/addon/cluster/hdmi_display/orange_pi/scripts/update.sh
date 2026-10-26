#!/usr/bin/env bash
# Standalone so the staged copy can recover even when live files are broken.
set -euo pipefail
umask 077
TARGET=/opt/cluster-receiver
STATE=/var/lib/cluster-receiver/updates
UNIT=cluster-hdmi.service
WIFI_CONF=/etc/systemd/system/cluster-hdmi.service.d/wifi.conf
WIFI_DISPATCHER=/etc/NetworkManager/dispatcher.d/90-cluster-wifi-power
FAN_HELPER="$STATE/fan_setup.sh"
FILES=()
REMOVE=()
pending=0
resume=0
recovering=0
recovery_goal=''
backup=''
wifi_rollback_snapshot=''
fan_rollback_snapshot=''

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
valid_file() {
  [[ "$1" =~ ^([a-zA-Z0-9_]+\.py|README\.md|requirements\.txt|receiver_assets\.txt|cluster-hdmi\.service|scripts/[a-zA-Z0-9_-]+\.sh|assets/fonts/[a-zA-Z0-9_-]+\.ttf|assets/icons/[a-zA-Z0-9_-]+\.png)$ ]]
}
safe_parents() {
  local directory=$1 name=$2 parent=$2
  [[ ! -L "$directory/$name" ]] || return 1
  while [[ "$parent" == */* ]]; do
    parent=${parent%/*}
    [[ ! -L "$directory/$parent" ]] || return 1
    [[ ! -e "$directory/$parent" || -d "$directory/$parent" ]] || return 1
  done
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
    safe_parents "$directory" "$name" || fail "Unsafe payload directory: $name"
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
  local source=$1 destination=$2 temporary relative directory=$TARGET
  safe_parents "$TARGET" "${destination#"$TARGET/"}" || return 1
  # Repair every ancestor, including an existing assets/ directory left at 700
  # by an older root-owned upload. Fixing fonts/ and icons/ alone is not enough
  # for the unprivileged receiver account to traverse assets/.
  relative=${destination#"$TARGET/"}
  while [[ "$relative" == */* ]]; do
    directory="$directory/${relative%%/*}"
    install -d -m 755 -- "$directory" || return 1
    relative=${relative#*/}
  done
  if [[ -f "$destination" ]] && cmp -s -- "$source" "$destination"; then
    chmod 644 -- "$destination" || return 1
    # Explicit success is required when called from the failed-update EXIT trap.
    return 0
  fi
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
snapshot_wifi_state() {
  local directory=$1 path name
  install -d -m 700 -- "$directory" || return 1
  for name in wifi.conf dispatcher; do
    if [[ "$name" == wifi.conf ]]; then path=$WIFI_CONF; else path=$WIFI_DISPATCHER; fi
    [[ ! -L "$path" ]] || return 1
    if [[ -e "$path" ]]; then
      [[ -f "$path" ]] || return 1
      cp -p -- "$path" "$directory/$name" || return 1
    fi
  done
  printf 'complete\n' > "$directory/state"
}
restore_wifi_state() {
  local directory=$1 path name
  # Older saved updates have no external Wi-Fi snapshot. Do not infer or erase
  # their original configuration; new updates always record presence/absence.
  [[ -f "$directory/state" ]] || return 0
  [[ $(< "$directory/state") == complete ]] || return 1
  for name in wifi.conf dispatcher; do
    if [[ "$name" == wifi.conf ]]; then path=$WIFI_CONF; else path=$WIFI_DISPATCHER; fi
    [[ ! -L "$path" ]] || return 1
    if [[ -f "$directory/$name" ]]; then
      [[ ! -L "$directory/$name" ]] || return 1
      install -d -m 755 -- "${path%/*}" || return 1
      cp -p -- "$directory/$name" "$path" || return 1
    else
      rm -f -- "$path" || return 1
    fi
  done
  systemctl daemon-reload
}
snapshot_fan_state() {
  local directory=$1
  # Saved updates from before fan support may have no helper or fan snapshot.
  [[ -e "$FAN_HELPER" || -L "$FAN_HELPER" ]] || return 0
  [[ -f "$FAN_HELPER" && ! -L "$FAN_HELPER" ]] || return 1
  bash "$FAN_HELPER" snapshot "$directory"
}
restore_fan_state() {
  local directory=$1
  [[ -d "$directory" ]] || return 0
  [[ -f "$FAN_HELPER" && ! -L "$FAN_HELPER" ]] || return 1
  bash "$FAN_HELPER" restore "$directory"
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
    safe_parents "$TARGET" "$name" || return 1
    if [[ ! -f "$backup/previous/$name" ]]; then
      rm -f -- "$TARGET/$name" || return 1
    fi
  done < "$backup/applied-files"
  while IFS= read -r name; do
    copy_file "$backup/previous/$name" "$TARGET/$name" || return 1
  done < "$backup/previous-files"
  clear_bytecode || return 1
  restore_wifi_state "$backup/wifi-system" || return 1
  restore_fan_state "$backup/fan-system" || return 1
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
  wifi_rollback_snapshot="$STATE/$latest/wifi-system"
  fan_rollback_snapshot="$STATE/$latest/fan-system"
  while IFS= read -r name; do
    valid_file "$name" || fail 'Invalid saved payload path.'
    [[ -f "$source_dir/$name" ]] || REMOVE+=("$name")
  done < "$STATE/$latest/applied-files"
fi
[[ "$source_dir" != "$TARGET" ]] || fail 'Use a separate staging directory.'
read_manifest "$source_dir"
if [[ $1 == apply ]]; then
  # Rollback manifests may belong to releases predating these helpers. Require
  # the current receiver dependencies only for a newly uploaded package.
  for name in control_socket.py display_controls.py texture_presenter.py touch_status.py receiver_build.py receiver_resources.py \
    h264_receiver.py h264_protocol.py h264_scene.py scene_layers.py path_worker.py cluster_renderer.py cluster_render_support.py receiver_assets.txt \
    assets/fonts/NotoSansKR-Bold.ttf scripts/prepare_h264.sh scripts/package_sources.sh \
    scripts/update.sh scripts/ensure_wifi.sh scripts/wifi_boot_setup.sh scripts/wifi_power_save.sh scripts/fan_setup.sh; do
    [[ " ${FILES[*]} " == *" $name "* ]] || fail "Payload is missing $name."
  done
  while IFS= read -r name || [[ -n "$name" ]]; do
    name=${name%$'\r'}
    [[ "$name" =~ ^assets/(fonts/[a-zA-Z0-9_-]+\.ttf|icons/[a-zA-Z0-9_-]+\.png)$ ]] || fail 'Invalid receiver asset path.'
    [[ " ${FILES[*]} " == *" $name "* ]] || fail "Payload is missing $name."
  done < "$source_dir/receiver_assets.txt"
  # Only skip a previously completed installation. Legacy installs still run
  # setup once, even if their runtime files happen to match this package.
  unchanged=1
  for name in "${FILES[@]}"; do
    safe_parents "$TARGET" "$name" || fail "Unsafe installed path: $name"
    if ! cmp -s -- "$source_dir/$name" "$TARGET/$name"; then unchanged=0; fi
  done
  for name in cluster_protocol.py frame_stream.py tcp_diagnostics.py; do
    if [[ -e "$TARGET/$name" ]]; then unchanged=0; fi
  done
  if [[ $unchanged == 1 && -s "$STATE/version" ]]; then
    printf 'CLUSTER_PI_UNCHANGED: Receiver already up to date; no restart or reboot needed.\n'
    exit 0
  fi
fi
for name in "${FILES[@]}"; do
  [[ "$name" != scripts/*.sh ]] || bash -n "$source_dir/$name"
done
if [[ $1 == apply ]]; then
  # Keep the live receiver running until all mandatory H.264 dependencies are ready.
  bash "$source_dir/scripts/prepare_h264.sh" || fail 'H.264 dependencies are unavailable; the running receiver was preserved.'
fi
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
if [[ $1 == apply ]]; then
  # Keep boot-configuration recovery usable after rollback removes the newly
  # added helper from an older standalone receiver package.
  install -m 700 -- "$source_dir/scripts/fan_setup.sh" "$STATE/fan_setup.sh.tmp"
  mv -f -- "$STATE/fan_setup.sh.tmp" "$FAN_HELPER"
elif [[ ! -e "$FAN_HELPER" && -f "$(dirname -- "${BASH_SOURCE[0]}")/fan_setup.sh" ]]; then
  install -m 700 -- "$(dirname -- "${BASH_SOURCE[0]}")/fan_setup.sh" "$FAN_HELPER"
fi
backup=$(mktemp -d "$STATE/$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX")
printf '%s\n' "$resume" > "$backup/resume"
snapshot_wifi_state "$backup/wifi-system" || fail 'Cannot safely back up receiver Wi-Fi setup.'
snapshot_fan_state "$backup/fan-system" || fail 'Cannot safely back up Orange Pi fan boot setup.'
install -d -m 700 -- "$backup/previous/scripts"
shopt -s nullglob
previous_paths=("$TARGET"/*.py "$TARGET"/scripts/*.sh "$TARGET/README.md" "$TARGET/requirements.txt" "$TARGET/receiver_assets.txt" "$TARGET/cluster-hdmi.service")
[[ ! -L "$TARGET/assets" ]] || fail 'Unsafe installed assets directory.'
if [[ -d "$TARGET/assets" ]]; then
  [[ -z $(find "$TARGET/assets" -type l -print -quit) ]] || fail 'Installed assets contain symlinks.'
  while IFS= read -r -d '' path; do previous_paths+=("$path"); done < <(find "$TARGET/assets" -type f -print0)
fi
for path in "${previous_paths[@]}"; do
  [[ -e "$path" || -L "$path" ]] || continue
  [[ ! -L "$path" && -f "$path" ]] || fail "Unsafe installed file: $path"
  name=${path#"$TARGET/"}
  valid_file "$name" || fail "Unexpected installed filename: $name"
  install -D -m 644 -- "$path" "$backup/previous/$name"
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
if [[ $1 == apply ]]; then
  # Boot-only preparation can install dtc without interrupting the current
  # receiver. The saved snapshot already covers any resulting boot changes.
  bash "$FAN_HELPER" install
fi
# Retire obsolete receiver modules; the complete previous version is backed up
# above and remains available to the existing rollback/recovery mechanism.
if [[ $1 == apply ]]; then
  for name in cluster_protocol.py frame_stream.py tcp_diagnostics.py; do
    [[ " ${FILES[*]} " == *" $name "* ]] || REMOVE+=("$name")
  done
fi
systemctl stop "$UNIT"
for name in "${FILES[@]}"; do
  copy_file "$source_dir/$name" "$TARGET/$name"
done
for name in "${REMOVE[@]}"; do
  safe_parents "$TARGET" "$name" || fail "Unsafe installed removal path: $name"
  rm -f -- "$TARGET/$name"
done
# Same-sized files copied within one second can otherwise reuse a stale .pyc,
# including when a failed update is immediately rolled back.
clear_bytecode
if [[ $1 == apply ]]; then
  bash "$TARGET/scripts/wifi_boot_setup.sh" install
  systemctl daemon-reload
  # A running SSH/receiver connection is preserved: change only powersave,
  # never reapply/reconnect the profile or restart NetworkManager.
  if ! timeout --kill-after=2s 8s bash "$TARGET/scripts/wifi_power_save.sh"; then
    printf '[CLUSTER_WIFI_POWER] Live power-save preparation failed or timed out.\n' >&2
  fi
else
  restore_wifi_state "$wifi_rollback_snapshot"
  restore_fan_state "$fan_rollback_snapshot"
fi
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
# A human-readable UTC apply timestamp plus the exact package identity. The
# timestamp is informational; incremental decisions always compare file hashes.
package_hash=$(sed 's/^\([a-f0-9]\{64\}\) \*/\1  /' "$source_dir/SHA256SUMS" | LC_ALL=C sort | sha256sum | cut -d ' ' -f 1)
printf '%s %s\n' "$(date -u +%Y%m%d%H%M%S)" "$package_hash" > "$STATE/version.tmp"
mv -f -- "$STATE/version.tmp" "$STATE/version"
copy_file "$STATE/version" "$TARGET/receiver-version"
if [[ $1 == apply ]]; then
  # Optional telemetry setup runs only after a successful receiver transaction.
  # It grants one exact read command without changing the service account.
  if ! /usr/bin/python3 "$TARGET/touch_status.py" --install-gpu-access </dev/null; then
    printf '[CLUSTER_GPU_ACCESS] GPU reader setup unavailable; receiver update remains complete.\n' >&2
  fi
fi
printf 'Receiver %s complete. Receiver account, HDMI options and boot target preserved.\n' "$1"
