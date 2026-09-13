#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/ftp_upload_utils.sh"

readonly LOG_BASE_DIR="/data"

upload_file() {
  local file_name="$1"
  local local_file_path="${LOG_BASE_DIR}/${file_name}"

  if [ ! -f "$local_file_path" ]; then
    log "ERROR" "Log file not found: $local_file_path"
    return 1
  fi

  log "INFO" "Log file found: $local_file_path"

  if ! check_network; then
    return 1
  fi

  local today car_name dongle_id
  today=$(date +%y-%m-%d-%H:%M)
  car_name=$(get_param "CarName")
  dongle_id=$(get_param "DongleId")

  local target_filename="${today}_${car_name}_${dongle_id}_${file_name}"
  local remote_path="/${FTP_DEFAULT_DIR}/${target_filename}"

  log "INFO" "Starting upload to ${FTP_HOST}..."
  log "INFO" "Target: $target_filename"

  if ftp_upload_file "$local_file_path" "$remote_path"; then
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

  local log_filename
  log_filename=$(basename "$1")

  if upload_file "$log_filename"; then
    exit 0
  else
    exit 1
  fi
}

main "$@"
