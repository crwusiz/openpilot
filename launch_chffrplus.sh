#!/usr/bin/bash

if [ -z "$BASEDIR" ]; then
  BASEDIR="/data/openpilot"
fi

source "$BASEDIR/launch_env.sh"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

function agnos_init {
  # wait longer for weston to come up
  if [ -f "$BASEDIR/prebuilt" ]; then
    sleep 5
  fi

  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta

  # set success flag for current boot slot
  sudo abctl --set_success

  # Check if AGNOS update is required
  if [ $(< /VERSION) != "$AGNOS_VERSION" ]; then
    AGNOS_PY="$DIR/system/hardware/tici/agnos.py"
    MANIFEST="$DIR/system/hardware/tici/agnos.json"
    if $AGNOS_PY --verify $MANIFEST; then
      sudo reboot
    fi
    $DIR/system/hardware/tici/updater $AGNOS_PY $MANIFEST
  fi
}

function launch {
  # Remove orphaned git lock if it exists on boot
  [ -f "$DIR/.git/index.lock" ] && rm -f $DIR/.git/index.lock

  # Pull time from panda
  $DIR/selfdrive/boardd/set_time.py

  # Check to see if there's a valid overlay-based update available. Conditions
  # are as follows:
  #
  # 1. The BASEDIR init file has to exist, with a newer modtime than anything in
  #    the BASEDIR Git repo. This checks for local development work or the user
  #    switching branches/forks, which should not be overwritten.
  # 2. The FINALIZED consistent file has to exist, indicating there's an update
  #    that completed successfully and synced to disk.

  if [ -f "${BASEDIR}/.overlay_init" ]; then
    #find ${BASEDIR}/.git -newer ${BASEDIR}/.overlay_init | grep -q '.' 2> /dev/null
    #if [ $? -eq 0 ]; then
    #  echo "${BASEDIR} has been modified, skipping overlay update installation"
    #else
      if [ -f "${STAGING_ROOT}/finalized/.overlay_consistent" ]; then
        if [ ! -d /data/safe_staging/old_openpilot ]; then
          echo "Valid overlay update found, installing"
          LAUNCHER_LOCATION="${BASH_SOURCE[0]}"

          mv $BASEDIR /data/safe_staging/old_openpilot
          mv "${STAGING_ROOT}/finalized" $BASEDIR
          cd $BASEDIR

          echo "Restarting launch script ${LAUNCHER_LOCATION}"
          unset AGNOS_VERSION
          exec "${LAUNCHER_LOCATION}"
        else
          echo "openpilot backup found, not updating"
          # TODO: restore backup? This means the updater didn't start after swapping
        fi
      fi
  #  fi
  fi

  # handle pythonpath
  ln -sfn $(pwd) /data/pythonpath
  export PYTHONPATH="$PWD"

  # hardware specific init
  agnos_init

  # write tmux scrollback to a file
  tmux capture-pane -pq -S-1000 > /tmp/launch_log

  # CarList
  # sed '$a-------------------' ( add last line )

  PARAMS_ROOT="/data/params"

  if [ ! -d "${PARAMS_ROOT}/crwusiz" ] ; then
    mkdir ${PARAMS_ROOT}/crwusiz
  fi

  cat $BASEDIR/selfdrive/car/hyundai/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > ${PARAMS_ROOT}/crwusiz/CarList_HYUNDAI
  awk '/HYUNDAI/' ${PARAMS_ROOT}/crwusiz/CarList_HYUNDAI > ${PARAMS_ROOT}/crwusiz/CarList_Hyundai
  awk '/KIA/' ${PARAMS_ROOT}/crwusiz/CarList_HYUNDAI > ${PARAMS_ROOT}/crwusiz/CarList_Kia
  awk '/GENESIS/' ${PARAMS_ROOT}/crwusiz/CarList_HYUNDAI > ${PARAMS_ROOT}/crwusiz/CarList_Genesis

  MANUFACTURER=$(cat ${PARAMS_ROOT}/d/SelectedManufacturer)
  if [ "${MANUFACTURER}" = "HYUNDAI" ]; then
    cp -f ${PARAMS_ROOT}/crwusiz/CarList_Hyundai ${PARAMS_ROOT}/crwusiz/CarList
  elif [ "${MANUFACTURER}" = "KIA" ]; then
    cp -f ${PARAMS_ROOT}/crwusiz/CarList_Kia ${PARAMS_ROOT}/crwusiz/CarList
  elif [ "${MANUFACTURER}" = "GENESIS" ]; then
    cp -f ${PARAMS_ROOT}/crwusiz/CarList_Genesis ${PARAMS_ROOT}/crwusiz/CarList
  else
    cp -f ${PARAMS_ROOT}/crwusiz/CarList_HYUNDAI ${PARAMS_ROOT}/crwusiz/CarList
  fi
  #cat $BASEDIR/selfdrive/car/gm/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > ${PARAMS_ROOT}/crwusiz/CarList_Gm
  #cat $BASEDIR/selfdrive/car/toyota/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > ${PARAMS_ROOT}/crwusiz/CarList_TOYOTA
  #awk '/TOYOTA/' ${PARAMS_ROOT}/crwusiz/CarList_TOYOTA > ${PARAMS_ROOT}/crwusiz/CarList_Toyota
  #awk '/LEXUS/' ${PARAMS_ROOT}/crwusiz/CarList_TOYOTA > ${PARAMS_ROOT}/crwusiz/CarList_Lexus
  #cat $BASEDIR/selfdrive/car/honda/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > ${PARAMS_ROOT}/crwusiz/CarList_Honda

  # git last commit log
  git log -1 --pretty=format:"%h, %cs, %cr" > ${PARAMS_ROOT}/d/GitLog

  if [ ! -f "${PARAMS_ROOT}/d/SelectedBranch" ]; then
    git branch --show-current > ${PARAMS_ROOT}/d/SelectedBranch
    #git status | grep "origin" | awk -F'/' '{print $2}' | sed -e 's/..$//' > ${PARAMS_ROOT}/d/SelectedBranch
  fi

  # git remote branch list
  git branch -r | sed '1d' | sed -e 's/[/]//g' | sed -e 's/origin//g' | sort -r > ${PARAMS_ROOT}/crwusiz/GitBranchList

  # git remote
  #sed 's/.\{4\}$//' ${PARAMS_ROOT}/d/GitRemote > ${PARAMS_ROOT}/crwusiz/GitRemote_

  # events language init
  LANG=$(cat ${PARAMS_ROOT}/d/LanguageSetting)

  if [ "${LANG}" = "main_ko" ]; then
    cp -f $BASEDIR/scripts/add/events_ko.py $BASEDIR/selfdrive/controls/lib/events.py
    cp -f $BASEDIR/scripts/add/ui_ko.h $BASEDIR/selfdrive/ui/ui.h
  else
    cp -f $BASEDIR/scripts/add/events.py $BASEDIR/selfdrive/controls/lib/events.py
    cp -f $BASEDIR/scripts/add/ui.h $BASEDIR/selfdrive/ui/ui.h
  fi

  # start manager
  cd selfdrive/manager
  ./build.py && ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
