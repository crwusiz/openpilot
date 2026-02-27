#!/usr/bin/env bash

set -euo pipefail

# ==============================================================================
# Import Common Utilities
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/common_utils.sh"

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

upload_file() {
  local file_name="$1"
  local local_file_path="${LOG_BASE_DIR}/${file_name}"

  # 1. Validate file existence
  if [ ! -f "$local_file_path" ]; then
    log "ERROR" "Log file not found: $local_file_path"
    return 1
  fi

  log "INFO" "Log file found: $local_file_path"

  # 2. Check Network
  if ! check_network; then
    return 1
  fi

  # 3. Prepare metadata for filename
  local today
  today=$(date +%y-%m-%d-%H:%M)

  local car_name
  car_name=$(get_param "CarName")

  local dongle_id
  dongle_id=$(get_param "DongleId")

  local target_filename="${today}_${car_name}_${dongle_id}_${file_name}"
  local ftp_url="ftp://${FTP_HOST}:${FTP_PORT}/${FTP_DIR}/${target_filename}"

  log "INFO" "Starting upload to ${FTP_HOST}..."
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
    echo -e "${YELLOW}Usage: $0 <LOG_FILENAME>${NC}"
    exit 1
  fi

  local log_filename="$1"
  log_filename=$(basename "$log_filename")

  if upload_file "$log_filename"; then
    exit 0
  else
    exit 1
  fi
}

main "$@"
