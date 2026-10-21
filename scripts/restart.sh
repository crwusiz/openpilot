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
readonly LOG_RESET_HELPER="${OPENPILOT_DIR}/openpilot/common/log_paths.py"

clear_logs=0
handoff=0
for argument in "$@"; do
  case "$argument" in
    --clear-logs) clear_logs=1 ;;
    --handoff) handoff=1 ;;
    *) printf 'ERROR: Unknown restart argument: %s\n' "$argument" >&2; exit 2 ;;
  esac
done

if [[ $handoff == 1 ]]; then
  # This pane survives the old comma session. Keep a failed reset visible instead
  # of silently starting a new session with only some of its old logs removed.
  if [[ $clear_logs == 1 ]]; then
    tmux set-option -p remain-on-exit on
  fi
  tmux kill-session -t "$MAIN_SESSION" 2>/dev/null || true
  if [[ $clear_logs == 1 ]]; then
    printf 'Waiting for active log uploads, then clearing local logs...\n'
    if ! python3 "$LOG_RESET_HELPER" --clear-all --timeout 150; then
      printf 'ERROR: Log reset did not complete. Replacement was not started; check the error above and retry: bash /data/openpilot/scripts/restart.sh --clear-logs\n' >&2
      exit 1
    fi
  fi
  tmux rename-session -t "$TEMP_SESSION" "$MAIN_SESSION"
  exec bash "$OPENPILOT_LAUNCH"
fi

if [[ $clear_logs == 1 && ! -r $LOG_RESET_HELPER ]]; then
  printf 'ERROR: Log reset helper is missing or unreadable: %s\n' "$LOG_RESET_HELPER" >&2
  exit 1
fi

# Allows you to restart Openpilot without rebooting the Comma 3
tmux kill-session -t "$TEMP_SESSION" 2>/dev/null || true

echo -e "\n ${GREEN}${BOLD}  Restart Now ...${NC} \n"

# Run the entire handoff in the replacement session's first pane. It survives
# killing the caller's comma session and does not depend on space for a split.
# Stop the old instance before launching the replacement from its required cwd.
printf -v restart_command 'exec bash %q --handoff' "${OPENPILOT_DIR}/scripts/restart.sh"
if [[ $clear_logs == 1 ]]; then restart_command+=' --clear-logs'; fi
tmux new-session -d -s "$TEMP_SESSION" -x 120 -y 40 -c "$OPENPILOT_DIR" "$restart_command"
