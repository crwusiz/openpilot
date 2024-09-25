#!/usr/bin/env bash

RED='\033[0;31m'
GREEN='\033[0;32m'
UNDERLINE='\033[4m'
BOLD='\033[1m'
NC='\033[0m'

pushd /data/openpilot

if [ -f /data/openpilot/prebuilt ]; then
  echo -n "0" > /data/params/d/PrebuiltEnable
  sudo rm -f prebuilt
fi

if ping -c 3 8.8.8.8 > /dev/null 2>&1; then
  SSL_VERIFY=$(git config --global http.sslVerify)
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  BRANCH_GONE=$(git branch -vv | grep ': gone]' | awk '{print $1}')
  CURRENT_FETCH=$(git config --get remote.origin.fetch)
  DESIRED_FETCH="+refs/heads/*:refs/remotes/origin/*"

  if [ "$CURRENT_FETCH" != "$DESIRED_FETCH" ]; then
    sed -i "s@$CURRENT_FETCH@${DESIRED_FETCH//\//\\\/}@" .git/config
  fi

  if [ "$SSL_VERIFY" != "false" ]; then
    git config --global http.sslVerify false
    echo "http.ssl verify false"
  fi

  git fetch --all --prune
  echo "Fetch completed!"

  if [ "${BRANCH_GONE}" != "" ]; then
    echo $BRANCH_GONE | xargs git branch -D
  fi

  echo -e "\n${RED}Resetting local branch to match remote...${NC}\n"
  git reset --hard origin/$BRANCH
  echo -e "\n  Git Fetch and Reset HEAD commits ...\n"
  echo -e "  current branch is [ ${GREEN}${BOLD} $BRANCH ${NC} ]  \n"
  exec /data/openpilot/scripts/restart.sh

else
  echo "wifi not connect check your network" > /data/check_network.log
fi
