#!/usr/bin/env bash

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <LOG_FILENAME>"
    exit 1
fi

LOG=${1}

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

if [ -f "/data/${LOG}" ]; then
  log_info "Log file found: /data/${LOG}"

  if check_network; then
    log_info "Starting upload to FTP..."

    if curl --ftp-create-dirs -T "/data/${LOG}" -u "${FTP_USER}:${FTP_PASS}" "ftp://${FTP_HOST}:${FTP_PORT}/tmux_log/${TODAY}_${CAR}_${ID}_${LOG}"; then
      log_success "Upload completed successfully."
    else
      log_error "Upload failed."
      exit 1
    fi
  fi
else
  log_warning "Log file not found: /data/${LOG}"
fi
