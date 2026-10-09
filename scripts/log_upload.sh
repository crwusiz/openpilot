#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/ftp_upload_utils.sh"

readonly LOG_BASE_DIR="/data/log"

upload_file() {
  local local_file_path="$1"
  if [[ "$local_file_path" != /* ]]; then
    local_file_path="${LOG_BASE_DIR}/$(basename "$local_file_path")"
  fi
  local file_name
  file_name=$(basename "$local_file_path")

  if [ ! -f "$local_file_path" ]; then
    log "ERROR" "Log file not found: $local_file_path"
    return 1
  fi

  log "INFO" "Log file found: $local_file_path"

  local today car_name dongle_id
  today=$(date +%y-%m-%d-%H:%M)
  car_name=$(get_param "CarName")
  dongle_id=$(get_param "DongleId")

  local target_filename="${today}_${car_name}_${dongle_id}_${file_name}"
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

main() {
  if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Usage: $0 <LOG_FILENAME>${NC}"
    exit 1
  fi

  if upload_file "$1"; then
    exit 0
  else
    exit 1
  fi
}

main "$@"
