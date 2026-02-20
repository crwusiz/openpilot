#!/usr/bin/env bash

set -euo pipefail

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

# Color Codes
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# ==============================================================================
# Utility Functions
# ==============================================================================

log() {
  local level="$1"
  local msg="$2"
  local color=""
  local tag=""

  case "$level" in
    "INFO")    color="${BLUE}";   tag="   [INFO]";;
    "SUCCESS") color="${GREEN}";  tag="[SUCCESS]";;
    "WARNING") color="${YELLOW}"; tag="[WARNING]";;
    "ERROR")   color="${RED}";    tag="  [ERROR]";;
    *)         color="${NC}";     tag="[UNKNOWN]";;
  esac

  echo -e "${color}${tag}${NC} $(date '+%Y-%m-%d %H:%M:%S') - ${msg}"
}

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

check_network() {
  log "INFO" "Checking network connectivity..."
  local dns_servers=("8.8.8.8" "1.1.1.1")

  for dns in "${dns_servers[@]}"; do
    if ping -c 1 -W 2 "$dns" > /dev/null 2>&1; then
      log "SUCCESS" "Network connectivity confirmed ($dns)"
      return 0
    fi
  done

  log "ERROR" "Network check failed. Please check your internet connection."
  return 1
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
  # --ftp-create-dirs: Create remote directory if missing
  # -T: Upload file
  # -u: User credentials
  # -s: Silent mode (hide progress bar, handled by exit code)
  # -S: Show error if it fails
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

  # Remove path if user provided full path (e.g., /data/log.txt -> log.txt)
  log_filename=$(basename "$log_filename")

  if upload_file "$log_filename"; then
    exit 0
  else
    exit 1
  fi
}

main "$@"
