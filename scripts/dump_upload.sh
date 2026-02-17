#!/usr/bin/env bash

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <SERVICE_NAME>"
    exit 1
fi

SERVICES=${1}

TODAY=$(date +%y-%m-%d-%H:%M)
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

LOG_FILE="/data/${SERVICES}.log"

log_info "Starting dump for service: ${SERVICES}"
python3 /data/openpilot/selfdrive/debug/dump.py "${SERVICES}" -c 5 -o "${LOG_FILE}"

if check_network; then
  if [ -f "${LOG_FILE}" ]; then
    log_info "Uploading ${SERVICES}.log to FTP..."

    if curl --ftp-create-dirs -T "${LOG_FILE}" -u "${FTP_USER}:${FTP_PASS}" "ftp://${FTP_HOST}:${FTP_PORT}/tmux_log/${TODAY}_${CAR}_${ID}_${SERVICES}.log"; then
      log_success "Upload completed successfully."
    else
      log_error "Upload failed."
      exit 1
    fi
  else
    log_warning "Log file not found: ${LOG_FILE}"
  fi
else
  log_error "Network unavailable. Skipping upload."
  exit 1
fi
