#!/usr/bin/env bash
# Keep native thermal protection; change only the PWM fan's minimum at boot.
# Standalone so a saved updater can restore boot files after an old rollback.
set -euo pipefail
BOOT=/boot
DEVICE_TREE=/sys/firmware/devicetree/base
ENV_FILE="$BOOT/orangepiEnv.txt"
OVERLAY="$BOOT/overlay-user/cluster-fan-minimum.dtbo"
TOKEN=cluster-fan-minimum
MIN_PWM=153 # 153 / 255 = 60% duty, not a measured RPM percentage.

fail() { printf '[CLUSTER_FAN] ERROR: %s\n' "$*" >&2; exit 1; }
safe_boot_paths() {
  local path
  for path in "$BOOT" "$BOOT/overlay-user"; do
    [[ ! -L "$path" && ( ! -e "$path" || -d "$path" ) ]] || return 1
  done
  for path in "$ENV_FILE" "$OVERLAY"; do
    [[ ! -L "$path" && ( ! -e "$path" || -f "$path" ) ]] || return 1
  done
}
snapshot() {
  local directory=$1
  safe_boot_paths || fail 'Unsafe fan boot configuration path.'
  [[ ! -L "$directory" && ! -e "$directory/state" && ! -L "$directory/state" ]] || fail 'Unsafe or occupied fan snapshot.'
  install -d -m 700 -- "$directory"
  [[ ! -e "$directory/orangepiEnv.txt" && ! -L "$directory/orangepiEnv.txt" &&
     ! -e "$directory/cluster-fan-minimum.dtbo" && ! -L "$directory/cluster-fan-minimum.dtbo" ]] || fail 'Occupied fan snapshot files.'
  if [[ -f "$ENV_FILE" ]]; then cp -p -- "$ENV_FILE" "$directory/orangepiEnv.txt"; fi
  if [[ -f "$OVERLAY" ]]; then cp -p -- "$OVERLAY" "$directory/cluster-fan-minimum.dtbo"; fi
  printf 'complete\n' > "$directory/state"
}
restore_file() {
  local source=$1 destination=$2 temporary
  [[ ! -L "$source" ]] || return 1
  if [[ -f "$source" ]]; then
    install -d -m 755 -- "${destination%/*}" || return 1
    temporary=$(mktemp "${destination%/*}/.cluster-fan.XXXXXXXX") || return 1
    if ! cp -p -- "$source" "$temporary" || ! mv -f -- "$temporary" "$destination"; then
      rm -f -- "$temporary"
      return 1
    fi
  else
    [[ ! -e "$source" ]] || return 1
    rm -f -- "$destination" || return 1
  fi
}
restore() {
  local directory=$1
  [[ ! -L "$directory" && ! -L "$directory/state" ]] || return 1
  # Versions before fan support have no external snapshot.
  [[ -f "$directory/state" ]] || return 0
  [[ $(< "$directory/state") == complete ]] || return 1
  safe_boot_paths || return 1
  # Put the referenced overlay back before restoring the boot environment.
  restore_file "$directory/cluster-fan-minimum.dtbo" "$OVERLAY" || return 1
  restore_file "$directory/orangepiEnv.txt" "$ENV_FILE"
}

case ${1:-} in
  --help|-h|'')
    printf 'Usage: sudo bash scripts/fan_setup.sh {install|snapshot <directory>|restore <directory>}\n'
    printf 'Orange Pi sun60iw2 PWM fan: minimum 60%%, native thermal speed increases; reboot required.\n'
    exit 0;;
  install) [[ $# == 1 ]] || fail 'install takes no arguments.';;
  snapshot|restore) [[ $# == 2 && -n $2 ]] || fail 'snapshot/restore requires a directory.';;
  *) fail 'Unknown fan setup action.';;
esac
[[ $(id -u) == 0 ]] || fail 'Run with sudo or from a root shell.'
case $1 in
  snapshot) snapshot "$2"; exit 0;;
  restore) restore "$2"; exit $?;;
esac

nodes=()
if [[ $(uname -r) == *sun60iw2* ]]; then
  for candidate in "$DEVICE_TREE"/soc*/pwm-fan; do
    if [[ -r "$candidate/compatible" ]] && tr '\000' '\n' < "$candidate/compatible" | grep -qx 'pwm-fan'; then
      nodes+=("$candidate")
    fi
  done
fi
if [[ ${#nodes[@]} == 0 ]]; then
  printf '[CLUSTER_FAN] Unsupported board/fan device tree; fan configuration was not installed.\n' >&2
  exit 0
fi
[[ ${#nodes[@]} == 1 ]] || fail 'Multiple PWM fan nodes; refusing an ambiguous boot overlay.'
node=${nodes[0]}
target_path=${node#"$DEVICE_TREE"}
[[ $target_path =~ ^/soc[a-zA-Z0-9_,.@+-]*/pwm-fan$ ]] || fail 'Unsupported fan device-tree path.'
safe_boot_paths || fail 'Unsafe fan boot configuration path.'
[[ -f "$ENV_FILE" ]] || fail 'Missing /boot/orangepiEnv.txt; cannot enable the fan overlay.'
[[ ! -f "$node/status" || $(tr -d '\000' < "$node/status") == okay ||
   $(tr -d '\000' < "$node/status") == ok ]] || fail 'The PWM fan is disabled in the device tree.'
[[ -r "$node/cooling-levels" ]] || fail 'Missing PWM fan cooling-levels.'
size=$(wc -c < "$node/cooling-levels")
[[ $size -ge 8 && $size -le 1024 && $((size % 4)) == 0 ]] || fail 'Invalid cooling-levels size.'
raw=$(od -An -v --endian=big -tu4 "$node/cooling-levels")
[[ $raw =~ ^[[:space:]0-9]+$ ]] || fail 'Invalid cooling-levels data.'
levels=()
previous=0
for value in $raw; do
  [[ $value -le 255 && $value -ge $previous ]] || fail 'Invalid PWM fan cooling curve.'
  previous=$value
  if [[ $value -lt $MIN_PWM ]]; then value=$MIN_PWM; fi
  levels+=("$value")
done
[[ $previous == 255 ]] || fail 'Fan cooling curve must retain a 100% maximum.'

temporary=$(mktemp -d /tmp/cluster-fan.XXXXXXXX)
pending=0
finish() {
  local result=$?
  trap - EXIT INT TERM HUP
  if [[ $pending == 1 ]]; then
    if ! restore "$temporary/previous"; then
      printf '[CLUSTER_FAN] ERROR: Boot configuration recovery failed: %s/previous\n' "$temporary" >&2
      exit 1 # Preserve the backup for recovery.
    fi
  fi
  rm -f -- "$temporary/fan.dts" "$temporary/fan.dtbo" "$temporary/env" \
    "$temporary/previous/state" "$temporary/previous/orangepiEnv.txt" "$temporary/previous/cluster-fan-minimum.dtbo"
  rmdir -- "$temporary/previous" "$temporary" 2>/dev/null || true
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Preserve other user overlays and all unrelated boot options. Refuse ambiguous
# duplicate assignments rather than silently shadowing a user's configuration.
found=0
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == user_overlays=* ]]; then
    [[ $found == 0 ]] || fail 'Duplicate user_overlays assignments.'
    found=1
    read -r -a existing_tokens <<< "${line#user_overlays=}"
    tokens=()
    for token in "${existing_tokens[@]}"; do
      [[ $token =~ ^[a-zA-Z0-9_-]+$ ]] || fail 'Unsupported user_overlays syntax.'
      [[ $token == "$TOKEN" ]] || tokens+=("$token")
    done
    # Apply our floor last, after any existing fan-related user overlay.
    printf 'user_overlays=%s\n' "${tokens[*]:+${tokens[*]} }$TOKEN"
  else
    printf '%s\n' "$line"
  fi
done < "$ENV_FILE" > "$temporary/env"
if [[ $found == 0 ]]; then printf 'user_overlays=%s\n' "$TOKEN" >> "$temporary/env"; fi

cat > "$temporary/fan.dts" <<DTS
/dts-v1/;
/plugin/;
/ {
  fragment@0 {
    target-path = "$target_path";
    __overlay__ { cooling-levels = <${levels[*]}>; };
  };
};
DTS
if ! command -v dtc >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y device-tree-compiler
fi
dtc -@ -I dts -O dtb -o "$temporary/fan.dtbo" "$temporary/fan.dts"
[[ -s "$temporary/fan.dtbo" ]] || fail 'Fan overlay compilation produced an empty file.'
snapshot "$temporary/previous"
pending=1
restore_file "$temporary/fan.dtbo" "$OVERLAY"
restore_file "$temporary/env" "$ENV_FILE"
chmod 644 -- "$OVERLAY" "$ENV_FILE"
sync -f "$OVERLAY" "$ENV_FILE"
pending=0
printf '[CLUSTER_FAN] Minimum PWM=60%%; cooling-levels=<%s>. Native thermal protection preserved. Reboot required.\n' "${levels[*]}"
