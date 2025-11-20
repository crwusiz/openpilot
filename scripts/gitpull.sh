#!/usr/bin/env bash

set -euo pipefail

# Define color codes for pretty output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
UNDERLINE='\033[4m'
BOLD='\033[1m'
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

configure_git() {
  log_info "Configuring Git settings..."

  local current_fetch=$(git config --get remote.origin.fetch 2>/dev/null || echo "")
  local desired_fetch="+refs/heads/*:refs/remotes/origin/*"

  if [ "$current_fetch" != "$desired_fetch" ]; then
    git config remote.origin.fetch "$desired_fetch"
    log_info "Updated remote fetch configuration."
  fi

  local ssl_verify=$(git config --global http.sslVerify 2>/dev/null || echo "")
  if [ "$ssl_verify" != "false" ]; then
    git config --global http.sslVerify false
    log_info "Set http.sslVerify to false."
  fi

  local submodule_recurse=$(git config --global submodule.recurse 2>/dev/null || echo "")
  if [ "$submodule_recurse" != "true" ]; then
    git config --global submodule.recurse true
    log_info "Set submodule.recurse to true."
  fi

  # Optimize Git for large repositories and network conditions
  git config --global http.postBuffer 1048576000      # Increase buffer for large pushes/fetches
  git config --global http.lowSpeedLimit 1000         # Minimum bytes per second
  git config --global http.lowSpeedTime 300           # Time in seconds for low speed limit
  git config --global core.preloadindex true          # Speed up git status etc.
  git config --global transfer.bundleURI true         # Optimize transfers over HTTP
  git config --global fetch.parallel 4                # Parallelize fetches
  git config --global submodule.fetchJobs 4           # Parallelize submodule fetches
  git config --global diff.ignoreSubmodules untracked # Ignore untracked submodule changes in diff

  log_success "Git configuration completed."
}

# openpilot folder check
setup_directory() {
  if [ -d "/data/openpilot" ]; then
    log_success "Changing directory to /data/openpilot..."
    pushd /data/openpilot > /dev/null
  else
    log_warning "/data/openpilot directory not found. Assuming current directory is the target."
  fi
}

handle_error() {
  local exit_code=$?
  local line_number=$1
  log_error "Script error at line $line_number. Exit code: $exit_code."

  if [[ $exit_code -eq 128 ]]; then
    log_warning "Git error detected. Attempting submodule recovery..."
    recover_submodules
  fi
  exit $exit_code
}

# Set up the trap for ERR signal
trap 'handle_error ${LINENO}' ERR
# Trap for script interruption (Ctrl+C, kill signals)
trap 'log_error "Script interrupted by user or signal."; exit 130' INT TERM

set_time_settings() {
  local timezone="$1"
  log_info "Setting timezone to $timezone..."

  if ! sudo mount -o remount,rw / 2>/dev/null; then
    log_warning "Could not remount / as read-write. Timezone settings might not persist."
  fi

  if ! sudo timedatectl set-timezone "$timezone" 2>/dev/null; then
    log_warning "Could not set timezone to $timezone. Check permissions or if timedatectl is available."
  fi

  if ! sudo timedatectl set-ntp true 2>/dev/null; then
    log_warning "Could not enable NTP. Time synchronization might be off."
  fi

  if ! sudo mount -o remount,ro / 2>/dev/null; then
    log_warning "Could not remount / as read-only. This could be a security risk."
  fi
  log_success "Time settings applied."
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

recover_submodules() {
  log_warning "Attempting to recover corrupted submodules..."

  local submodules=$(git config --file .gitmodules --get-regexp path | awk '{ print $2 }' || true)

  for submodule in $submodules; do
    if [ -d "$submodule" ]; then
      log_info "Cleaning submodule: $submodule"
      if ! (cd "$submodule" && git reset --hard HEAD); then
        log_warning "Removing corrupted submodule directory: $submodule"
        rm -rf "$submodule"
      fi
    else
      log_info "Submodule directory $submodule does not exist, will re-initialize."
    fi
  done

  if ! git submodule update --init --recursive --force; then
    log_error "Failed to fully recover submodules. Manual intervention may be needed."
    return 1
  fi
  log_success "Submodule recovery attempt completed."
  return 0
}

clean_git_repo() {
  log_info "Cleaning git repository (preserving __pycache__)..."
  git clean -fdX
  git clean -fd

  git gc --auto
  if ! git fsck --full; then
    log_warning "Git fsck found issues, attempting repair..."
    git fsck --full --unreachable --dangling --prune=now || true
    log_info "Git fsck repair attempted."
  fi
  log_success "Git repository cleaned."
}

safe_fetch_and_reset() {
  local branch="$1"
  local max_retries=3
  local retry=1

  while [ $retry -le $max_retries ]; do
    log_info "Fetching changes for branch '$branch' (attempt $retry/$max_retries)..."

    log_info "Performing full fetch with progress..."
    if timeout 600 git fetch --all --prune --progress &> /dev/null; then
      log_success "Full fetch completed successfully."
      break
    else
      local fetch_exit_code=$?
      if [ $fetch_exit_code -eq 124 ]; then
        log_warning "Fetch timed out after 600 seconds."
      else
        log_warning "Fetch failed with exit code: $fetch_exit_code."
      fi

      if [ $retry -eq $max_retries ]; then
        log_error "Fetch failed after $max_retries attempts. Cleaning repo and exiting."
        clean_git_repo
        return 1
      fi
      log_warning "Fetch failed, retrying in 15 seconds..."
      sleep 15
      ((retry++))
    fi
  done

  if ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    log_error "Remote branch 'origin/$branch' does not exist. Cannot reset."
    return 1
  fi

  if ! git diff --quiet || ! git diff --cached --quiet; then
    log_warning "Discarding uncommitted local changes and untracked files..."
    local reset_head_output=$(git reset --hard HEAD 2>&1)
    echo -e "    ${BOLD}${reset_head_output}${NC}"
    git clean -df
  fi

  log_info "Resetting current branch to 'origin/$branch'..."
  local reset_output=""
  reset_output=$(git reset --hard "origin/$branch" 2>&1)
  if [ $? -eq 0 ]; then
    log_success "Repository successfully reset to 'origin/$branch'."
    echo -e "    ${BOLD}${reset_output}${NC}"
  else
    log_error "Hard reset to 'origin/$branch' failed. Check if remote branch is accessible or repo is corrupted."
    echo -e "    ${RED}Error detail:${NC} ${reset_output}"
    return 1
  fi

  return 0
}

cleanup_gone_branches() {
  log_info "Cleaning up local branches that are gone from remote..."
  local gone_branches=$(git branch -vv | grep ': gone]' | awk '{print $1}' | tr -d '*' || true)

  if [ -n "$gone_branches" ]; then
    echo "$gone_branches" | while read -r branch; do
      if [ -n "$branch" ] && [ "$branch" != "$(git rev-parse --abbrev-ref HEAD)" ]; then
        if ! git branch -D "$branch" 2>/dev/null; then
          log_warning "Could not delete gone branch: $branch. It might have unmerged changes."
        else
          log_info "Deleted gone branch: $branch"
        fi
      fi
    done
    log_success "Gone branches cleanup completed."
  else
    log_info "No gone branches to clean up."
  fi
}

compare_commits() {
  local branch="$1"
  log_info "Comparing local and remote commits for branch '$branch'..."

  local remote_commit=$(git ls-remote origin "$branch" | awk '{print substr($1,1,7)}' 2>/dev/null || echo "")
  local local_commit=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "")

  if [ -z "$remote_commit" ] || [ -z "$local_commit" ]; then
    log_error "Could not retrieve commit information for comparison."
    return 1
  fi

  local remote_time=$(date -d @"$(git show -s --format=%ct "origin/$branch" 2>/dev/null || echo "0")" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "Unknown")
  local local_time=$(date -d @"$(git show -s --format=%ct HEAD 2>/dev/null || echo "0")" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "Unknown")

  echo -e "\n Local Commit: ($local_time) [ ${GREEN}${BOLD}$local_commit${NC} ]"
  echo -e "Remote Commit: ($remote_time) [ ${GREEN}${BOLD}$remote_commit${NC} ]"
}

process_submodule() {
  local repo_name="$1"
  local repo_path="$2"

  log_info "Processing submodule: $repo_name (Path: $repo_path)"

  if [[ ! -d "$repo_path" ]]; then
    log_error "$repo_name: Submodule path does not exist ($repo_path). Skipping."
    return 1
  fi

  local expected_commit=$(git ls-files --stage "$repo_path" | awk '{print $2}' || true)

  if [ -z "$expected_commit" ]; then
    log_warning "$repo_name: Could not determine expected commit from superproject. Attempting generic update as a fallback."
    (cd "$repo_path" && git submodule update --init --recursive --force) || \
      { log_error "$repo_name: Generic submodule update failed."; return 1; }
    log_success "$repo_name: Generic submodule update completed (no specific commit found in superproject)."
    return 0
  fi

  log_info "Fetching and resetting '$repo_name' to ${expected_commit:0:7}..."

  if ! pushd "$repo_path" > /dev/null; then
    log_error "Could not change to submodule directory: $repo_path."
    return 1
  fi

  local stash_applied=0
  if ! git diff-index --quiet HEAD --; then
    log_warning "Stashing local changes in '$repo_path' before hard reset."
    if git stash push -m "Auto-stash before submodule reset $(date)"; then
      stash_applied=1
    else
      log_warning "Failed to stash changes in '$repo_path'. Continuing anyway."
    fi
  fi

  local fetch_success=0
  if timeout 300 git fetch origin --prune &> /dev/null; then
    fetch_success=1
  else
    local fetch_exit_code=$?
    log_error "'$repo_name': Submodule fetch failed or timed out (exit code $fetch_exit_code). Cannot ensure correct commit locally."
    if [ "$stash_applied" -eq 1 ]; then git stash pop > /dev/null 2>&1 || true; fi # Clean up stash if fetch failed
    popd > /dev/null
    return 1
  fi

  local reset_output=""
  if [ "$fetch_success" -eq 1 ]; then
    reset_output=$(git reset --hard "$expected_commit" 2>&1)
    if [ $? -eq 0 ]; then
      log_success "'$repo_name': Successfully updated and reset."
      echo -e "    ${BOLD}${reset_output}${NC}"
    else
      log_error "'$repo_name': Hard reset to ${expected_commit:0:7} failed. Check if commit exists or repo is corrupted."
      echo -e "    ${RED}Error detail:${NC} ${reset_output}"
      if [ "$stash_applied" -eq 1 ]; then git stash pop > /dev/null 2>&1 || true; fi
      popd > /dev/null
      return 1
    fi
  else
    log_error "'$repo_name': Skipping reset due to failed fetch."
    if [ "$stash_applied" -eq 1 ]; then git stash pop > /dev/null 2>&1 || true; fi
    popd > /dev/null
    return 1
  fi

  if [ "$stash_applied" -eq 1 ]; then
    log_info "'$repo_name': Attempting to restore stashed changes."
    if ! git stash pop; then
      log_warning "'$repo_name': Failed to restore stash. Manual intervention required. Check 'git stash list'."
    fi
  fi

  popd > /dev/null

  log_info "$repo_name: Cleaning up submodule repository..."
  git -C "$repo_path" gc --auto || log_warning "$repo_name: Git gc failed for '$repo_name'."
  git -C "$repo_path" clean -fd

  log_success "$repo_name: Processing completed."
  return 0
}

main() {
  log_info "Starting Git pull script..."

  setup_directory

  if [ -f "/data/openpilot/prebuilt" ]; then
    echo -n "0" > /data/params/d/PrebuiltEnable 2>/dev/null || true
    sudo rm -f prebuilt
    log_success "Removed prebuilt flag."
  fi

  if ! check_network; then
    touch /data/check_network.log
    log_error "Network check failed, exiting"
    exit 1
  fi

  local lang=$(cat /data/params/d/LanguageSetting 2>/dev/null || echo "")
  case "$lang" in
    "ko")
      set_time_settings "Asia/Seoul"
      ;;
    "en")
      set_time_settings "America/New_York"
      ;;
    *)
      log_warning "Unknown language setting: $lang"
      ;;
  esac

  local branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
  log_info "Current branch: $branch"

  configure_git

  if ! safe_fetch_and_reset "$branch"; then
    log_error "Failed to fetch and reset main repository."
    echo 1 > /data/gitpull_exit_code.log
    exit 1
  fi

  log_info "Starting comprehensive submodule processing..."
  local submodules_list=$(git config --file .gitmodules --get-regexp path | awk '{ print $2 }' || true)
  local submodule_failure_count=0

  for submodule_path in $submodules_list; do
    local submodule_name=$(basename "$submodule_path")
    if ! process_submodule "$submodule_name" "$submodule_path"; then
      log_error "Failed to fully process submodule: $submodule_name. Continuing with others."
      ((submodule_failure_count++))
    fi
  done

  if [ "$submodule_failure_count" -gt 0 ]; then
    log_warning "Completed Git pull with $submodule_failure_count submodule(s) that had issues."
  else
    log_success "All submodules processed successfully."
  fi

  log_success "Git pull process completed."

  cleanup_gone_branches

  if compare_commits "$branch"; then
    echo -e "\nCommit Compare [${GREEN}${BOLD} match ${NC}]\n"
    if [ -x "/data/openpilot/scripts/restart.sh" ]; then
      log_success "Executing restart script...\n"
      echo 0 > /data/gitpull_exit_code.log
      exec /data/openpilot/scripts/restart.sh
    else
      log_error "Restart script '/data/openpilot/scripts/restart.sh' not found. Please check path."
      echo 1 > /data/gitpull_exit_code.log
      exit 1
    fi
  else
    echo -e "\nCommit Compare [${RED}${BOLD} not match ${NC}]\n"
    log_error "Git pull process failed: Local and remote commits do not match after reset. Exiting."
    exit 1
  fi
}

set +e
main "$@"
exit_code=$?

if [ $exit_code -ne 0 ]; then
  log_error "Script completed with errors (final exit code: $exit_code)."
fi

exit $exit_code
