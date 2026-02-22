#!/usr/bin/env bash

set -euo pipefail

# ==============================================================================
# Import Common Utilities
# ==============================================================================
source "/data/common_utils.sh"

# ==============================================================================
# Configuration and Constants
# ==============================================================================
# FTP Configuration
readonly FTP_USER="openpilot"
readonly FTP_PASS="ruF3~Dt8"
readonly FTP_HOST="jmtechn.com"
readonly FTP_PORT="8022"
readonly FTP_DIR="tmux_log"

# Paths
readonly LOG_BASE_DIR="/data"
readonly PARAMS_DIR="/data/params/d"
readonly DUMP_SCRIPT="/data/openpilot/selfdrive/debug/dump.py"

# ==============================================================================
# Utility Functions
# ==============================================================================
# Safely read a parameter file, defaulting to "Unknown" if missing
get_param() {
  local param_name="$1"
  local param_file="${PARAMS_DIR}/${param_name}"

  if [ -f "$param_file" ]; then
    cat "$param_file"
  else
    echo "Unknown"
  fi
}

generate_dump() {
  local service_name="$1"
  local output_file="$2"

  log "INFO" "Starting dump for service: ${service_name}"

  # Check if dump script exists
  if [ ! -f "$DUMP_SCRIPT" ]; then
    log "ERROR" "Dump script not found at $DUMP_SCRIPT"
    return 1
  fi

  # Execute python dump script
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

  # 1. Validate file existence
  if [ ! -f "$local_file_path" ]; then
    log "ERROR" "Log file not found: $local_file_path"
    return 1
  fi

  # 2. Check Network
  if ! check_network; then
    log "ERROR" "Network unavailable. Skipping upload."
    return 1
  fi

  # 3. Prepare metadata for filename
  local today
  today=$(date +%y-%m-%d-%H:%M)

  local car_name
  car_name=$(get_param "CarName")

  local dongle_id
  dongle_id=$(get_param "DongleId")

  local target_filename="${today}_${car_name}_${dongle_id}_${service_name}.log"
  local ftp_url="ftp://${FTP_HOST}:${FTP_PORT}/${FTP_DIR}/${target_filename}"

  log "INFO" "Uploading ${service_name}.log to FTP..."
  log "INFO" "Target: $target_filename"

  # 4. Perform Upload
  if curl --ftp-create-dirs \
          --connect-timeout 30 \
          --retry 3 \
          -T "$local_file_path" \
          -u "${FTP_USER}:${FTP_PASS}" \
          "$ftp_url"; then
    log "SUCCESS" "Upload completed successfully."
    return 0
  else
    log "ERROR" "Upload failed."
    return 1
  fi
}

# ==============================================================================
# Main Execution Flow
# ==============================================================================
main() {
  if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Usage: $0 <SERVICE_NAME>${NC}"
    exit 1
  fi

  local service_name="$1"
  local log_file="${LOG_BASE_DIR}/${service_name}.log"

  # Step 1: Generate Log Dump
  if ! generate_dump "$service_name" "$log_file"; then
    exit 1
  fi

  # Step 2: Upload Log Dump
  if ! upload_dump "$service_name" "$log_file"; then
    exit 1
  fi

  exit 0
}

main "$@"
