#!/usr/bin/env bash
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
probe=0
card=${SDL_KMSDRM_DEVICE_INDEX:-}
output=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --egl) probe=1; shift ;;
    --card) [[ $# -ge 2 ]] || fail '--card needs a number'; card=$2; shift 2 ;;
    --output) [[ $# -ge 2 && -n "$2" ]] || fail '--output needs a path'; output=$2; shift 2 ;;
    --help|-h)
      printf 'Usage: sudo bash scripts/diagnose.sh [--egl] [--card N] [--output /tmp/report.log]\n'
      printf 'Collects diagnostics without stopping services. --egl allocates GBM/EGL resources without a modeset.\n'
      exit 0
      ;;
    *) fail "Unknown option: $1" ;;
  esac
done
if [[ -z "$card" ]]; then
  card=$(hdmi_card_index) || card=0
fi
[[ "$card" =~ ^[0-9]+$ ]] || fail 'DRM card must be a nonnegative integer.'
umask 077
if [[ -z "$output" ]]; then
  output=$(mktemp /tmp/cluster-diagnostics.XXXXXXXX.log) || fail 'Could not create the diagnostic log.'
fi

collect() {
  local item interface probe_status=0
  printf 'Cluster display diagnostics: %s\n' "$(date -Is)"
  printf '\n--- OS and kernel ---\n'
  cat /etc/os-release
  uname -a
  printf '\n--- Python, SDL and libraries ---\n'
  export PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONUNBUFFERED=1
  if [[ "$probe" == 1 ]]; then
    /usr/bin/python3 "$PACKAGE_DIR/diagnose_display.py" --egl --card "$card" || probe_status=$?
  else
    /usr/bin/python3 "$PACKAGE_DIR/diagnose_display.py" || probe_status=$?
  fi
  printf '\n--- DRM cards and HDMI modes ---\n'
  ls -l /dev/dri /sys/class/drm
  for item in /sys/class/drm/card*-HDMI-A-*; do
    [[ -d "$item" ]] || continue
    printf '\n%s\n' "$item"
    cat "$item/status" "$item/modes"
  done
  printf '\n--- DRM clients (run with sudo for debugfs access) ---\n'
  for item in /sys/kernel/debug/dri/*/clients; do
    [[ -r "$item" ]] || continue
    printf '\n%s\n' "$item"
    cat "$item"
  done
  printf '\n--- EGL vendor configuration ---\n'
  for item in /usr/share/glvnd/egl_vendor.d/*.json; do
    [[ -r "$item" ]] || continue
    printf '\n%s\n' "$item"
    cat "$item"
  done
  printf '\n--- Graphics libraries ---\n'
  ldconfig -p | awk '/lib(EGL|GLES|gbm|drm|pvr|IMG)/'
  printf '\n--- Graphics packages ---\n'
  dpkg-query -W python3-pygame 'libsdl2*' libgbm1 libegl1 libgles2 '*pvr*' 'xserver-xorg-img*'
  printf '\n--- Network ---\n'
  ip -4 addr
  ip -4 route
  if command -v iw >/dev/null; then
    printf '\n--- Wi-Fi power saving, signal and link rate ---\n'
    for item in /sys/class/net/*/wireless; do
      [[ -d "$item" ]] || continue
      interface=${item%/wireless}
      interface=${interface##*/}
      iw dev "$interface" get power_save
      iw dev "$interface" link
    done
  fi
  printf '\n--- Display services ---\n'
  systemctl status cluster-hdmi.service display-manager.service --no-pager
  printf '\n--- Receiver journal ---\n'
  journalctl -u cluster-hdmi.service -b -n 100 --no-pager
  printf '\nProbe/metadata exit status: %s\n' "$probe_status"
  return "$probe_status"
}
collect 2>&1 | tee "$output"
status=$?
printf '\nDiagnostic log: %s\n' "$output"
exit "$status"
