#!/usr/bin/bash

if [ -z "$BASEDIR" ]; then
  BASEDIR="/data/openpilot"
fi

source "$BASEDIR/launch_env.sh"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

function agnos_init {
  # wait longer for weston to come up
  if [ -f "$BASEDIR/prebuilt" ]; then
    sleep 3
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
  if [ ! -f "/data/params/crwusiz" ] ; then
    mkdir /data/params/crwusiz
  fi

  cat /data/openpilot/selfdrive/car/hyundai/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > /data/params/crwusiz/CarList_HYUNDAI
  cp -f /data/params/crwusiz/CarList_HYUNDAI /data/params/crwusiz/CarList
  awk '/HYUNDAI/' /data/params/crwusiz/CarList_HYUNDAI > /data/params/crwusiz/CarList_Hyundai
  awk '/KIA/' /data/params/crwusiz/CarList_HYUNDAI > /data/params/crwusiz/CarList_Kia
  awk '/GENESIS/' /data/params/crwusiz/CarList_HYUNDAI > /data/params/crwusiz/CarList_Genesis
  cat /data/openpilot/selfdrive/car/toyota/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > /data/params/crwusiz/CarList_TOYOTA
  awk '/TOYOTA/' /data/params/crwusiz/CarList_TOYOTA > /data/params/crwusiz/CarList_Toyota
  awk '/LEXUS/' /data/params/crwusiz/CarList_TOYOTA > /data/params/crwusiz/CarList_Lexus
  cat /data/openpilot/selfdrive/car/honda/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > /data/params/crwusiz/CarList_Honda
  cat /data/openpilot/selfdrive/car/gm/values.py | grep ' = "' | awk -F'"' '{print $2}' | sed '$d' > /data/params/crwusiz/CarList_Gm

  # git last commit log
  git log -1 --pretty=format:"%h, %cs, %cr" > /data/params/d/GitLog

  # git remote
  #sed 's/.\{4\}$//' /data/params/d/GitRemote > /data/params/crwusiz/GitRemote_

  # panda safety init
  if [ ! -f "/data/PANDA_DEFAULT" ] && [ ! -f "/data/PANDA_MDPS" ]; then
    touch /data/PANDA_DEFAULT
    echo -n "0" > /data/params/d/PandaSafetySelect
  elif [ -f "/data/PANDA_DEFAULT" ]; then
    cp -f /data/openpilot/scripts/add/main.c /data/openpilot/panda/board/
    cp -f /data/openpilot/scripts/add/safety_defaults.h /data/openpilot/panda/board/safety/
    cp -f /data/openpilot/scripts/add/bxcan.h /data/openpilot/panda/board/drivers/
    echo -n "0" > /data/params/d/PandaSafetySelect
  elif [ -f "/data/PANDA_MDPS" ]; then
    cp -f /data/openpilot/scripts/add/main_mdps.c /data/openpilot/panda/board/main.c
    cp -f /data/openpilot/scripts/add/safety_defaults_mdps.h /data/openpilot/panda/board/safety/safety_defaults.h
    cp -f /data/openpilot/scripts/add/bxcan_mdps.h /data/openpilot/panda/board/drivers/bxcan.h
    echo -n "1" > /data/params/d/PandaSafetySelect
  fi

  # events language init
  if [ ! -f "/data/EVENTS_EN" ] && [ ! -f "/data/EVENTS_KO" ]; then
    touch /data/EVENTS_EN
    echo -n "main_en" > /data/params/d/LanguageSetting
  elif [ -f "/data/EVENTS_EN" ]; then
    cp -f /data/openpilot/scripts/add/events.py /data/openpilot/selfdrive/controls/lib/events.py
    echo -n "main_en" > /data/params/d/LanguageSetting
  elif [ -f "/data/EVENTS_KO" ]; then
    cp -f /data/openpilot/scripts/add/events_ko.py /data/openpilot/selfdrive/controls/lib/events.py
    echo -n "main_ko" > /data/params/d/LanguageSetting
  fi

  # start manager
  cd selfdrive/manager
  ./build.py && ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
