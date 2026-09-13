#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/common_utils.sh"

# FTP Configuration
readonly FTP_USER="openpilot"
readonly FTP_PASS="ruF3~Dt8"
readonly FTP_HOST="ftp.jmtechn.com"
readonly FTP_PORT="8022"
readonly FTP_DEFAULT_DIR="tmux_log"

# Paths
readonly PARAMS_DIR="/data/params/d"

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

# Core FTP upload function
# Usage: ftp_upload_file <LOCAL_FILE_PATH> <REMOTE_PATH_WITH_LEADING_SLASH>
ftp_upload_file() {
  local local_path="$1"
  local remote_path="$2"

  if [ ! -f "$local_path" ]; then
    log "ERROR" "File not found: $local_path"
    return 1
  fi

  local ftp_url="ftp://${FTP_HOST}:${FTP_PORT}${remote_path}"

  if curl -sS \
          --ftp-create-dirs \
          --connect-timeout 30 \
          --retry 3 \
          -T "$local_path" \
          -u "${FTP_USER}:${FTP_PASS}" \
          "$ftp_url"; then
    return 0
  else
    return 1
  fi
}
