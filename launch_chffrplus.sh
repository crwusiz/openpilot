#!/usr/bin/bash

if [ -z "$BASEDIR" ]; then
  BASEDIR="/data/openpilot"
fi

source "$BASEDIR/launch_env.sh"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

function agnos_init {
  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta

  # set success flag for current boot slot
  sudo abctl --set_success

  # TODO: do this without udev in AGNOS
  # udev does this, but sometimes we startup faster
  sudo chgrp gpu /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 660 /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0

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
  if [ -f /AGNOS ]; then
    agnos_init
  fi

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

  # git remote branch list
  git branch -r | sed '1d' | sed -e 's/[/]//g' | sed -e 's/origin//g' | sort -r > ${PARAMS_ROOT}/crwusiz/GitBranchList

  # git remote
  #sed 's/.\{4\}$//' ${PARAMS_ROOT}/d/GitRemote > ${PARAMS_ROOT}/crwusiz/GitRemote_

  # events language init
  LANG=$(cat ${PARAMS_ROOT}/d/LanguageSetting)
  EVENTSTAT=$(git status)
  UIFILE="$BASEDIR/selfdrive/ui/ui.h"
  E1='"openpilot Unavailable"'
  E2='"Waiting for controls to start"'
  E3='"TAKE CONTROL IMMEDIATELY"'
  E4='"Controls Unresponsive"'
  E5='"Reboot Device"'
  K1='"오픈파일럿을 사용할수없습니다"'
  K2='"프로세스가 준비중입니다"'
  K3='"핸들을 잡아주세요"'
  K4='"프로세스가 응답하지않습니다"'
  K5='"장치를 재부팅하세요"'

  # events.py 한글로 변경 및 파일이 교체된 상태인지 확인 / ui.h 영문 내용 바꾸기
  if [ "${LANG}" = "main_ko" ] && [[ ! "${EVENTSTAT}" == *"modified:   selfdrive/controls/lib/events.py"* ]]; then
    cp -f $BASEDIR/selfdrive/controls/lib/events.py $BASEDIR/scripts/add/events_en.py
    cp -f $BASEDIR/scripts/add/events_ko.py $BASEDIR/selfdrive/controls/lib/events.py
    if [[ ! "${EVENTSTAT}" == *"modified:   selfdrive/controls/ui/ui.h"* ]]; then
    sed -i -e "s/${E1}/${K1}/g" -e "s/${E2}/${K2}/g" -e "s/${E3}/${K3}/g" -e "s/${E4}/${K4}/g" -e "s/${E5}/${K5}/g" "$UIFILE"
    fi
  elif [ "${LANG}" = "main_en" ] && [[ "${EVENTSTAT}" == *"modified:   selfdrive/controls/lib/events.py"* ]]; then
    cp -f $BASEDIR/scripts/add/events_en.py $BASEDIR/selfdrive/controls/lib/events.py
    if [[ "${EVENTSTAT}" == *"modified:   selfdrive/controls/ui/ui.h"* ]]; then
    sed -i -e "s/${K1}/${E1}/g" -e "s/${K2}/${E2}/g" -e "s/${K3}/${E3}/g" -e "s/${K4}/${E4}/g" -e "s/${K5}/${E5}/g" "$UIFILE"
    fi
  fi

  # start manager
  cd selfdrive/manager
  if [ ! -f $DIR/prebuilt ]; then
    ./build.py
  fi
  ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
