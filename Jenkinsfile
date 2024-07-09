def retryWithDelay(int maxRetries, int delay, Closure body) {
  for (int i = 0; i < maxRetries; i++) {
    try {
      return body()
    } catch (Exception e) {
      sleep(delay)
    }
  }
  throw Exception("Failed after ${maxRetries} retries")
}

def device(String ip, String step_label, String cmd) {
  withCredentials([file(credentialsId: 'id_rsa', variable: 'key_file')]) {
    def ssh_cmd = """
ssh -tt -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 -o BatchMode=yes -o StrictHostKeyChecking=no -i ${key_file} 'comma@${ip}' /usr/bin/bash <<'END'

set -e

shopt -s huponexit # kill all child processes when the shell exits

export CI=1
export PYTHONWARNINGS=error
export LOGPRINT=debug
export TEST_DIR=${env.TEST_DIR}
export SOURCE_DIR=${env.SOURCE_DIR}
export GIT_BRANCH=${env.GIT_BRANCH}
export GIT_COMMIT=${env.GIT_COMMIT}
export AZURE_TOKEN='${env.AZURE_TOKEN}'
# only use 1 thread for tici tests since most require HIL
export PYTEST_ADDOPTS="-n 0"


export GIT_SSH_COMMAND="ssh -i /data/gitkey"

source ~/.bash_profile
if [ -f /TICI ]; then
  source /etc/profile

  rm -rf /tmp/tmp*
  rm -rf ~/.commacache
  rm -rf /dev/shm/*
  rm -rf /dev/tmp/tmp*

  if ! systemctl is-active --quiet systemd-resolved; then
    echo "restarting resolved"
    sudo systemctl start systemd-resolved
    sleep 3
  fi

  # restart aux USB
  if [ -e /sys/bus/usb/drivers/hub/3-0:1.0 ]; then
    echo "restarting aux usb"
    echo "3-0:1.0" | sudo tee /sys/bus/usb/drivers/hub/unbind
    sleep 0.5
    echo "3-0:1.0" | sudo tee /sys/bus/usb/drivers/hub/bind
  fi
fi
if [ -f /data/openpilot/launch_env.sh ]; then
  source /data/openpilot/launch_env.sh
fi

ln -snf ${env.TEST_DIR} /data/pythonpath

cd ${env.TEST_DIR} || true
${cmd}
exit 0

END"""

    sh script: ssh_cmd, label: step_label
  }
}

def deviceStage(String stageName, String deviceType, List extra_env, def steps) {
  stage(stageName) {
    if (currentBuild.result != null) {
        return
    }

    def extra = extra_env.collect { "export ${it}" }.join('\n');
    def branch = env.BRANCH_NAME ?: 'master';

    lock(resource: "", label: deviceType, inversePrecedence: true, variable: 'device_ip', quantity: 1, resourceSelectStrategy: 'random') {
      docker.image('ghcr.io/commaai/alpine-ssh').inside('--user=root') {
        timeout(time: 20, unit: 'MINUTES') {
          retry (3) {
            device(device_ip, "git checkout", extra + "\n" + readFile("selfdrive/test/setup_device_ci.sh"))
          }
          steps.each { item ->
            device(device_ip, item[0], item[1])
          }
        }
      }
    }
  }
}

def pcStage(String stageName, Closure body) {
  node {
  stage(stageName) {
    if (currentBuild.result != null) {
        return
    }

    checkout scm

    def dockerArgs = "--user=batman -v /tmp/comma_download_cache:/tmp/comma_download_cache -v /tmp/scons_cache:/tmp/scons_cache -e PYTHONPATH=${env.WORKSPACE} --cpus=8 --memory 16g -e PYTEST_ADDOPTS='-n8'";

    def openpilot_base = retryWithDelay (3, 15) {
      return docker.build("openpilot-base:build-${env.GIT_COMMIT}", "-f Dockerfile.openpilot_base .")
    }

    lock(resource: "", label: 'pc', inversePrecedence: true, quantity: 1) {
      openpilot_base.inside(dockerArgs) {
        timeout(time: 20, unit: 'MINUTES') {
          try {
            retryWithDelay (3, 15) {
              sh "git config --global --add safe.directory '*'"
              sh "git submodule update --init --recursive"
              sh "git lfs pull"
            }
            body()
          } finally {
              sh "rm -rf ${env.WORKSPACE}/* || true"
              sh "rm -rf .* || true"
            }
          }
        }
      }
    }
  }
}

def setupCredentials() {
  withCredentials([
    string(credentialsId: 'azure_token', variable: 'AZURE_TOKEN'),
  ]) {
    env.AZURE_TOKEN = "${AZURE_TOKEN}"
  }
}


node {
  env.CI = "1"
  env.PYTHONWARNINGS = "error"
  env.TEST_DIR = "/data/openpilot"
  env.SOURCE_DIR = "/data/openpilot_source/"
  setupCredentials()

  env.GIT_BRANCH = checkout(scm).GIT_BRANCH
  env.GIT_COMMIT = checkout(scm).GIT_COMMIT

  def excludeBranches = ['master-ci', 'devel', 'devel-staging', 'release3', 'release3-staging',
                         'testing-closet*', 'hotfix-*']
  def excludeRegex = excludeBranches.join('|').replaceAll('\\*', '.*')

  if (env.BRANCH_NAME != 'master') {
    properties([
        disableConcurrentBuilds(abortPrevious: true)
    ])
  }

  try {
    if (env.BRANCH_NAME == 'devel-staging') {
      deviceStage("build release3-staging", "tici-needs-can", [], [
        ["build release3-staging", "RELEASE_BRANCH=release3-staging $SOURCE_DIR/release/build_release.sh"],
      ])
    }

    if (env.BRANCH_NAME == 'master-ci') {
      deviceStage("build nightly", "tici-needs-can", [], [
        ["build nightly", "RELEASE_BRANCH=nightly $SOURCE_DIR/release/build_release.sh"],
      ])
    }

    if (!env.BRANCH_NAME.matches(excludeRegex)) {
    parallel (
      // tici tests
      'onroad tests': {
        deviceStage("onroad", "tici-needs-can", [], [
          // TODO: ideally, this test runs in master-ci, but it takes 5+m to build it
          //["build master-ci", "cd $SOURCE_DIR/release && TARGET_DIR=$TEST_DIR $SOURCE_DIR/scripts/retry.sh ./build_devel.sh"],
          ["build openpilot", "cd system/manager && ./build.py"],
          ["check dirty", "release/check-dirty.sh"],
          ["onroad tests", "pytest selfdrive/test/test_onroad.py -s"],
          ["time to onroad", "pytest selfdrive/test/test_time_to_onroad.py"],
        ])
      },
      'HW + Unit Tests': {
        deviceStage("tici-hardware", "tici-common", ["UNSAFE=1"], [
          ["build", "cd system/manager && ./build.py"],
          ["test pandad", "pytest selfdrive/pandad/tests/test_pandad.py"],
          ["test power draw", "pytest -s system/hardware/tici/tests/test_power_draw.py"],
          ["test encoder", "LD_LIBRARY_PATH=/usr/local/lib pytest system/loggerd/tests/test_encoder.py"],
          ["test pigeond", "pytest system/ubloxd/tests/test_pigeond.py"],
          ["test manager", "pytest system/manager/test/test_manager.py"],
        ])
      },
      'loopback': {
        deviceStage("loopback", "tici-loopback", ["UNSAFE=1"], [
          ["build openpilot", "cd system/manager && ./build.py"],
          ["test pandad loopback", "pytest selfdrive/pandad/tests/test_pandad_loopback.py"],
        ])
      },
      'camerad': {
        deviceStage("AR0231", "tici-ar0231", ["UNSAFE=1"], [
          ["build", "cd system/manager && ./build.py"],
          ["test camerad", "pytest system/camerad/test/test_camerad.py"],
          ["test exposure", "pytest system/camerad/test/test_exposure.py"],
        ])
        deviceStage("OX03C10", "tici-ox03c10", ["UNSAFE=1"], [
          ["build", "cd system/manager && ./build.py"],
          ["test camerad", "pytest system/camerad/test/test_camerad.py"],
          ["test exposure", "pytest system/camerad/test/test_exposure.py"],
        ])
      },
      'sensord': {
        deviceStage("LSM + MMC", "tici-lsmc", ["UNSAFE=1"], [
          ["build", "cd system/manager && ./build.py"],
          ["test sensord", "pytest system/sensord/tests/test_sensord.py"],
        ])
        deviceStage("BMX + LSM", "tici-bmx-lsm", ["UNSAFE=1"], [
          ["build", "cd system/manager && ./build.py"],
          ["test sensord", "pytest system/sensord/tests/test_sensord.py"],
        ])
      },
      'replay': {
        deviceStage("model-replay", "tici-replay", ["UNSAFE=1"], [
          ["build", "cd system/manager && ./build.py"],
          ["model replay", "selfdrive/test/process_replay/model_replay.py"],
        ])
      },
      'tizi': {
        deviceStage("tizi", "tizi", ["UNSAFE=1"], [
          ["build openpilot", "cd system/manager && ./build.py"],
          ["test pandad loopback", "SINGLE_PANDA=1 pytest selfdrive/pandad/tests/test_pandad_loopback.py"],
          ["test pandad spi", "pytest selfdrive/pandad/tests/test_pandad_spi.py"],
          ["test pandad", "pytest selfdrive/pandad/tests/test_pandad.py"],
          ["test amp", "pytest system/hardware/tici/tests/test_amplifier.py"],
          ["test hw", "pytest system/hardware/tici/tests/test_hardware.py"],
          ["test qcomgpsd", "pytest system/qcomgpsd/tests/test_qcomgpsd.py"],
        ])
      },

    )
    }
  } catch (Exception e) {
    currentBuild.result = 'FAILED'
    throw e
  }
}
