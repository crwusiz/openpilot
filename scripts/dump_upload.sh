#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/ftp_upload_utils.sh"

readonly LOG_BASE_DIR="/data"
readonly DUMP_SCRIPT="/data/openpilot/tools/script/dump.py"

generate_dump() {
  local service_name="$1"
  local output_file="$2"

  log "INFO" "Starting dump for service: ${service_name}"

  if [ ! -f "$DUMP_SCRIPT" ]; then
    log "ERROR" "Dump script not found at $DUMP_SCRIPT"
    return 1
  fi

  if python3 "$DUMP_SCRIPT" "${service_name}" -c 5 -o "${output_file}"; then
    log "SUCCESS" "Dump generated successfully: ${output_file}"
    return 0
  else
    log "ERROR" "Failed to generate dump for ${service_name}"
    return 1
  fi
}

upload_dump() {
  local service_name="$1"
  local local_file_path="$2"

  if [ ! -f "$local_file_path" ]; then
    log "ERROR" "Log file not found: $local_file_path"
    return 1
  fi

  if ! check_network; then
    log "ERROR" "Network unavailable. Skipping upload."
    return 1
  fi

  local today car_name dongle_id
  today=$(date +%y-%m-%d-%H:%M)
  car_name=$(get_param "CarName")
  dongle_id=$(get_param "DongleId")

  local target_filename="${today}_${car_name}_${dongle_id}_${service_name}.log"
  local remote_path="/${FTP_DEFAULT_DIR}/${target_filename}"

  log "INFO" "Uploading ${service_name}.log to FTP..."
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
    echo -e "${YELLOW}Usage: $0 <SERVICE_NAME>${NC}"
    exit 1
  fi

  local service_name="$1"
  local log_file="${LOG_BASE_DIR}/${service_name}.log"

  if ! generate_dump "$service_name" "$log_file"; then
    exit 1
  fi

  if ! upload_dump "$service_name" "$log_file"; then
    exit 1
  fi

  exit 0
}

main "$@"
