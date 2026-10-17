# Addon 의존성 설치

`launch_chffrplus.sh`는 C4의 저장된 옵션을 읽어 사용하는 그룹만 준비합니다. `requirements.txt`는 모든 기능을 수동 설치할 때 쓰는 모음이며 부팅 설치에는 사용하지 않습니다. PC에서는 실행할 기능의 그룹 파일을 선택해 설치합니다.

| 사용 기능 | 설치 그룹 | 추가 Python 패키지 |
| --- | --- | --- |
| 대시보드 | dashboard | NiceGUI, ansi2html |
| USB JPEG / USB H.264 | cluster-jpeg + cluster-usb | OpenCV, Pillow, PyUSB, PyCryptodome |
| Network JPEG | cluster-jpeg | OpenCV, Pillow |
| Network H.264의 C4 송신기 | cluster-h264 | libdatachannel-py; 저장소의 teleoprtc 사용 |
| cluster 꺼짐 | 없음 | cluster 그룹을 설치하지 않음 |

`cluster-jpeg` 그룹의 OpenCV·Pillow는 C4에서 화면을 합성하는 USB JPEG·USB H.264·Network JPEG가 함께 사용합니다. NumPy는 기존 openpilot 환경을 사용합니다. USB 벤더의 DES 암호화에 사용하는 PyCryptodome은 `cluster-usb`에서 버전과 import를 확인하며, 기존 openpilot/opendbc 환경의 버전이 일치하면 추가 설치를 생략합니다. USB의 네이티브 libusb도 기기 환경에 필요합니다.

USB H.264는 C4의 V4L2 하드웨어 인코더와 저장소 빌드에서 생성한 전용 네이티브 라이브러리를 사용합니다. USB H.264 선택 때문에 Network 전용 `cluster-h264`나 Pi의 aiortc/PyAV를 설치하지 않습니다. 지원되지 않는 기기나 빌드에서는 인코더 오류를 표시하며, 대시보드에서 USB JPEG로 돌아갈 수 있습니다. USB와 Network의 코덱은 각각 `ClusterUsbCodec`와 `ClusterNetworkCodec`에 저장됩니다.

대시보드는 현재 manager에서 항상 실행하므로 기본적으로 dashboard 그룹을 준비합니다. 기존 manager 옵션인 환경변수 `BLOCK=dashboard`를 설정하면 실행과 설치를 모두 제외합니다. 예를 들어 `launch_env.sh`에 `export BLOCK=dashboard`를 설정할 수 있습니다. 다른 프로세스도 제외 중이라면 쉼표 목록을 유지합니다. 이 설정을 제거한 뒤 다시 시작하면 대시보드를 사용할 수 있습니다.

AGNOS에서는 `/data/addon_deps/<그룹>`에 설치하고, 해당 기능의 프로세스에만 경로를 추가합니다. PC에서는 사용자 캐시의 Python ABI별 디렉터리를 사용합니다. `ADDON_DEPS_ROOT`로 위치를 바꿀 수 있습니다. 시스템 환경에 정확한 버전이 이미 있고 import에 성공하면 추가 설치를 생략합니다. 저장된 그룹은 요구 파일·Python ABI 해시와 import 검사를 통과하면 재설치하지 않습니다. 기존 공용 디렉터리나 시스템 패키지는 자동 삭제하지 않습니다.

부팅 후 처음 USB/JPEG/H.264 모드로 바꿔 필요한 그룹이 없다면 cluster가 **오프로드 상태에서만** 설치를 진행합니다. 주행 중에는 준비를 기다리며, 이미 설치된 모드는 바로 실행합니다. 설치 중 주행을 시작하거나 선택을 바꾸면 설치를 취소하고 완성되지 않은 파일을 사용하지 않습니다. 실패 시 이전 그룹을 보존하고 오프로드에서 재시도합니다. 진행 내용은 `cluster_debug.log`의 `[CLUSTER_DEPS_*]`에서 확인합니다.

수동 점검·준비는 C4 저장소 루트에서 실행합니다.

```bash
python3 openpilot/selfdrive/addon/dependencies.py selected --check
python3 openpilot/selfdrive/addon/dependencies.py selected
```

두 번째 명령은 선택된 그룹의 설치를 수행하므로 오프로드에서 사용합니다. 자동 부팅 설치는 manager 시작 전에 실행합니다.

Orange Pi는 별도 환경입니다. C4에 aiortc·PyAV·pygame을 추가하지 않습니다. Pi H.264에는 aiortc/PyAV, NumPy, Pillow, OpenCV가 필요하며 기존 `pi_update.sh`와 Pi 설치 스크립트가 OS 패키지를 준비합니다. Pi JPEG 수신은 pygame을 사용합니다.
