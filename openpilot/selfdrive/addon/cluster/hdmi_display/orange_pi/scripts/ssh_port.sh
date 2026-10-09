#!/usr/bin/env bash
# Run on the Orange Pi, or automatically after a C4 update over SSH port 22.
set -euo pipefail
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Usage: sudo bash scripts/ssh_port.sh\n'
  printf 'Replace the Orange Pi SSH listener on port 22 with port 9122.\n'
  exit 0
fi
[[ $# == 0 ]] || fail 'Usage: sudo bash scripts/ssh_port.sh'
require_root
SSH_DIR=/etc/ssh
UNIT_DIR=/etc/systemd/system
BACKUP_DIR=/var/lib/cluster-receiver/ssh-port
LOCK_FILE=/run/lock/cluster-ssh-port.lock
exec 9> "$LOCK_FILE"
flock --wait 5 9 || fail 'Another SSH port change is in progress.'
sshd=$(command -v sshd) || sshd=/usr/sbin/sshd
[[ -x "$sshd" ]] || fail 'OpenSSH server (sshd) is missing.'
command -v ss >/dev/null || fail 'Install iproute2 (ss is missing).'
config=$SSH_DIR/sshd_config
[[ -f "$config" && ! -L "$config" ]] || fail 'sshd_config must be a regular file.'
before=$("$sshd" -T)
socket_unit=''
for candidate in ssh.socket sshd.socket; do
  if systemctl is-active --quiet "$candidate"; then socket_unit=$candidate; break; fi
done
if ! grep -Eq '^port 22$|^listenaddress ([^:[:space:]]+|\[[^]]+\]):22([[:space:]]|$)' <<< "$before"; then
  if [[ -z "$socket_unit" || -z $(ss -H -ltn '( sport = :22 )') ]]; then
    printf 'SSH port 22 is not configured; leaving the current SSH ports unchanged.\n'
    exit 0
  fi
fi
service_unit=''
for candidate in ssh.service sshd.service; do
  if [[ $(systemctl show --property=LoadState --value "$candidate") == loaded ]]; then service_unit=$candidate; break; fi
done
[[ -n "$service_unit" ]] || fail 'No OpenSSH systemd service was found.'
install -d -m 700 -- "$BACKUP_DIR"
backup=$(mktemp -d "$BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX")
printf 'SSH configuration backup: %s\n' "$backup"
shopt -s nullglob
files=("$config" "$SSH_DIR"/sshd_config.d/*.conf)
for index in "${!files[@]}"; do
  [[ -f "${files[index]}" && ! -L "${files[index]}" ]] || fail 'SSH configuration includes must be regular files.'
  cp -p -- "${files[index]}" "$backup/$index"
done
socket_file=''
socket_existed=0
if [[ -n "$socket_unit" ]]; then
  socket_file=$UNIT_DIR/$socket_unit.d/90-cluster-port.conf
  [[ ! -L "$socket_file" ]] || fail 'The SSH socket override must not be a symlink.'
  if [[ -e "$socket_file" ]]; then
    cp -p -- "$socket_file" "$backup/socket.conf"
    socket_existed=1
  fi
fi
activate() {
  if [[ -n "$socket_unit" ]]; then
    systemctl daemon-reload
    systemctl restart "$socket_unit" "$service_unit"
  else
    # A reload preserves existing authenticated SSH sessions.
    systemctl reload "$service_unit"
  fi
}
pending=1
finish() {
  local result=$? recovery_failed=0
  trap - EXIT INT TERM HUP
  set +e
  if (( pending )); then
    for index in "${!files[@]}"; do
      cp -p -- "$backup/$index" "${files[index]}" || recovery_failed=1
    done
    if [[ -n "$socket_file" ]]; then
      if (( socket_existed )); then cp -p -- "$backup/socket.conf" "$socket_file" || recovery_failed=1
      else rm -f -- "$socket_file" || recovery_failed=1; fi
    fi
    "$sshd" -t && activate || recovery_failed=1
    if (( recovery_failed )); then printf 'SSH recovery failed; configuration backup: %s\n' "$backup" >&2
    else printf 'SSH port change failed; previous SSH configuration restored.\n' >&2; fi
    (( result != 0 )) || result=1
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
for index in "${!files[@]}"; do
  # Preserve file ownership/mode while replacing explicit port 22 directives.
  cp -p -- "${files[index]}" "$backup/edited"
  sed -E \
    -e 's/^([[:space:]]*[Pp][Oo][Rr][Tt][[:space:]]+)22([[:space:]]*(#.*)?)$/\19122\2/' \
    -e 's/^([[:space:]]*[Ll][Ii][Ss][Tt][Ee][Nn][Aa][Dd][Dd][Rr][Ee][Ss][Ss][[:space:]]+)([^:[:space:]#]+|\[[^]]+\]):22([[:space:]]*(#.*)?)$/\1\2:9122\3/' \
    "${files[index]}" > "$backup/edited"
  mv -f -- "$backup/edited" "${files[index]}"
done
after=$("$sshd" -T)
if ! grep -qx 'port 9122' <<< "$after"; then
  # With only the commented default '#Port 22', sshd needs an explicit port.
  # Prepend before any Include or Match block so this remains a global option.
  cp -p -- "$config" "$backup/edited"
  { printf 'Port 9122\n'; cat -- "$config"; } > "$backup/edited"
  mv -f -- "$backup/edited" "$config"
fi
"$sshd" -t
after=$("$sshd" -T)
grep -qx 'port 9122' <<< "$after" || fail 'SSH port 9122 was not configured.'
if grep -Eq '^port 22$|^listenaddress ([^:[:space:]]+|\[[^]]+\]):22([[:space:]]|$)' <<< "$after"; then
  fail 'Port 22 remains in the SSH configuration; inspect additional Include/Port directives.'
fi
if [[ -n "$socket_file" ]]; then
  install -d -m 755 -- "${socket_file%/*}"
  # Socket activation owns the listening ports independently of sshd. Reset
  # ListenStream and use sshd's effective bind addresses, preserving other ports.
  # Separate IPv6 from IPv4 to avoid two sockets binding the same IPv4 port.
  { printf '[Socket]\nBindIPv6Only=ipv6-only\nListenStream=\n'
    awk '$1 == "listenaddress" && !seen[$2]++ { print "ListenStream=" $2 }' <<< "$after"
  } > "$socket_file"
  chmod 644 -- "$socket_file"
fi
activate
for _attempt in {1..10}; do
  if [[ -n $(ss -H -ltn '( sport = :9122 )') && -z $(ss -H -ltn '( sport = :22 )') ]]; then
    pending=0
    printf 'Orange Pi SSH port changed: 22 -> 9122. Port 22 is no longer listening.\n'
    exit 0
  fi
  sleep 0.2
done
fail 'SSH listener verification failed: port 9122 must be open and port 22 closed.'
