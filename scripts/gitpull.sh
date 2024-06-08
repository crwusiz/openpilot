#!/usr/bin/bash

play_sound() {
    local sound_file="$1"
    if [ -f "$sound_file" ]; then
        play "$sound_file"
    else
        echo "Error: File $sound_file not found."
    fi
}

pushd /data/openpilot

if [ -f /data/openpilot/prebuilt ]; then
  echo -n "0" > /data/params/d/PrebuiltEnable
  sudo rm -f prebuilt
fi

#FETCH_STATUS=$(git fetch --all --prune | zenity --text-info --title="Git Fetch Status" --width=200 --height=200) && sleep 5

ping -c 3 8.8.8.8 > /dev/null 2>&1

if [ $? -eq 0 ]; then
  git fetch --all --prune
  echo "Fetch completed!"

  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  REMOTE_HASH=$(git rev-parse --short --verify origin/$BRANCH)
  BRANCH_GONE=$(git branch -vv | grep ': gone]' | awk '{print $1}')

  if [ "${BRANCH_GONE}" != "" ]; then
    echo $BRANCH_GONE | xargs git branch -D
  fi

  #git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
  #sed -i 's/fetch = .*/fetch = +refs\/heads\/*:refs\/remotes\/origin\/*/' .git/config

  DESIRED_FETCH="+refs/heads/*:refs/remotes/origin/*"
  CURRENT_FETCH=$(git config --get remote.origin.fetch)

  if [ "$CURRENT_FETCH" != "$DESIRED_FETCH" ]; then
      sed -i "s@$CURRENT_FETCH@${DESIRED_FETCH//\//\\\/}@" .git/config
  fi

  echo ""
  git reset --hard $REMOTE_HASH

  echo ""
  echo "  Git Fetch and Reset HEAD commit ..."
  echo ""
  echo "  current branch is [ $BRANCH ]  "
  echo ""

  exec /data/openpilot/scripts/restart.sh
else
  echo "wifi not connect check your network" > /data/gitpull.log
  if ! command -v play &> /dev/null; then
    echo "Installing sox..."
    sudo apt-get update
    sudo apt-get install -y sox
  fi
  play_sound "/data/openpilot/selfdrive/assets/sounds/warning_soft.wav"
fi