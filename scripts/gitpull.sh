#!/usr/bin/bash

pushd /data/openpilot

echo -n "0" > /data/params/d/PrebuiltEnable
sudo rm -f prebuilt

#FETCH_STATUS=$(git fetch --all --prune | zenity --text-info --title="Git Fetch Status" --width=200 --height=200) && sleep 5

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