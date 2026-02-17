#!/usr/bin/env bash

set -euo pipefail

# ==============================================================================
# Configuration and Constants
# ==============================================================================

readonly OPENPILOT_DIR="/data/openpilot"
readonly PARAMS_DIR="/data/params/d"
readonly LOG_FILE="/data/gitpull_exit_code.log"
readonly RESTART_SCRIPT="${OPENPILOT_DIR}/scripts/restart.sh"

# Color Codes
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly BOLD='\033[1m'
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

# Print indented detail info
log_detail() {
  echo -e "    ${BOLD}$1${NC}"
}

handle_error() {
  local exit_code=$?
  local line_number=$1
  log "ERROR" "Script error occurred (Line: $line_number, Exit: $exit_code)"
  exit $exit_code
}

trap 'handle_error ${LINENO}' ERR
trap 'log "ERROR" "User interrupted."; exit 130' INT TERM

# ==============================================================================
# Core Functions
# ==============================================================================

setup_environment() {
  if [ -d "$OPENPILOT_DIR" ]; then
    cd "$OPENPILOT_DIR" || exit 1
  else
    log "WARNING" "$OPENPILOT_DIR not found. Running in current directory."
  fi

  # Remove Prebuilt flag
  if [ -f "${OPENPILOT_DIR}/prebuilt" ]; then
    echo -n "0" > "${PARAMS_DIR}/PrebuiltEnable" 2>/dev/null || true
    rm -f prebuilt
  fi
}

check_network() {
  log "INFO" "Checking network connectivity..."
  local dns_servers=("8.8.8.8" "1.1.1.1")

  for dns in "${dns_servers[@]}"; do
    if ping -c 1 -W 2 "$dns" > /dev/null 2>&1; then
      log "SUCCESS" "Network confirmed ($dns)"
      return 0
    fi
  done

  log "ERROR" "Network failed."
  return 1
}

set_timezone() {
  local lang_setting
  lang_setting=$(cat "${PARAMS_DIR}/LanguageSetting" 2>/dev/null || echo "en")
  local target_zone="America/New_York"

  if [[ "$lang_setting" == "ko" ]]; then
    target_zone="Asia/Seoul"
  fi

  log "INFO" "Setting timezone: $target_zone (Language: $lang_setting)"

  # Apply settings quietly
  sudo mount -o remount,rw / 2>/dev/null || true
  sudo timedatectl set-timezone "$target_zone" 2>/dev/null || true
  sudo timedatectl set-ntp true 2>/dev/null || true
  sudo mount -o remount,ro / 2>/dev/null || true

  log "SUCCESS" "Timezone set."
}

configure_git() {
  log "INFO" "Optimizing Git config..."
  git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
  git config --global http.sslVerify false
  git config --global submodule.recurse true
  git config --global http.postBuffer 524288000
  git config --global core.preloadindex true
  git config --global fetch.parallel 4
  git config --global submodule.fetchJobs 4
  git config --global diff.ignoreSubmodules untracked
}

update_repository() {
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
  log "INFO" "Current branch: $branch - Starting update"

  # 1. Fetch (Quietly, log on failure)
  log "INFO" "Fetching changes..."
  if ! git fetch origin --prune --quiet; then
    log "WARNING" "Fetch failed. Retrying..."
    sleep 3
    if ! git fetch origin --prune --quiet; then
        log "ERROR" "Fetch failed. Check network."
        return 1
    fi
  fi
  log "SUCCESS" "Fetch complete."

  # 2. Reset (Hide output, format manually)
  log "INFO" "Resetting main repo (origin/$branch)..."
  if git reset --hard "origin/$branch" > /dev/null 2>&1; then
    local commit_hash
    commit_hash=$(git rev-parse --short HEAD)
    local commit_msg
    commit_msg=$(git log -1 --format=%s)

    log "SUCCESS" "Reset complete."
    log_detail "HEAD is now at $commit_hash $commit_msg"
  else
    log "ERROR" "Reset failed."
    return 1
  fi

  git clean -fd > /dev/null 2>&1
}

update_submodules() {
  log "INFO" "Processing submodules..."

  # Extract paths from .gitmodules
  local paths
  paths=$(git config --file .gitmodules --get-regexp path | awk '{ print $2 }' || true)

  for path in $paths; do
    local name
    name=$(basename "$path")
    log "INFO" "Processing submodule: $name"

    # Update submodule (Hide git output, show result log)
    if git submodule update --init --force "$path" > /dev/null 2>&1; then
      local sub_hash
      sub_hash=$(git -C "$path" rev-parse --short HEAD)
      local sub_msg
      sub_msg=$(git -C "$path" log -1 --format=%s)

      log "SUCCESS" "'$name': Update complete."
      log_detail "HEAD is now at $sub_hash $sub_msg"

      # Clean submodule
      git -C "$path" clean -fd > /dev/null 2>&1
    else
      log "WARNING" "'$name': Update failed. Retrying with force init..."
      git submodule deinit -f "$path" > /dev/null 2>&1 || true
      if git submodule update --init --force "$path" > /dev/null 2>&1; then
         local sub_hash_retry
         sub_hash_retry=$(git -C "$path" rev-parse --short HEAD)
         local sub_msg_retry
         sub_msg_retry=$(git -C "$path" log -1 --format=%s)

         log "SUCCESS" "'$name': Recovery and update complete."
         log_detail "HEAD is now at $sub_hash_retry $sub_msg_retry"
      else
         log "ERROR" "'$name': Recovery failed."
      fi
    fi
  done

  log "SUCCESS" "All submodules processed."
}

cleanup_gone_branches() {
  # Quietly process
  git fetch -p --quiet
  local gone_branches
  gone_branches=$(git branch -vv | grep ': gone]' | awk '{print $1}' || true)

  if [ -n "$gone_branches" ]; then
    log "INFO" "Cleaning gone branches: $gone_branches"
    echo "$gone_branches" | xargs -r git branch -D > /dev/null 2>&1
  fi
}

compare_and_restart() {
  local local_hash remote_hash local_time remote_time

  local_hash=$(git rev-parse --short=7 HEAD)
  remote_hash=$(git rev-parse --short=7 "@{u}")

  local_time=$(date -d @"$(git show -s --format=%ct HEAD)" '+%Y-%m-%d %H:%M:%S')
  remote_time=$(date -d @"$(git show -s --format=%ct "@{u}")" '+%Y-%m-%d %H:%M:%S')

  echo ""
  echo -e " Local Commit: ($local_time) [ ${GREEN}${BOLD}$local_hash${NC} ]"
  echo -e "Remote Commit: ($remote_time) [ ${GREEN}${BOLD}$remote_hash${NC} ]"
  echo ""

  if [ "$local_hash" == "$remote_hash" ]; then
    echo -e "Commit Compare [ ${GREEN}${BOLD}match${NC} ]"
    echo ""

    if [ -x "$RESTART_SCRIPT" ]; then
      log "SUCCESS" "Restarting system..."
      echo 0 > "$LOG_FILE"
      exec "$RESTART_SCRIPT"
    else
      log "ERROR" "Restart script not found: $RESTART_SCRIPT"
      echo 1 > "$LOG_FILE"
      exit 1
    fi
  else
    echo -e "Commit Compare [ ${RED}${BOLD}mismatch${NC} ]"
    log "ERROR" "Hash mismatch. Update failed."
    echo 1 > "$LOG_FILE"
    exit 1
  fi
}

# ==============================================================================
# Main Execution
# ==============================================================================

main() {
  log "INFO" "Starting Git Pull process"

  setup_environment

  if ! check_network; then
    touch "/data/check_network.log"
    exit 1
  fi

  set_timezone
  configure_git

  if ! update_repository; then
    echo 1 > "$LOG_FILE"
    exit 1
  fi

  update_submodules
  cleanup_gone_branches
  compare_and_restart
}

main "$@"
