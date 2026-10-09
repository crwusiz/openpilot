#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/ftp_upload_utils.sh"

readonly LOG_BASE_DIR="/data/log"

resolve_log_path() {
  if [[ "$1" == /* ]]; then printf '%s\n' "$1"
  else printf '%s/%s\n' "$LOG_BASE_DIR" "$(basename "$1")"; fi
}

upload_prefix() {
  printf '%s_%s_%s' "$(date +%y-%m-%d-%H:%M)" "$(get_param "CarName")" "$(get_param "DongleId")"
}

upload_file() {
  local local_file_path="$1"
  local_file_path=$(resolve_log_path "$local_file_path")
  local file_name
  file_name=$(basename "$local_file_path")

  if [ ! -f "$local_file_path" ]; then
    log "ERROR" "Log file not found: $local_file_path"
    return 1
  fi

  log "INFO" "Log file found: $local_file_path"

  local prefix=${2:-}
  if [[ -z "$prefix" ]]; then prefix=$(upload_prefix); fi
  local target_filename="${prefix}_${file_name}"
  local remote_path="/${FTP_DEFAULT_DIR}/${target_filename}"

  log "INFO" "Starting upload to ${FTP_HOST}..."
  log "INFO" "Target: $target_filename"

  if ftp_upload_file "$local_file_path" "$remote_path" --max-time 30 --retry-max-time 60; then
    log "SUCCESS" "Upload completed successfully."
    return 0
  else
    log "ERROR" "Upload failed."
    return 1
  fi
}

upload_pi_log() (
  local c4_log="$1" prefix="$2" directory sidecar collector status=0
  directory=$(mktemp -d "${TMPDIR:-/tmp}/cluster-log-upload.XXXXXXXX") || {
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 로그 수집을 준비하지 못했습니다.'
    return 0
  }
  trap 'rm -rf -- "$directory"' EXIT
  sidecar="$directory/cluster_pi_debug.log"
  collector="${SCRIPT_DIR}/../openpilot/selfdrive/addon/cluster/hdmi_display/pi_log_collector.py"
  if [[ -f "$collector" ]]; then
    "${CLUSTER_PYTHON:-python3}" "$collector" --c4-log "$c4_log" --output "$sidecar" || status=$?
  else
    status=1
  fi
  if (( status != 0 )); then
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 로그 수집에 실패했습니다. 실패 보고서를 함께 업로드합니다.'
  fi
  if [[ ! -s "$sidecar" ]]; then
    printf '=== Orange Pi cluster diagnostics ===\nCollection status: failed\nFailure: Pi collector missing or could not produce a log (exit %s).\n' "$status" > "$sidecar"
    status=1
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: Pi 진단 로그를 얻지 못해 실패 보고서를 준비했습니다.'
  fi
  if upload_file "$sidecar" "$prefix"; then
    if (( status == 0 )); then
      log "SUCCESS" "C4 and Orange Pi logs uploaded with the same filename prefix."
    else
      printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그와 Pi 실패 보고서는 업로드됐지만 Pi 진단 로그 수집에는 실패했습니다.'
    fi
  else
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 로그 또는 실패 보고서 업로드는 실패했습니다.'
  fi
  return 0
)

main() {
  if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Usage: $0 <LOG_FILENAME>${NC}"
    exit 1
  fi

  local log_path prefix
  log_path=$(resolve_log_path "$1")
  prefix=$(upload_prefix)
  if upload_file "$log_path" "$prefix"; then
    if [[ "$(basename "$log_path")" == cluster_debug.log ]]; then
      upload_pi_log "$log_path" "$prefix"
    fi
    exit 0
  else
    exit 1
  fi
}

main "$@"
