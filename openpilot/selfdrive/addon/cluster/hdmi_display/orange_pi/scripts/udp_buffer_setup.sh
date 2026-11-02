#!/usr/bin/env bash
# Receiver-owned permission ceiling only; each ICE socket still sets SO_RCVBUF.
# Standalone so a saved updater can also restore versions predating this helper.
set -euo pipefail
umask 077
MAX_FILE=/proc/sys/net/core/rmem_max
UNIT_CONF=/etc/systemd/system/cluster-hdmi.service.d/udp-buffer.conf
TARGET=/opt/cluster-receiver
MINIMUM=1048576

fail() { printf '[CLUSTER_UDP_LIMIT] ERROR: %s\n' "$*" >&2; exit 1; }
safe_paths() {
  local parent=${UNIT_CONF%/*}
  [[ ! -L "$MAX_FILE" && -f "$MAX_FILE" && ! -L "$UNIT_CONF" && ( ! -e "$UNIT_CONF" || -f "$UNIT_CONF" ) ]] || return 1
  while [[ "$parent" != / && "$parent" != . ]]; do
    [[ ! -L "$parent" && ( ! -e "$parent" || -d "$parent" ) ]] || return 1
    [[ "$parent" == */* ]] || break
    parent=${parent%/*}
    [[ -n "$parent" ]] || break
  done
}
read_max() {
  local value
  value=$(< "$MAX_FILE")
  [[ "$value" =~ ^[0-9]{1,10}$ && $value -gt 0 && $value -le 2147483647 ]] || return 1
  printf '%s\n' "$value"
}
validate_snapshot() {
  local directory=$1 name
  [[ ! -L "$directory" && -d "$directory" ]] || return 1
  for name in state before.conf before.max intent after.conf after.max config_changed runtime_changed; do
    [[ ! -L "$directory/$name" && ( ! -e "$directory/$name" || -f "$directory/$name" ) ]] || return 1
  done
  [[ -f "$directory/state" && $(< "$directory/state") == complete ]] || return 1
}
snapshot() {
  local directory=$1 value
  safe_paths || fail 'Unsafe UDP setup path.'
  [[ ! -L "$directory" && ! -e "$directory/state" && ! -L "$directory/state" ]] || fail 'Occupied UDP snapshot.'
  install -d -m 700 -- "$directory"
  local name
  for name in before.conf before.max intent after.conf after.max config_changed runtime_changed; do
    [[ ! -e "$directory/$name" && ! -L "$directory/$name" ]] || fail 'Occupied UDP snapshot file.'
  done
  value=$(read_max) || fail 'Cannot read UDP receive buffer ceiling.'
  [[ ! -f "$UNIT_CONF" ]] || cp -p -- "$UNIT_CONF" "$directory/before.conf"
  printf '%s\n' "$value" > "$directory/before.max"
  printf 'complete\n' > "$directory/state"
}
same_config() {
  local expected=$1
  if [[ -f "$expected" ]]; then
    [[ -f "$UNIT_CONF" ]] && cmp -s -- "$expected" "$UNIT_CONF"
  else
    [[ ! -e "$UNIT_CONF" ]]
  fi
}
write_config() {
  local source=$1 temporary
  if [[ -f "$source" ]]; then
    install -d -m 755 -- "${UNIT_CONF%/*}" || return 1
    temporary=$(mktemp "${UNIT_CONF%/*}/.cluster-udp.XXXXXXXX") || return 1
    if ! install -m 644 -- "$source" "$temporary" || ! mv -f -- "$temporary" "$UNIT_CONF"; then
      rm -f -- "$temporary"
      return 1
    fi
  else
    rm -f -- "$UNIT_CONF" || return 1
  fi
}
prepare_intent() {
  local directory=$1 wanted_config=$2 wanted_max=$3 current
  [[ -n "$directory" ]] || return 0
  validate_snapshot "$directory" || return 1
  [[ ! -e "$directory/intent" ]] || return 1
  # A transaction cannot take ownership of an external edit made since backup.
  same_config "$directory/before.conf" || return 1
  current=$(read_max) || return 1
  printf '%s\n' "$current" > "$directory/before.max"
  [[ ! -f "$wanted_config" ]] || cp -p -- "$wanted_config" "$directory/after.conf"
  printf '%s\n' "$wanted_max" > "$directory/after.max"
  if same_config "$wanted_config"; then printf '0\n'; else printf '1\n'; fi > "$directory/config_changed"
  if [[ "$current" == "$wanted_max" ]]; then printf '0\n'; else printf '1\n'; fi > "$directory/runtime_changed"
  # Persist the expected post-state before either mutation, including SIGKILL.
  printf 'complete\n' > "$directory/intent"
}
restore() {
  local directory=$1 tracking=${2:-} current desired config
  [[ -e "$directory" || -L "$directory" ]] || return 0 # Legacy backup.
  validate_snapshot "$directory" || return 1
  [[ -f "$directory/intent" ]] || return 0 # Snapshot never applied.
  [[ $(< "$directory/intent") == complete ]] || return 1
  safe_paths || return 1
  if ! same_config "$directory/after.conf"; then
    printf '[CLUSTER_UDP_LIMIT] External drop-in change preserved; runtime ceiling retained.\n' >&2
    return 0
  fi
  current=$(read_max) || return 1
  desired=$current
  config="$directory/after.conf"
  if [[ $(< "$directory/config_changed") == 1 ]]; then config="$directory/before.conf"; fi
  if [[ $(< "$directory/runtime_changed") == 1 && "$current" == "$(< "$directory/after.max")" ]]; then
    desired=$(< "$directory/before.max")
    [[ "$desired" =~ ^[0-9]{1,10}$ && $desired -gt 0 && $desired -le 2147483647 ]] || return 1
  fi
  prepare_intent "$tracking" "$config" "$desired" || return 1
  write_config "$config" || return 1
  if [[ "$desired" != "$current" ]]; then printf '%s\n' "$desired" > "$MAX_FILE" || return 1; fi
  printf '[CLUSTER_UDP_LIMIT] Saved receiver setup restored; rmem_max=%s.\n' "$desired"
}
apply_floor() {
  local current desired
  safe_paths || return 1
  current=$(read_max) || return 1
  desired=$current
  [[ $desired -ge $MINIMUM ]] || desired=$MINIMUM
  if [[ "$desired" != "$current" ]]; then printf '%s\n' "$desired" > "$MAX_FILE" || return 1; fi
  [[ $(read_max) -ge $MINIMUM ]] || return 1
  printf '[CLUSTER_UDP_LIMIT] before=%s | actual=%s | minimum=%s | rmem_default=unchanged\n' "$current" "$(read_max)" "$MINIMUM"
}

case ${1:-} in
  --help|-h|'') printf 'Usage: sudo bash scripts/udp_buffer_setup.sh {install [snapshot]|apply|snapshot <directory>|restore <directory> [tracking-snapshot]}\n'; exit 0;;
  install) [[ $# -le 2 ]] || fail 'install takes an optional snapshot directory.';;
  apply) [[ $# == 1 ]] || fail 'apply takes no arguments.';;
  snapshot) [[ $# == 2 && -n $2 ]] || fail 'snapshot requires a directory.';;
  restore) [[ $# == 2 || $# == 3 ]] || fail 'restore requires a snapshot and optional tracking snapshot.';;
  *) fail 'Unknown UDP setup action.';;
esac
[[ $(id -u) == 0 ]] || fail 'Run with sudo or from a root shell.'
case $1 in
  apply) apply_floor; exit $?;;
  snapshot) snapshot "$2"; exit 0;;
  restore) restore "$2" "${3:-}"; exit $?;;
esac
safe_paths || fail 'Unsafe UDP setup path.'
temporary=$(mktemp -d /tmp/cluster-udp.XXXXXXXX)
tracking=${2:-$temporary/snapshot}
pending=0
finish() {
  local result=$?
  trap - EXIT INT TERM HUP
  if [[ $pending == 1 ]] && ! restore "$tracking"; then
    printf '[CLUSTER_UDP_LIMIT] Recovery failed; snapshot retained at %s.\n' "$tracking" >&2
    exit 1
  fi
  rm -f -- "$temporary/config"
  if [[ "$tracking" == "$temporary/snapshot" ]]; then
    rm -f -- "$tracking/state" "$tracking/before.conf" "$tracking/before.max" "$tracking/intent" \
      "$tracking/after.conf" "$tracking/after.max" "$tracking/config_changed" "$tracking/runtime_changed"
    rmdir -- "$tracking" 2>/dev/null || true
  fi
  rmdir -- "$temporary" 2>/dev/null || true
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
cat > "$temporary/config" <<UNIT
# Managed by cluster udp_buffer_setup.sh; receiver socket receive ceiling only.
[Service]
ExecStartPre=-+/bin/bash $TARGET/scripts/udp_buffer_setup.sh apply
UNIT
[[ ! -e "$UNIT_CONF" ]] || cmp -s -- "$temporary/config" "$UNIT_CONF" || fail 'Existing UDP drop-in differs; preserving the external configuration.'
[[ $# == 2 ]] || snapshot "$tracking"
current=$(read_max) || fail 'Cannot read UDP receive buffer ceiling.'
desired=$current
[[ $desired -ge $MINIMUM ]] || desired=$MINIMUM
prepare_intent "$tracking" "$temporary/config" "$desired" || fail 'Cannot record UDP setup ownership.'
pending=1
write_config "$temporary/config"
apply_floor
pending=0
printf '[CLUSTER_UDP_LIMIT] Receiver boot guard installed; higher existing ceilings retained.\n'
