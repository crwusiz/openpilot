#!/usr/bin/env bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
UNDERLINE='\033[4m'
BOLD='\033[1m'
NC='\033[0m'

readonly MAIN_SESSION="comma"
readonly TEMP_SESSION="tmp"
readonly OPENPILOT_LAUNCH="/data/openpilot/launch_openpilot.sh"
readonly RESTART_LOG="/data/restart.log"

# Allows you to restart Openpilot without rebooting the Comma 3
tmux kill-session -t "$TEMP_SESSION" 2>/dev/null || true
tmux new-session -d -s "$TEMP_SESSION"

# Use the generated pane ID instead of an ambiguous session/pane shorthand.
launch_pane=$(tmux display-message -p -t "$TEMP_SESSION" '#{pane_id}')
tmux send-keys -l -t "$launch_pane" "$OPENPILOT_LAUNCH"
tmux send-keys -t "$launch_pane" Enter

# This helper runs in the new session. It therefore survives killing the old
# comma session that started restart.sh, then promotes tmp to comma.
helper_command="tmux kill-session -t '$MAIN_SESSION' 2>/dev/null || true; tmux rename-session -t '$TEMP_SESSION' '$MAIN_SESSION' >>'$RESTART_LOG' 2>&1"
tmux split-window -d -v -t "$launch_pane" "$helper_command"

echo -e "\n ${GREEN}${BOLD}  Restart Now ...${NC} \n"
