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
