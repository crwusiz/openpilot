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
readonly FTP_ROOT_DIR="tmux_log"

# Paths
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
  local local_path="$1"
  local remote_path="$2"
  local file_desc="$3"

  local ftp_url="ftp://${FTP_HOST}:${FTP_PORT}${remote_path}"

  if curl --ftp-create-dirs \
          --connect-timeout 30 \
          --retry 3 \
          -T "$local_path" \
          -u "${FTP_USER}:${FTP_PASS}" \
          "$ftp_url"; then
    log "INFO" "Uploaded: ${file_desc}"
    return 0
  else
    log "WARNING" "Failed to upload: ${file_desc}"
    return 1
  fi
}

process_segment() {
  local log_folder="$1"
  local current_idx="$2"
  local total_count="$3"

  local folder_name
  folder_name=$(basename "$log_folder")

  log "INFO" "Processing segment ${current_idx}/${total_count}: ${folder_name}"

  if [ ! -d "$log_folder" ]; then
    log "WARNING" "Directory not found: $log_folder. Skipping..."
    return 1
  fi

  local today
  today=$(date +%Y-%m-%d)

  local car_name
  car_name=$(get_param "CarName")

  local dongle_id
  dongle_id=$(get_param "DongleId")

  local remote_base_dir="/${FTP_ROOT_DIR}/${today}_${car_name}_${dongle_id}/${folder_name}"

  # 1. Upload qcamera.ts
  if [ -f "${log_folder}/qcamera.ts" ]; then
    upload_file "${log_folder}/qcamera.ts" \
                "${remote_base_dir}/qcamera.ts" \
                "qcamera.ts" || true
  fi

  # 2. Upload rlog files
  shopt -s nullglob
  for rlog in "${log_folder}"/rlog.*; do
    local fname
    fname=$(basename "$rlog")
    upload_file "$rlog" "${remote_base_dir}/${fname}" "$fname" || true
  done

  # 3. Upload qlog files
  for qlog in "${log_folder}"/qlog.*; do
    local fname
    fname=$(basename "$qlog")
    upload_file "$qlog" "${remote_base_dir}/${fname}" "$fname" || true
  done
  shopt -u nullglob

  log "SUCCESS" "Completed segment ${folder_name}"
}

# ==============================================================================
# Main Execution Flow
# ==============================================================================
main() {
  if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Usage: $0 <LOG_FOLDER1> [LOG_FOLDER2] ...${NC}"
    exit 1
  fi

  if ! check_network; then
    exit 1
  fi

  local total_segments=$#
  local current_segment=0

  log "INFO" "Starting route upload with ${total_segments} segments"

  for log_folder in "$@"; do
    current_segment=$((current_segment + 1))
    process_segment "$log_folder" "$current_segment" "$total_segments"
  done

  log "SUCCESS" "Route upload complete (${total_segments} segments processed)"
  exit 0
}

main "$@"
