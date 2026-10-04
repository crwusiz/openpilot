#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
  printf 'Usage: sudo bash scripts/service.sh {enable [user]|restart|stop|desktop|status|logs}\n'
  printf 'enable: install the unit, close the desktop and select console boot.\n'
  printf 'desktop: disable the receiver and restore the previous boot target.\n'
}

reset_receiver_failed_state() {
  local message result
  if message=$(LC_ALL=C systemctl reset-failed cluster-hdmi.service 2>&1); then
    return 0
  else
    result=$?
  fi
  # ResetFailedUnit only acts on a unit already in the manager's memory. A new
  # or stopped unit can be unloaded; enable/start will load its installed file.
  if [[ "$message" == *"Unit cluster-hdmi.service not loaded."* ]]; then
    printf 'No loaded receiver state to reset; continuing with service startup.\n'
    return 0
  fi
  printf '%s\n' "$message" >&2
  return "$result"
}
action=${1:-}
case "$action" in
  status)
    [[ $# == 1 ]] || fail 'Usage: bash scripts/service.sh status'
    systemctl is-enabled cluster-hdmi.service || true
    exec systemctl status cluster-hdmi.service --no-pager
    ;;
  logs)
    [[ $# == 1 ]] || fail 'Usage: bash scripts/service.sh logs'
    exec journalctl -u cluster-hdmi.service -n 100 -f
    ;;
  --help|-h|'') usage; exit 0 ;;
  enable|restart|stop|desktop) require_root ;;
  *) usage >&2; exit 1 ;;
esac

case "$action" in
  enable)
    [[ $# -le 2 ]] || fail 'Usage: sudo bash scripts/service.sh enable [user]'
    [[ -f /opt/cluster-receiver/cluster_receiver.py ]] || fail 'Run scripts/install.sh first.'
    account=${2:-orangepi}
    [[ "$account" =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || fail 'Invalid account name.'
    id "$account" >/dev/null 2>&1 || fail "Account $account does not exist; pass the actual service account."
    group=$(id -gn "$account")
    install -d /var/lib/cluster-receiver /etc/systemd/system/cluster-hdmi.service.d
    # Keep the original target when enable is run again after an update.
    if [[ ! -s /var/lib/cluster-receiver/previous-target ]]; then
      systemctl get-default > /var/lib/cluster-receiver/previous-target
    fi
    install -m 644 /opt/cluster-receiver/cluster-hdmi.service /etc/systemd/system/cluster-hdmi.service
    printf '[Service]\nUser=%s\nGroup=%s\n' "$account" "$group" \
      > /etc/systemd/system/cluster-hdmi.service.d/account.conf
    printf 'Enabling the console receiver: closing the desktop and selecting console boot.\n'
    stop_if_installed cluster-hdmi.service
    stop_if_installed display-manager.service
    systemctl daemon-reload
    reset_receiver_failed_state
    systemctl enable --now cluster-hdmi.service
    systemctl set-default multi-user.target
    systemctl status cluster-hdmi.service --no-pager
    ;;
  restart)
    [[ $# == 1 ]] || fail 'Usage: sudo bash scripts/service.sh restart'
    systemctl daemon-reload
    reset_receiver_failed_state
    systemctl restart cluster-hdmi.service
    systemctl status cluster-hdmi.service --no-pager
    ;;
  stop)
    [[ $# == 1 ]] || fail 'Usage: sudo bash scripts/service.sh stop'
    stop_if_installed cluster-hdmi.service
    ;;
  desktop)
    [[ $# == 1 ]] || fail 'Usage: sudo bash scripts/service.sh desktop'
    stop_if_installed cluster-hdmi.service
    if systemctl cat cluster-hdmi.service >/dev/null 2>&1; then
      systemctl disable cluster-hdmi.service
    fi
    target=graphical.target
    if [[ -s /var/lib/cluster-receiver/previous-target ]]; then
      read -r target < /var/lib/cluster-receiver/previous-target
    fi
    [[ "$target" =~ ^[a-zA-Z0-9_.@-]+\.target$ ]] || fail 'Invalid saved boot target.'
    systemctl set-default "$target"
    systemctl start display-manager.service
    printf 'Desktop started; boot target restored to %s.\n' "$target"
    ;;
esac
