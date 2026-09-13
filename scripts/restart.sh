#!/usr/bin/env bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
UNDERLINE='\033[4m'
BOLD='\033[1m'
NC='\033[0m'

readonly MAIN_SESSION="comma"
readonly TEMP_SESSION="tmp"
readonly OPENPILOT_DIR="/data/openpilot"
readonly OPENPILOT_LAUNCH="/data/openpilot/launch_openpilot.sh"
readonly RESTART_LOG="/data/restart.log"

# Allows you to restart Openpilot without rebooting the Comma 3
tmux kill-session -t "$TEMP_SESSION" 2>/dev/null || true

echo -e "\n ${GREEN}${BOLD}  Restart Now ...${NC} \n"

# Run the entire handoff in the replacement session's first pane. It survives
# killing the caller's comma session and does not depend on space for a split.
# Stop the old instance before launching the replacement from its required cwd.
restart_command="tmux kill-session -t '$MAIN_SESSION' 2>/dev/null || true; tmux rename-session -t '$TEMP_SESSION' '$MAIN_SESSION' >>'$RESTART_LOG' 2>&1 && exec bash '$OPENPILOT_LAUNCH'"
tmux new-session -d -s "$TEMP_SESSION" -x 120 -y 40 -c "$OPENPILOT_DIR" "$restart_command"
