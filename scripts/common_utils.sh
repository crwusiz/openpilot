#!/usr/bin/env bash

# ==============================================================================
# Common Colors
# ==============================================================================
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly BOLD='\033[1m'
readonly NC='\033[0m'

# ==============================================================================
# Common Functions
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

check_network() {
  log "INFO" "Checking network connectivity for git operations..."
  local target_url="https://github.com"

  if curl -I -s -m 3 "$target_url" > /dev/null 2>&1; then
    log "SUCCESS" "Network connectivity confirmed ($target_url)"
    return 0
  else
    log "ERROR" "Network check failed. Cannot reach $target_url. Please check your connection."
    return 1
  fi
}
