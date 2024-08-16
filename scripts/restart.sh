#!/usr/bin/env bash

RED='\033[0;31m'
GREEN='\033[0;32m'
UNDERLINE='\033[4m'
BOLD='\033[1m'
NC='\033[0m'

# Allows you to restart Openpilot without rebooting the Comma 3
tmux new -d -s tmp;
tmux split-window -v -t tmp;
tmux send-keys -t tmp.0 "/data/openpilot/launch_openpilot.sh" ENTER
tmux send-keys -t tmp.1 "tmux kill-session -t comma" ENTER
tmux send-keys -t tmp.1 "tmux rename-session -t tmp comma" ENTER
tmux send-keys -t tmp.1 "exit" ENTER

echo ""
echo -e "${GREEN}${BOLD}  Restart Now ...${NC}"
echo ""
