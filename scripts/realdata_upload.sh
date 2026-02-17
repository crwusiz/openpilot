#!/usr/bin/env bash

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <LOG_FOLDER1> [LOG_FOLDER2] [LOG_FOLDER3] ..."
    exit 1
fi

TODAY=$(date +%Y-%m-%d)
CAR=$(cat /data/params/d/CarName)
ID=$(cat /data/params/d/DongleId)

FTP_USER="openpilot"
FTP_PASS="ruF3~Dt8"
FTP_HOST="jmtechn.com"
FTP_PORT="8022"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
  echo -e "${BLUE}   [INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
  echo -e "${RED}  [ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

check_network() {
  log_info "Checking network connectivity..."
  local dns_servers=("8.8.8.8" "8.8.4.4" "1.1.1.1" "1.0.0.1")
  local connected=1

  for dns in "${dns_servers[@]}"; do
    if ping -c 3 -W 5 "$dns" > /dev/null 2>&1; then
      log_success "Network connectivity confirmed via $dns."
      connected=0
      break
    else
      log_warning "Failed to reach $dns."
    fi
  done

  if [ $connected -eq 0 ]; then
    return 0
  else
    log_error "All network connectivity tests failed."
    return 1
  fi
}

upload_file() {
  local filename="$1"
  local remote_filename="$2"
  local remote_path="$3"

  if curl --ftp-create-dirs -T "$filename" -u "$FTP_USER:$FTP_PASS" "ftp://${FTP_HOST}:${FTP_PORT}${remote_path}"; then
      return 0
  else
      log_error "Failed to upload ${remote_filename}"
      return 1
  fi
}

log_info "Starting route upload with ${#} segments"

if ! check_network; then
  exit 1
fi

TOTAL_SEGMENTS=$#
CURRENT_SEGMENT=0

for LOG_FOLDER in "$@"; do
  CURRENT_SEGMENT=$((CURRENT_SEGMENT + 1))
  LOG_FOLDER_NAME=$(basename "$LOG_FOLDER")

  log_info "Processing segment ${CURRENT_SEGMENT}/${TOTAL_SEGMENTS}: ${LOG_FOLDER_NAME}"

  if [ ! -d "$LOG_FOLDER" ]; then
    log_warning "Directory $LOG_FOLDER does not exist, skipping..."
    continue
  fi

  log_info "Creating remote directories..."


  ftp -n << EOF > /dev/null 2>&1
open $FTP_HOST $FTP_PORT
user $FTP_USER $FTP_PASS
mkdir /tmux_log/${TODAY}_${CAR}_${ID}
mkdir /tmux_log/${TODAY}_${CAR}_${ID}/${LOG_FOLDER_NAME}
bye
EOF

    # qcamera.ts
    if [ -f "${LOG_FOLDER}/qcamera.ts" ]; then
      log_info "Uploading qcamera.ts from ${LOG_FOLDER_NAME}"
      remote_path="/tmux_log/${TODAY}_${CAR}_${ID}/${LOG_FOLDER_NAME}/qcamera.ts"
      if ! upload_file "${LOG_FOLDER}/qcamera.ts" "qcamera.ts" "$remote_path"; then
        log_warning "Skipping qcamera.ts due to upload failure."
      fi
    fi

    # rlog files
    shopt -s nullglob
    for rlog_file in "${LOG_FOLDER}"/rlog.*; do
        filename=$(basename "$rlog_file")
        log_info "Uploading ${filename} from ${LOG_FOLDER_NAME}"
        remote_path="/tmux_log/${TODAY}_${CAR}_${ID}/${LOG_FOLDER_NAME}/${filename}"
        if ! upload_file "$rlog_file" "$filename" "$remote_path"; then
           log_warning "Skipping ${filename} due to upload failure."
        fi
    done

    # qlog files
    for qlog_file in "${LOG_FOLDER}"/qlog.*; do
        filename=$(basename "$qlog_file")
        log_info "Uploading ${filename} from ${LOG_FOLDER_NAME}"
        remote_path="/tmux_log/${TODAY}_${CAR}_${ID}/${LOG_FOLDER_NAME}/${filename}"
        if ! upload_file "$qlog_file" "$filename" "$remote_path"; then
           log_warning "Skipping ${filename} due to upload failure."
        fi
    done
    shopt -u nullglob

    log_success "Completed segment ${CURRENT_SEGMENT}/${TOTAL_SEGMENTS}: ${LOG_FOLDER_NAME}"
done

log_success "Route upload complete (${TOTAL_SEGMENTS} segments processed)"
exit 0
