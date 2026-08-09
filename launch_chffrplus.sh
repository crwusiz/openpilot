#!/usr/bin/env bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

source "$DIR/launch_env.sh"

function agnos_init {
  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta
  rm -f /data/scons_cache/config.lock

  # set success flag for current boot slot
  sudo abctl --set_success

  # TODO: do this without udev in AGNOS
  # udev does this, but sometimes we startup faster
  sudo chgrp gpu /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 660 /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0

  # Check if AGNOS update is required
  if [ $(< /VERSION) != "$AGNOS_VERSION" ]; then
    AGNOS_PY="$DIR/openpilot/common/hardware/comma/agnos.py"
    MANIFEST="$DIR/openpilot/system/hardware/comma/agnos.json"
    MODEL="$(tr -d '\000\r\n' 2>/dev/null < /sys/firmware/devicetree/base/model | tr '[:upper:]' '[:lower:]')"
    MODEL="${MODEL#comma }"
    if [ "$MODEL" = "c3" ] || [ "$MODEL" = "tici" ]; then
      MANIFEST="$DIR/openpilot/common/hardware/comma/agnos_tici.json"
    fi
    if $AGNOS_PY --verify $MANIFEST; then
      sudo reboot
    fi
    if ! python3 $DIR/openpilot/system/ui/updater.py $AGNOS_PY $MANIFEST; then
      echo "python updater failed, falling back to bundled updater"
      $DIR/openpilot/common/hardware/comma/updater $AGNOS_PY $MANIFEST
    fi
  fi
}

function launch {
  # Remove orphaned git lock if it exists on boot
  [ -f "$DIR/.git/index.lock" ] && rm -f $DIR/.git/index.lock

  # Check to see if there's a valid overlay-based update available. Conditions
  # are as follows:
  #
  # 1. The DIR init file has to exist, with a newer modtime than anything in
  #    the DIR Git repo. This checks for local development work or the user
  #    switching branches/forks, which should not be overwritten.
  # 2. The FINALIZED consistent file has to exist, indicating there's an update
  #    that completed successfully and synced to disk.

  if [ -f "${DIR}/.overlay_init" ]; then
    find ${DIR}/.git -newer ${DIR}/.overlay_init | grep -q '.' 2> /dev/null
    if [ $? -eq 0 ]; then
      echo "${DIR} has been modified, skipping overlay update installation"
    else
      if [ -f "${STAGING_ROOT}/finalized/.overlay_consistent" ]; then
        if [ ! -d /data/safe_staging/old_openpilot ]; then
          echo "Valid overlay update found, installing"
          LAUNCHER_LOCATION="${BASH_SOURCE[0]}"

          mv $DIR /data/safe_staging/old_openpilot
          mv "${STAGING_ROOT}/finalized" $DIR
          cd $DIR

          echo "Restarting launch script ${LAUNCHER_LOCATION}"
          unset AGNOS_VERSION
          exec "${LAUNCHER_LOCATION}"
        else
          echo "openpilot backup found, not updating"
          # TODO: restore backup? This means the updater didn't start after swapping
        fi
      fi
    fi
  fi

  # handle pythonpath
  ln -sfn $(pwd) /data/pythonpath
  export PYTHONPATH="$PWD"

  # submodule package symlinks for PYTHONPATH imports on device.
  # on PC these come from editable installs via pyproject.toml / uv.
  ln -sfn msgq_repo/msgq msgq
  ln -sfn opendbc_repo/opendbc opendbc
  ln -sfn rednose_repo/rednose rednose
  ln -sfn teleoprtc_repo/teleoprtc teleoprtc
  ln -sfn tinygrad_repo/tinygrad tinygrad

  # hardware specific init
  if [ -f /AGNOS ]; then
    agnos_init
  fi

  # write tmux scrollback to a file
  tmux capture-pane -pq -S-1000 > /tmp/launch_log

  PARAMS_ROOT="/data/params"

  if [ ! -d "${PARAMS_ROOT}/crwusiz" ]; then
    mkdir -p ${PARAMS_ROOT}/crwusiz
  fi

  FINGERPRINTS="$DIR/opendbc/car/fingerprints.py"
  grep "HYUNDAI.HYUNDAI" "$FINGERPRINTS" | awk -F ': ' '{print $1}' | awk '{gsub(/^ +| +$/,"")}1' | sed 's/"//g' | sort > ${PARAMS_ROOT}/crwusiz/CarList_Hyundai
  grep "HYUNDAI.KIA" "$FINGERPRINTS" | awk -F ': ' '{print $1}' | awk '{gsub(/^ +| +$/,"")}1' | sed 's/"//g' | sort > ${PARAMS_ROOT}/crwusiz/CarList_Kia
  grep "HYUNDAI.GENESIS" "$FINGERPRINTS" | awk -F ': ' '{print $1}' | awk '{gsub(/^ +| +$/,"")}1' | sed 's/"//g' | sort > ${PARAMS_ROOT}/crwusiz/CarList_Genesis

  MANUFACTURER=$(cat ${PARAMS_ROOT}/d/SelectedManufacturer)
  if [ "${MANUFACTURER}" = "HYUNDAI" ]; then
    cp -f ${PARAMS_ROOT}/crwusiz/CarList_Hyundai ${PARAMS_ROOT}/crwusiz/CarList
  elif [ "${MANUFACTURER}" = "KIA" ]; then
    cp -f ${PARAMS_ROOT}/crwusiz/CarList_Kia ${PARAMS_ROOT}/crwusiz/CarList
  elif [ "${MANUFACTURER}" = "GENESIS" ]; then
    cp -f ${PARAMS_ROOT}/crwusiz/CarList_Genesis ${PARAMS_ROOT}/crwusiz/CarList
  else
    pushd ${PARAMS_ROOT}/crwusiz
    cat CarList_Hyundai CarList_Kia CarList_Genesis | sort -u > CarList
    popd
  fi

  # git remote branch list
  git branch -r | sed '1d' | sed -e 's/[/]//g' | sed -e 's/origin//g' | sort -r > ${PARAMS_ROOT}/crwusiz/GitBranchList

  # events language init
  LANG=$(cat ${PARAMS_ROOT}/d/LanguageSetting)
  GITSTAT=$(git status)

  # events.py 한글로 변경 및 파일이 교체된 상태인지 확인
  if [ "${LANG}" = "ko" ] && [[ ! "${GITSTAT}" == *"modified:   openpilot/selfdrive/selfdrived/events.py"* ]]; then
    cp -f $DIR/openpilot/selfdrive/selfdrived/events.py $DIR/scripts/add/events_en.py
    cp -f $DIR/scripts/add/events_ko.py $DIR/openpilot/selfdrive/selfdrived/events.py
  elif [ "${LANG}" = "en" ] && [[ "${GITSTAT}" == *"modified:   openpilot/selfdrive/selfdrived/events.py"* ]]; then
    cp -f $DIR/scripts/add/events_en.py $DIR/openpilot/selfdrive/selfdrived/events.py
  fi

  # openpilot ssh key installer
  if [ ! -f /data/params/d/GithubSshKeys ]; then
    echo -n openpilot > /data/params/d/GithubUsername
    cat /usr/comma/setup_keys > /data/params/d/GithubSshKeys
  fi

  if [ "$(cat /data/params/d/SshEnabled 2>/dev/null)" = "0" ]; then
    echo -n 1 > /data/params/d/SshEnabled
  fi

  if [ "$(cat /data/params/d/AdbEnabled 2>/dev/null)" = "0" ]; then
    echo -n 1 > /data/params/d/AdbEnabled
  fi

  ADDON_REQUIREMENTS="$DIR/openpilot/selfdrive/addon/requirements.txt"
  ADDON_REQUIREMENTS_HASH=$(sha256sum "$ADDON_REQUIREMENTS" | awk '{print $1}')

  if [ -f /AGNOS ]; then
    ADDON_DEPS="/data/addon_deps"
    ADDON_INSTALL_TMPDIR="/data/addon_pip_tmp"
    mkdir -p "$ADDON_DEPS"
    mkdir -p "$ADDON_INSTALL_TMPDIR"
    export ADDON_PYTHONPATH="$ADDON_DEPS"
    ADDON_REQUIREMENTS_MARKER="$ADDON_DEPS/.requirements_installed"
    ADDON_INSTALLER=(python3 -m pip install --no-cache-dir --upgrade --target "$ADDON_DEPS")
  else
    ADDON_SITE_PACKAGES=$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
    ADDON_REQUIREMENTS_MARKER="$ADDON_SITE_PACKAGES/.addon_requirements_installed"

    if python3 -m pip --version &>/dev/null; then
      ADDON_INSTALLER=(python3 -m pip install --break-system-packages)
    elif command -v uv &>/dev/null; then
      ADDON_INSTALLER=(uv pip install --python "$(command -v python3)")
    else
      echo "Neither pip nor uv is available for $(command -v python3)"
      exit 1
    fi
  fi

  if [ ! -f "$ADDON_REQUIREMENTS_MARKER" ] || [ "$(cat "$ADDON_REQUIREMENTS_MARKER")" != "$ADDON_REQUIREMENTS_HASH" ]; then
    echo "Installing addon dependencies from $ADDON_REQUIREMENTS..."
    (
      if [ -n "${ADDON_INSTALL_TMPDIR:-}" ]; then
        export TMPDIR="$ADDON_INSTALL_TMPDIR"
      fi
      "${ADDON_INSTALLER[@]}" -r "$ADDON_REQUIREMENTS"
    ) || {
      echo "Failed to install addon dependencies"
      exit 1
    }
    echo -n "$ADDON_REQUIREMENTS_HASH" > "$ADDON_REQUIREMENTS_MARKER"
  fi

  # start manager
  cd openpilot/system/manager
  if [ ! -f $DIR/prebuilt ]; then
    ./build.py
  fi
  ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
