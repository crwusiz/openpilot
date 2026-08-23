#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/ftp_upload_utils.sh"

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

  local today car_name dongle_id
  today=$(date +%Y-%m-%d)
  car_name=$(get_param "CarName")
  dongle_id=$(get_param "DongleId")

  local remote_base_dir="/${FTP_DEFAULT_DIR}/${today}_${car_name}_${dongle_id}/${folder_name}"

  # 1. Upload qcamera.ts
  if [ -f "${log_folder}/qcamera.ts" ]; then
    if ftp_upload_file "${log_folder}/qcamera.ts" "${remote_base_dir}/qcamera.ts"; then
      log "INFO" "Uploaded: qcamera.ts"
    else
      log "WARNING" "Failed to upload: qcamera.ts"
    fi
  fi

  # 2. Upload rlog files
  shopt -s nullglob
  for rlog in "${log_folder}"/rlog.*; do
    local fname
    fname=$(basename "$rlog")
    if ftp_upload_file "$rlog" "${remote_base_dir}/${fname}"; then
      log "INFO" "Uploaded: ${fname}"
    else
      log "WARNING" "Failed to upload: ${fname}"
    fi
  done

  # 3. Upload qlog files
  for qlog in "${log_folder}"/qlog.*; do
    local fname
    fname=$(basename "$qlog")
    if ftp_upload_file "$qlog" "${remote_base_dir}/${fname}"; then
      log "INFO" "Uploaded: ${fname}"
    else
      log "WARNING" "Failed to upload: ${fname}"
    fi
  done
  shopt -u nullglob

  log "SUCCESS" "Completed segment ${folder_name}"
}

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
