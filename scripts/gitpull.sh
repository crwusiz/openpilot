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
readonly OPENPILOT_DIR="/data/openpilot"
readonly PARAMS_DIR="/data/params/d"
readonly LOG_FILE="/data/gitpull_exit_code.log"
readonly RESTART_SCRIPT="${OPENPILOT_DIR}/scripts/restart.sh"

# ==============================================================================
# Utility Functions
# ==============================================================================
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

# progress spinner
run_with_spinner() {
  local message="$1"
  shift
  local spin='-\|/'
  local i=0

  "$@" > /dev/null 2>&1 &
  local pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    i=$(( (i + 1) % 4 ))
    printf "\r    %s... %s" "$message" "${spin:$i:1}"
    sleep 0.1
  done

  printf "\r\033[K"
  wait "$pid"
}

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
  git config --local remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
  git config --local http.sslVerify false
  git config --local submodule.recurse true
  git config --local http.postBuffer 16777216
  git config --local http.maxRequestBuffer 16777216
  git config --local pack.windowMemory 16m
  git config --local pack.packSizeLimit 32m
  git config --local core.deltaBaseCacheLimit 16m
  git config --local pack.threads 1
  git config --local core.preloadindex true
  git config --local fetch.parallel 1
  git config --local submodule.fetchJobs 1
  git config --local gc.auto 0
  git config --local diff.ignoreSubmodules untracked
}

update_repository() {
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
  log "INFO" "Current branch: $branch - Starting update"

  # 1. Fetch
  log "INFO" "Fetching changes..."
  local fetch_refspec="+refs/heads/${branch}:refs/remotes/origin/${branch}"
  if ! run_with_spinner "Fetching Git objects" git -c pack.threads=1 -c pack.windowMemory=16m -c pack.packSizeLimit=32m \
      fetch origin "$fetch_refspec" --prune --no-tags --quiet; then
    log "WARNING" "Fetch failed. Retrying..."
    sleep 3
    if ! run_with_spinner "Retrying fetch" git -c pack.threads=1 -c pack.windowMemory=8m -c pack.packSizeLimit=16m \
        fetch origin "$fetch_refspec" --prune --no-tags --quiet; then
        log "ERROR" "Fetch failed. Check network."
        return 1
    fi
  fi
  log "SUCCESS" "Fetch complete."

  log "INFO" "Cleaning untracked files..."
  git clean -fd > /dev/null 2>&1

  log "INFO" "Resetting main repo (origin/$branch)..."
  local reset_err
  if ! reset_err=$(GIT_LFS_SKIP_SMUDGE=1 git reset --hard "origin/$branch" 2>&1); then
    log "ERROR" "Reset failed. Reason: $reset_err"
    return 1
  fi

  # 2. Main LFS Pull
  log "INFO" "Pulling LFS files..."
  if ! run_with_spinner "Downloading LFS files" env GIT_LFS_FORCE_PROGRESS=0 git lfs pull; then
    log "ERROR" "LFS Pull failed. Please check network connection."
    return 1
  fi

  local commit_hash
  commit_hash=$(git rev-parse --short HEAD)
  local commit_msg
  commit_msg=$(git log -1 --format=%s)

  log "SUCCESS" "Reset and LFS Pull complete."
  log_detail "HEAD is now at $commit_hash $commit_msg"
}

update_submodules() {
  log "INFO" "Processing submodules..."

  git submodule sync > /dev/null 2>&1

  # Extract paths from .gitmodules
  local paths
  paths=$(git config --file .gitmodules --get-regexp path | awk '{ print $2 }' || true)

  for path in $paths; do
    local name
    name=$(basename "$path")
    log "INFO" "Processing submodule: $name"

    if run_with_spinner "Updating $name" git submodule update --init --force --jobs 4 "$path"; then

      run_with_spinner "Downloading LFS for $name" env GIT_LFS_FORCE_PROGRESS=0 git -C "$path" lfs pull || true

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
      if run_with_spinner "Force updating $name" git submodule update --init --force --jobs 4 "$path"; then
         run_with_spinner "Downloading LFS for $name" env GIT_LFS_FORCE_PROGRESS=0 git -C "$path" lfs pull || true

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

  log "INFO" "Verifying commit synchronization..."
  log_detail "Local  Commit: ${GREEN}${BOLD}${local_hash}${NC} ($local_time)"
  log_detail "Remote Commit: ${GREEN}${BOLD}${remote_hash}${NC} ($remote_time)"

  if [ "$local_hash" == "$remote_hash" ]; then
    log "SUCCESS" "Commit synchronized: Match ($local_hash)"

    if [ -x "$RESTART_SCRIPT" ]; then
      log "SUCCESS" "Restarting system in background..."
      echo 0 > "$LOG_FILE"
      nohup bash "$RESTART_SCRIPT" >/dev/null 2>&1 &
      exit 0
    else
      log "ERROR" "Restart script not found: $RESTART_SCRIPT"
      echo 1 > "$LOG_FILE"
      exit 1
    fi
  else
    log "ERROR" "Commit mismatch detected (Local: $local_hash vs Remote: $remote_hash)"
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
