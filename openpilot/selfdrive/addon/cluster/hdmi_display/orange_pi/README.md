# Orange Pi 3W HDMI 클러스터 수신기

C4가 미러링폰 핫스팟을 통해 보내는 **1920x480 JPEG 프레임**을 HDMI 터치 모니터에 표시하는 독립 실행 패키지입니다. 확인된 HDMI 모드는 **480x1920**이며, 가로로 설치한 모니터에 맞춰 영상을 90도 회전합니다. openpilot 전체를 설치할 필요는 없습니다. Python 3.10 이상과 pygame이 필요합니다.

## 장비 확인 결과와 실행

2026-10-04 장비 점검에서 **KMSDRM + OpenGL ES 2로 HDMI 화면이 정상 출력되는 것을 확인했습니다.** systemd 서비스 실행과 재부팅 후 자동 연결도 확인했습니다. 출력은 `480x1920`, 영상 회전은 `90`, 터치 보정은 `270`이며 C4 `192.168.0.82:9200` 자동 검색·연결도 성공했습니다. 이 IP는 해당 점검 당시 주소이며 고정값으로 사용하지 않습니다.

기존 `Can't window GBM/EGL surfaces on window creation.` 오류는 화면 생성 전에 ES2를 요청하고 SDL 렌더러를 `opengles2`로 맞춘 뒤 재현되지 않았습니다. SDL 터치 누름·뗌 이벤트와 원본·보정 좌표 로그도 확인했습니다. **2026-10-08 버전부터 터치로 밝기 조절·화면 끄기·다시 켜기를 지원합니다.** 새 UI와 GPU 출력 경로의 실제 장비 동작, 네 모서리 보정 정확도는 업데이트 후 확인해야 합니다.

시작 중 `Could not restore CRTC` 메시지는 남았지만 이후 `HDMI display ready`와 C4 연결 로그가 기록됐고 화면도 정상 출력됐습니다. 이 점검에서는 초기화 실패로 이어지지 않았습니다.

**수정된 이 폴더 전체를 `/opt/cluster-receiver`에 반영한 뒤** SSH 또는 텍스트 콘솔에서 실행합니다. 이 스크립트는 기존 수신기와 디스플레이 매니저를 중지하므로 현재 데스크톱 로그인 세션이 종료됩니다. 부팅 설정은 바꾸지 않습니다.

```bash
sudo bash /opt/cluster-receiver/scripts/run_console.sh --log-touch
```

다른 실행 환경에서 GBM/EGL 오류가 다시 발생하면 진단 파일을 만듭니다. 출력되는 `/tmp/cluster-diagnostics.XXXXXXXX.log`에 EGL 설정 개수, 실패한 함수와 EGL 오류 코드, DRM 점유 및 서비스 로그가 저장됩니다.

```bash
sudo bash /opt/cluster-receiver/scripts/diagnose.sh --egl
```

진단은 서비스를 중지하거나 화면 모드를 바꾸지 않습니다. `--egl`은 버퍼·표면·컨텍스트를 잠시 만들었다가 해제합니다. 성공해도 실제 HDMI scanout/page flip까지 확인한 것은 아닙니다. 화면 출력을 먼저 확인하려면 아래 **데스크톱 실행**을 사용합니다.

## 확인된 장치 정보

| 항목 | 실제 출력 / 상태 |
| --- | --- |
| HDMI 모드 | `480x1920`, `card0-HDMI-A-1` connected |
| USB 터치 | `wch.cn USB2IIC_CTP_CONTROL`, ID `1a86:e5e3` |
| 입력 장치 | 현재 `/dev/input/event1`; 재부팅·USB 연결 순서에 따라 변경 가능 |
| 터치 입력 / 보정 | libinput touch, identity matrix; SDL 누름·뗌과 좌표 변환 확인, 네 모서리 정확도는 추가 점검 필요 |
| OS / 커널 | Orange Pi 1.0.2, Ubuntu 26.04 / `6.6.98-sun60iw2` |
| Python / SDL | OS `/usr/bin/python3` 3.14.4 / SDL 2.32.10 |
| SDL 비디오 드라이버 | x11, wayland, KMSDRM, offscreen, dummy, evdev |
| 카드 | HDMI `card0`, GPU `card1` / `renderD128` |
| DRM 점유 | 이전 Xorg master 점유는 display-manager 중지 후 해제됨 |
| HDMI 화면 | KMSDRM + GLES2, `480x1920`, 영상 회전 `90`으로 정상 출력 확인 |
| 터치 UI 동작 | 밝기·화면 끄기 구현; 새 버전의 실제 장비 점검 필요 |

`Size: 216x137mm`는 터치 장치 크기이며 HDMI 해상도가 아닙니다. `axp8191-pek`는 보드 전원 키입니다.

## 연결 구조

```text
안드로이드 미러링폰 핫스팟
├── C4: TCP 0.0.0.0:9200에서 대기, 1920x480 JPEG 송신
└── Orange Pi 3W: wlan0 대역에서 C4 검색
    ├── HDMI: 480x1920 모드에 회전한 영상 출력
    └── USB: 모니터 터치 컨트롤러 입력
```

C4의 기본 전송 방식은 `DISPLAY.transport = "usb"`입니다. 대시보드에서 `Network (Orange Pi HDMI)`를 선택한 뒤 cluster를 켭니다. 저장된 `ClusterDisplayTransport`가 기본값보다 우선합니다. `USB (TURZX Display)` 사용 중 Chestnut eGPU가 감지되면 `ClusterEnable`이 꺼지고 USB cluster가 중지됩니다. 부트로더 및 GPU 로딩 상태도 포함하며, eGPU를 분리해도 자동으로 다시 켜지지 않습니다. Network 모드는 eGPU 연결 여부와 관계없이 사용할 수 있습니다.

### 대시보드에서 Orange Pi IP 확인

C4 대시보드의 **Toggles → Cluster Enable** 아래에 `Orange Pi 연결됨 · IP: ...`가 표시됩니다. 연결된 TCP 수신기의 실제 IPv4 주소를 1초마다 확인하므로 핫스팟에서 새 IP를 받아도 다시 연결되면 표시가 바뀝니다. 이 주소가 SSH 업데이트 대상이며 C4 주소와 다릅니다.

연결이 끊기면 `Orange Pi 연결 대기 중`으로 바뀝니다. C4 송신 프로세스가 비정상 종료해 상태를 지우지 못해도 마지막 갱신으로부터 10초가 지나면 연결된 것으로 표시하지 않습니다. Cluster가 꺼져 있거나 USB 모드이면 Network 모드를 켜라는 안내가 표시됩니다.

일부 핫스팟은 접속 장치 간 통신을 차단합니다. 같은 SSID에서도 연결되지 않으면 AP/client isolation을 확인합니다. 프로토콜에는 인증·암호화가 없으므로 차량 내부의 신뢰할 수 있는 핫스팟에서 사용합니다.

## 스크립트 목록

명령은 `scripts/`의 Bash 파일로 제공합니다. `bash`로 실행하므로 실행 권한 설정은 필요하지 않습니다. 파일은 LF 줄바꿈을 사용합니다. **`deploy.sh`는 C4에서**, 나머지 스크립트는 Orange Pi에서 실행합니다. PC에서는 MobaXterm 등 SSH 클라이언트로 접속하면 됩니다.

| 파일 | 용도 |
| --- | --- |
| [install.sh](scripts/install.sh) | OS pygame·필수 라이브러리 설치, `/opt/cluster-receiver`로 패키지 복사 |
| [connect_wifi.sh](scripts/connect_wifi.sh) | 핫스팟 연결 및 IP 확인; 비밀번호는 대화형 입력 |
| [ensure_wifi.sh](scripts/ensure_wifi.sh) | 차량 `Android` 프로필 저장·재사용, 자동 연결·Wi-Fi 절전 해제 |
| [wifi_power_save.sh](scripts/wifi_power_save.sh) | 활성 Wi-Fi 프로필의 절전 설정만 변경하고 실제 인터페이스의 `off` 확인 |
| [wifi_boot_setup.sh](scripts/wifi_boot_setup.sh) | 수신기 시작·Wi-Fi 재연결 시 절전 해제 실행 설정 설치 |
| [run_console.sh](scripts/run_console.sh) | 데스크톱·수신기 중지, 연결된 HDMI 카드 선택, KMSDRM + GLES2 실행 |
| [diagnose.sh](scripts/diagnose.sh) | OS·SDL·DRM·서비스 진단 파일 저장; 선택적으로 GBM/EGL 점검 |
| [run_desktop.sh](scripts/run_desktop.sh) | 로그인한 X11 데스크톱에서 소프트웨어 화면 출력 |
| [touch.sh](scripts/touch.sh) | libinput 장치 목록 또는 지정 장치의 실제 이벤트 확인 |
| [service.sh](scripts/service.sh) | 자동 실행 설치·차량 Wi-Fi 부팅 설정·재시작·중지·상태 확인·로그 보기·데스크톱 복구 |
| [deploy.sh](scripts/deploy.sh) | C4에서 연결된 Pi IP 자동 확인, SSH/SCP로 파일 전송·적용 또는 이전 버전 복구 |
| [update.sh](scripts/update.sh) | Pi에서 전송 파일 검증·백업·교체·실행 확인·실패 시 복구 |
| [ssh_port.sh](scripts/ssh_port.sh) | Pi SSH의 22번 포트를 9122번으로 변경·검증, 실패 시 SSH 설정 복구 |

## 설치 및 Wi-Fi

이 폴더를 파일 전송 도구로 Orange Pi에 복사한 뒤 설치 스크립트를 실행합니다. 이미 `/opt/cluster-receiver`에 복사했다면 다음을 사용합니다. 다른 위치라면 해당 위치의 `scripts/install.sh`를 실행하면 됩니다.

```bash
sudo bash /opt/cluster-receiver/scripts/install.sh
```

Debian/Ubuntu의 OS `python3-pygame`을 설치하고 `/usr/bin/python3`로 실행합니다. 가상 환경은 필요하지 않습니다. 이미 수신기가 설치된 장비에는 Wi-Fi 절전 해제 실행 설정을 보완하며, 서비스 실행 계정·화면 옵션·데스크톱 부팅 대상은 유지합니다. 연결 대기 화면의 한국어 문구를 위해 `fonts-noto-cjk`도 설치합니다. 이전 버전을 설치한 장비는 수정된 파일을 반영하고 `install.sh`를 한 번 실행해 이 폰트를 추가합니다.

pip wheel의 SDL은 시스템 SDL과 빌드 기능이 다를 수 있습니다. `libdrm`·`libgbm` 설치만으로 wheel에 KMSDRM이 추가되지는 않습니다. 진단의 `pygame` 경로는 보통 `/usr/lib/python3/dist-packages/pygame/...`입니다. `.local`, `/usr/local`, `.venv`가 나오면 pip 설치나 `PYTHONPATH`가 OS 패키지를 가리는지 확인합니다.

### 차량 테더링 자동 연결

차량 환경의 기본 정보는 **SSID `Android`, 비밀번호 `12345678`, 인터페이스 `wlan0`**입니다. [ensure_wifi.sh](scripts/ensure_wifi.sh)가 같은 SSID의 기존 Wi-Fi 프로필을 찾아 재사용하고, 없으면 `cluster-vehicle-wlan0` 이름으로 생성합니다. 중복 프로필이 있으면 해당 인터페이스에서 실제 사용 중인 같은 SSID의 UUID를 우선합니다. 프로필 이름이 바뀌었더라도 SSID가 같으면 재사용하며, 다른 인터페이스에 묶인 프로필은 수정하지 않습니다.

새 프로필은 기본 비밀번호와 `psk-flags=0`, 로그인 사용자 제한 해제를 시스템 프로필에 저장하고 DHCP를 사용합니다. 기존 프로필은 비밀번호·보안 방식·`psk-flags`·사용자 제한·우선순위·IP 설정을 보존합니다. 비밀번호를 바꾸려면 `CLUSTER_WIFI_PASSWORD`를 명시해야 하며, 이때도 다른 보안·사용자 설정은 유지합니다. 기존 프로필이 로그인 사용자의 비밀번호 에이전트를 요구하는 경우 그 정책도 유지됩니다. 비밀번호를 실행 로그에 출력하지 않습니다.

차량 프로필의 자동 연결을 켜고 재시도 횟수를 `0`으로 지정합니다. `0`은 계속 재시도하는 설정입니다. 새 프로필의 우선순위는 `100`이며 기존 프로필의 우선순위는 유지합니다. 핫스팟이 꺼져 있어도 프로필을 먼저 저장할 수 있으며, 핫스팟이 나중에 켜지면 NetworkManager가 연결을 시도합니다. 현재 활성 연결을 강제로 바꾸는 명령은 실행하지 않습니다. [NetworkManager 자동 연결 설정](https://networkmanager.dev/docs/api/latest/settings-connection.html)

프레임 송수신 지연을 줄이기 위해 차량 프로필에 `802-11-wireless.powersave=2`(절전 해제)를 저장합니다. [wifi_power_save.sh](scripts/wifi_power_save.sh)는 실제 활성 Wi-Fi 프로필의 절전 속성만 변경하고, `iw set power_save off` 뒤 `get power_save`로 결과를 확인합니다. 다른 SSID를 사용 중이면 그 프로필의 비밀번호·IP·자동 연결 정책은 유지합니다. 인터페이스를 생략하면 NetworkManager에서 Wi-Fi 장치를 찾아 적용하므로 `wlan1`도 지원합니다. 재접속이나 NetworkManager·SSH 재시작은 하지 않습니다. 드라이버가 요청을 지원하지 않거나 계속 `on`을 반환하면 경고와 실제 상태를 남깁니다. `install.sh`에는 `iw` 설치가 포함됩니다. [NetworkManager powersave 설정](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html), [iw 사용법](https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html)

```bash
sudo apt-get install -y iw
sudo bash /opt/cluster-receiver/scripts/wifi_power_save.sh wlan0
iw dev wlan0 get power_save
```

**2026-10-10 버전부터 `pi_update.sh`가 기존 Pi의 누락된 절전 해제 실행 설정도 자동 보완합니다.** 수신기 시작 전 검사와 NetworkManager의 연결 완료·재적용 이벤트에서 절전을 해제합니다. Wi-Fi 연결 후 드라이버가 다시 `on`으로 설정하는 경우를 처리하기 위해 `/etc/NetworkManager/dispatcher.d/90-cluster-wifi-power`를 설치합니다. 실행 시간은 8초로 제한합니다. 기존 수신기 계정·화면 옵션·부팅 대상과 다른 drop-in은 유지합니다. [NetworkManager dispatcher](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager-dispatcher.html)

파일을 직접 복사한 장비에서 실행 설정을 설치하려면 다음을 사용합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/service.sh wifi
sudo bash /opt/cluster-receiver/scripts/service.sh status
```

`wifi`는 NetworkManager를 시작하고 프로필을 즉시 저장하며 `wifi.conf`와 dispatcher를 설치합니다. 기존 `wifi.conf` 내용은 보존하고 절전 확인 명령을 추가합니다. 이후 수신기가 시작할 때마다 다음 준비 명령이 실행되므로 프로필을 삭제한 경우에도 다시 생성합니다. 처음 설치할 때는 `service.sh enable <계정>`에 이 설정이 포함됩니다.

```ini
[Unit]
Wants=NetworkManager.service
After=NetworkManager.service

[Service]
ExecStartPre=-+/usr/bin/timeout --kill-after=2s 10s /bin/bash /opt/cluster-receiver/scripts/ensure_wifi.sh wlan0
ExecStartPre=-+/usr/bin/timeout --kill-after=2s 8s /bin/bash /opt/cluster-receiver/scripts/wifi_power_save.sh
```

Wi-Fi 준비만 root 권한으로 실행하고 수신기는 기존 서비스 계정으로 실행합니다. 프로필 준비는 10초, 절전 확인은 8초 제한과 종료 유예 2초를 두며 실패·시간 초과를 journal에 남기고 HDMI 초기화를 계속합니다. Wi-Fi 연결이나 DHCP 완료를 기다리는 단계는 없습니다. HDMI 연결 대기 화면을 띄운 뒤 수신기가 C4를 계속 검색합니다. `[CLUSTER_WIFI_POWER] ... desired=off actual=off`와 수집 파일의 `Power save: off`를 확인합니다. [systemd 실행 접두사 설명](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml)

부팅 설정 없이 프로필만 미리 저장하려면 다음을 실행합니다. 핫스팟이 보이지 않아도 사용할 수 있습니다.

```bash
sudo bash /opt/cluster-receiver/scripts/ensure_wifi.sh
```

다른 핫스팟에 즉시 접속하는 수동 명령은 다음과 같습니다. 이 명령은 NetworkManager가 비밀번호를 대화형으로 요청합니다. SSID·인터페이스는 환경에 맞게 바꿉니다. 차량 기본값을 변경하려면 `ensure_wifi.sh`의 기본값 또는 서비스의 `CLUSTER_WIFI_SSID`, `CLUSTER_WIFI_PASSWORD` 환경 변수를 변경합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/connect_wifi.sh "Android" wlan0
sudo bash /opt/cluster-receiver/scripts/touch.sh
```

USB 터치는 HDMI와 별도 케이블로 연결합니다. 밝기·화면 끄기 조작은 Pi에서 처리하며 C4로 터치 이벤트를 전송하지 않습니다. 이벤트 기록과 기존 좌표 보정도 유지합니다.

### 터치로 밝기 조절·화면 끄기

시작 밝기는 **8단계(80%)**입니다. 화면을 터치하면 밝기 슬라이더와 **화면 끄기** 버튼이 나타납니다. 슬라이더는 **1~10단계(10%, 20%, …, 100%)**로 조절하며, 현재 단계와 밝기를 함께 표시합니다. 닫기 버튼이나 메뉴 밖 터치로 닫을 수 있습니다. 조작하지 않으면 약 8초 뒤 메뉴가 닫힙니다. 꺼진 화면을 다시 터치하면 직전 밝기로 복원하고 최신 수신 영상을 표시합니다. 밝기는 현재 실행 동안 유지하며 서비스 재시작 시 기본값으로 돌아갑니다. 시작값은 `--brightness`에 퍼센트로 지정하며, 10% 단위로 반올림합니다.

밝기는 영상 색상을 어둡게 하는 방식이고, 화면 끄기는 검은 화면을 표시합니다. **HDMI 모니터의 물리 백라이트나 전원을 끄지는 않습니다.** 모니터가 검은 화면에서도 빛을 내면 모니터 자체 밝기 설정이 필요합니다. 화면이 꺼져 있는 동안 Pi는 JPEG 디코딩을 생략하고 최신 JPEG만 보관합니다. 양쪽이 새 버전이면 C4도 최대 5 FPS로 줄여 불필요한 렌더링·전송을 줄입니다. 다시 터치하면 보관한 영상으로 즉시 복원하고 다음 ACK에서 정상 프레임 속도로 돌아갑니다.

## 콘솔 실행과 회전

시작 직후 C4의 첫 영상이 도착할 때까지 검은 배경에 **`COMMA 연결 대기 중`**, **`네트워크와 클러스터 옵션을 확인하세요`**를 표시합니다. 핫스팟이 없거나 C4가 꺼져 있어도 대기 문구를 표시하며 주기적으로 연결을 재시도합니다. 첫 정상 프레임이 도착하면 클러스터 영상으로 바뀝니다. 대기 문구에도 영상과 같은 회전을 적용하므로 가로로 설치한 모니터에서 읽을 수 있습니다.

대기 문구 아래에는 실제 접속한 **`SSID: Android`**, **`IP: 192.168.43.27`**처럼 Pi의 네트워크 정보를 표시합니다. 수신기의 `--interface`(기본 `wlan0`)에서 NetworkManager 연결 상태와 IPv4 주소를 확인하며, 연결되지 않았거나 IP를 아직 받지 못했으면 **`Network Offline`**을 표시합니다. 인터넷 접속 여부나 C4 연결 여부와는 별개인 로컬 네트워크 상태입니다. 유선 인터페이스를 지정하면 SSID 대신 `Network: <인터페이스>`가 표시됩니다.

상태는 대기 중 약 2초마다 별도 작업 스레드에서 조회하고 변경됐을 때 화면을 다시 그립니다. AP 재검색은 요청하지 않으며 접속·연결 해제·IP 변경이 반영됩니다. 조회 실패나 시간 초과에도 대기화면과 터치 처리는 계속됩니다.

한글 폰트를 찾지 못하면 `Waiting for COMMA connection`과 `Check Network and COMMA cluster option`을 pygame 기본 폰트로 표시합니다. 한글 표시에는 위의 `install.sh`를 사용합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/run_console.sh --log-touch
```

스크립트 기본값은 `wlan0`, 출력 `480x1920`, 회전 `90`입니다. 뒤에 붙이는 수신기 옵션으로 변경할 수 있습니다. 영상이 거꾸로 보이면 다음을 사용합니다. 폭·높이 옵션만으로 OS에 없는 HDMI 모드를 만들 수는 없습니다. 영상은 회전 후 출력 크기에 맞춥니다.

```bash
sudo bash /opt/cluster-receiver/scripts/run_console.sh --rotation 270 --log-touch
```

C4 IP를 알고 있다면 자동 검색을 생략합니다. 아래 IP는 실제 C4 IP로 바꿉니다.

```bash
sudo bash /opt/cluster-receiver/scripts/run_console.sh --host 192.168.43.10
```

연결이 끊기거나 기본 2초 안에 완전한 프레임을 받지 못하면 마지막 운행 영상을 지우고 연결 대기 화면으로 돌아갑니다. `--frame-timeout`으로 시간을 조절합니다. 재연결 후 새 프레임이 도착하면 정상 영상으로 복구됩니다. ESC, 창 닫기, Ctrl+C는 수동 실행을 종료하며 종료 시 화면을 검게 지웁니다.

### 오렌지파이 부팅 로고

전원을 켠 뒤 나타나는 OS 부팅 로고도 변경할 수 있습니다. 이 수신기의 COMMA 대기화면은 OS 부팅 이후에 표시되며, 부팅 로고는 OS 테마나 부트로더에서 관리합니다.

공식 Orange Pi 빌드의 Plymouth `orangepi` 테마는 `/usr/share/plymouth/themes/orangepi/watermark.png`를 중앙 이미지로 설치합니다. 로딩 애니메이션이 함께 나오는 화면이 이 테마를 사용한다면 해당 PNG를 교체합니다. 먼저 Pi에서 실제 테마와 파일을 확인합니다. [공식 테마 패키지 빌드](https://github.com/orangepi-xunlong/orangepi-build/blob/next/scripts/compilation.sh#L741), [테마 설정](https://github.com/orangepi-xunlong/orangepi-build/blob/next/external/packages/plymouth-theme-orangepi/orangepi.plymouth)

```bash
readlink -f /usr/share/plymouth/themes/default.plymouth
ls -l /usr/share/plymouth/themes/orangepi/
ls -l /boot/boot.bmp /boot/logo.bmp
```

활성 테마가 `orangepi`이고 `watermark.png`가 있는 경우, 원하는 투명 배경 PNG를 `/tmp/custom-logo.png`로 전송한 뒤 원본을 백업하고 교체합니다. 이미지가 물리 패널 방향에 맞게 표시되는지도 다음 부팅에서 확인합니다.

```bash
sudo cp -an /usr/share/plymouth/themes/orangepi/watermark.png /usr/share/plymouth/themes/orangepi/watermark.png.original
sudo install -m 644 /tmp/custom-logo.png /usr/share/plymouth/themes/orangepi/watermark.png
sudo update-initramfs -u
```

Plymouth 이미지는 초기 부팅용 initramfs에도 들어가므로 이미지 교체 후 갱신이 필요합니다. [공식 테마 설치 스크립트](https://github.com/orangepi-xunlong/orangepi-build/blob/next/external/packages/plymouth-theme-orangepi/debian/postinst)

전원 직후 로딩 애니메이션 없이 나타나는 로고는 별도의 부트로더 단계일 수 있습니다. 공식 빌드는 `/boot/boot.bmp`와 `/boot/logo.bmp`도 설치하지만, 실제 사용 파일과 BMP 형식은 해당 OS의 부트로더를 확인해야 합니다. [공식 부팅 이미지 설치 코드](https://github.com/orangepi-xunlong/orangepi-build/blob/next/scripts/distributions.sh#L471)

### 터치 좌표

`--log-touch`는 검색·연결 대기 중에도 원본 `raw`와 보정된 `cluster` 좌표를 기록합니다. 가로로 설치한 모니터의 네 모서리를 누릅니다. `cluster`는 왼쪽 위 `(0, 0)`, 오른쪽 위 `(1, 0)`, 왼쪽 아래 `(0, 1)`, 오른쪽 아래 `(1, 1)` 부근이어야 합니다.

기본 보정은 영상 회전의 역방향이며 `--rotation 90`에서는 `(raw_y, 1 - raw_x)`입니다. 터치가 이미 가로 좌표를 보고한다면 다음처럼 보정을 끕니다. 다른 방향이면 `--touch-rotation 90`, `180`, `270` 중 맞는 값을 선택합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/run_console.sh --touch-rotation 0 --log-touch
```

SDL 로그가 없으면 수신기를 종료한 뒤 실제 입력을 확인합니다. 장치 번호가 바뀌었다면 인자 없는 `touch.sh`로 새 경로를 확인합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/touch.sh /dev/input/event1
```

libinput 이벤트는 나오지만 SDL 로그가 없으면 SDL 입력 드라이버와 실행 사용자의 `input` 그룹을 확인합니다. libinput 인식만으로 SDL finger 이벤트까지 확인된 것은 아닙니다.

## GBM/EGL 오류 진단

`kmsdrm not available`은 비디오 초기화 실패입니다. 이전 GBM/EGL 오류는 그 단계를 통과한 뒤 발생했습니다. [SDL 2.32.10 KMSDRM 코드](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/kmsdrm/SDL_kmsdrmvideo.c#L1088)는 GBM 표면, EGL 표면, EGL current 설정의 실패를 최종적으로 같은 화면 생성 오류로 덮어씁니다. `Could not restore CRTC`는 실패한 화면을 정리하는 과정에서도 출력되므로 최초 원인으로 확정하지 않습니다.

KMSDRM은 일반 pygame Surface에도 EGL을 사용합니다. 이번 수정은 ES2 프로파일, depth/stencil 0, `SDL_RENDER_DRIVER=opengles2`를 사용합니다. [SDL EGL 코드](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/SDL_egl.c#L716)와 [렌더러 선택 문서](https://wiki.libsdl.org/SDL2/SDL_HINT_RENDER_DRIVER)를 바탕으로 적용했으며 위 장비에서 정상 화면 출력을 확인했습니다. 다른 OS 이미지·GPU 드라이버의 동작은 각각 확인해야 합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/diagnose.sh --egl
```

진단 기본 대상은 연결된 HDMI 카드입니다. 다른 카드와 비교하려면 번호를 지정합니다. 현재 `card1`은 GPU이며 HDMI는 `card0`에 있습니다. GPU 카드의 probe 성공만으로 HDMI 출력이 가능한 것은 아닙니다.

```bash
sudo bash /opt/cluster-receiver/scripts/diagnose.sh --egl --card 1 --output /tmp/cluster-card1.log
```

| 진단 결과 | 다음 확인 |
| --- | --- |
| SDL 목록에 KMSDRM 없음 | pygame에 연결된 SDL 빌드 기능과 OS pygame 경로 |
| 라이브러리 `load FAILED` | 표시된 라이브러리의 OS 패키지·링크 경로 |
| 다른 프로세스의 DRM `master=y` | 해당 세션의 점유 해제 여부; 콘솔 스크립트는 display-manager를 중지함 |
| `eglInitialize` 실패 | 선택 카드와 GBM/EGL 공급자 호환성 |
| ARGB8888 window의 OpenGL=0, ES2>0 | 데스크톱 OpenGL 설정 차이가 원인일 가능성; ES2 재시도 결과 |
| ES2 설정도 없음 | SDL이 요구하는 EGL 창 설정이 없음; X11 경로 점검 |
| GBM/EGL 표면 생성 실패 | 실패한 함수·오류 코드, 카드·버퍼 형식·GPU 드라이버 연결 |
| probe 성공, 수신기는 실패 | SDL 화면 생성·모드 설정·page flip은 별도 확인 필요 |

`load OK`는 라이브러리 로드 성공이며 GPU 렌더링 성공이 아닙니다. debugfs `clients`가 없거나 읽히지 않으면 DRM 점유는 미확인입니다. [master 검사 우회](https://wiki.libsdl.org/SDL2/SDL_HINT_KMSDRM_REQUIRE_DRM_MASTER)는 점유된 HDMI를 사용할 수 있게 해주지 않습니다.

이 보드 OS는 [Orange Pi sun60iw2 빌드 설정](https://github.com/orangepi-xunlong/orangepi-build/blob/next/external/config/sources/families/sun60iw2.conf)에 별도 IMG 그래픽 패키지를 사용합니다. 진단 없이 다른 보드용 Mali 라이브러리를 설치하거나 vendor EGL/GBM 파일을 교체하지 않습니다.

## 데스크톱 실행

KMSDRM 경로가 계속 실패하면 기존 X11 데스크톱에서 소프트웨어 출력을 점검합니다. SSH에서는 다음으로 클러스터 서비스를 중지·비활성화하고 display-manager를 시작합니다. 자동 실행 설정 때 저장한 부팅 대상을 복원하며, 저장된 값이 없으면 `graphical.target`을 사용합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/service.sh desktop
```

이후 **모니터에 로그인한 사용자의 데스크톱 터미널**에서 실행합니다. `sudo`를 사용하지 않습니다. `SDL_VIDEODRIVER=x11`, `SDL_FRAMEBUFFER_ACCELERATION=0`, `SDL_RENDER_DRIVER=software`로 [Surface의 3D 가속 경로](https://wiki.libsdl.org/SDL2/SDL_HINT_FRAMEBUFFER_ACCELERATION)를 끕니다.

```bash
bash /opt/cluster-receiver/scripts/run_desktop.sh --log-touch
```

데스크톱에서 이미 화면을 가로로 회전했다면 다음을 사용합니다. 터치 보정은 실제 모서리 좌표에 맞춥니다.

```bash
bash /opt/cluster-receiver/scripts/run_desktop.sh --width 1920 --height 480 --rotation 0 --log-touch
```

창 점검에는 `--windowed`를 추가합니다. 실행 중인 X11/XWayland 세션이 필요합니다. SSH에서 임의로 `DISPLAY=:0`을 지정하거나 접근 권한을 풀어주는 작업은 하지 않습니다. X11 성공은 KMSDRM 성공과 별개입니다.

## 부팅 시 자동 실행

**콘솔 수동 실행에서 HDMI 출력을 확인한 뒤** 전용 클러스터 환경에 적용합니다. X11로만 확인한 경우에는 이 KMSDRM 서비스를 설치하지 않습니다.

수동 실행 중인 수신기를 **Ctrl+C로 먼저 종료**하고 수정된 폴더를 `/opt/cluster-receiver`에 반영합니다. 위 `install.sh`로 한글 폰트를 설치한 뒤 아래를 실행하면 서비스를 즉시 시작하고 다음 부팅에도 자동 실행합니다. 기본 출력과 회전은 수동 확인한 `480x1920`, `90`입니다.

기본 서비스 계정은 `orangepi`입니다. 실제 계정이 다르면 두 번째 인자로 지정합니다. 스크립트는 계정 존재 여부를 검사하고 systemd `account.conf`에 User/Group을 반영합니다. 다른 회전·터치 옵션으로 점검했다면 설치 전에 `cluster-hdmi.service`의 `ExecStart`에도 반영합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/service.sh enable orangepi
sudo bash /opt/cluster-receiver/scripts/service.sh status
sudo bash /opt/cluster-receiver/scripts/service.sh logs
```

`enable`은 서비스 파일과 차량 Wi-Fi 준비용 `wifi.conf`를 설치하고 데스크톱을 중지하며 다음 부팅 대상을 `multi-user.target`으로 바꿉니다. 이전 대상은 `/var/lib/cluster-receiver/previous-target`에 한 번만 저장합니다. 수정한 서비스 파일 적용에도 `enable`을 사용합니다. 다른 drop-in 설정은 유지됩니다. 이미 설치된 서비스에 Wi-Fi 준비만 추가할 때는 `service.sh wifi`를 사용합니다.

### `Unit cluster-hdmi.service not loaded`에서 설치가 멈춘 경우

패키지 설치가 완료됐어도 이전 `service.sh`는 `reset-failed`에서 이 오류가 발생하면 자동 실행 등록 전에 중단됐습니다. 이때 `status`는 `disabled`, `inactive (dead)`를 표시합니다. 해당 로그만으로 pygame이나 서비스 실행 계정의 문제라고 판단하지 않습니다.

수정된 `scripts/service.sh`는 이 특정 오류만 건너뛰고 등록·시작을 계속합니다. 다른 초기화 오류와 실제 서비스 시작 오류는 그대로 반환합니다. 부팅 대상 변경도 등록·시작 명령이 성공한 뒤 수행합니다. 기존 `/var/lib/cluster-receiver/previous-target` 값은 유지합니다.

수정된 스크립트를 `/opt/cluster-receiver/scripts/service.sh`에 반영한 뒤 다음을 다시 실행합니다. 패키지와 폰트 설치를 반복할 필요는 없습니다.

```bash
sudo bash /opt/cluster-receiver/scripts/service.sh enable orangepi
sudo bash /opt/cluster-receiver/scripts/service.sh status
```

`enabled`, `active (running)`이 되지 않으면 `service.sh logs`에서 새 시작 시각의 오류를 확인합니다. 과거 실패 로그는 서비스 재등록 후에도 journal에 남습니다.

### 부팅 후 확인과 재시작

서비스는 [systemd의 `network.target`](https://systemd.io/NETWORK_ONLINE/) 기준으로 시작해 핫스팟의 IP 할당 완료를 기다리지 않습니다. 수신기가 먼저 HDMI 대기 문구를 띄우고 Wi-Fi/C4 연결을 재시도합니다. 재부팅 뒤에도 `service.sh status`에서 `enabled`, `active (running)`인지 확인할 수 있습니다.

서비스는 `/usr/bin/python3`, 출력 `480x1920`, 회전 `90`, GLES2 렌더러와 `video`, `render`, `input` 그룹, tty1을 사용합니다. stdout/stderr는 journal에 기록합니다. 초기화 실패는 60초 내 최대 5회 시작 후 중지합니다. 원인을 해결한 뒤 다음으로 실패 상태를 초기화하고 재시작합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/service.sh restart
```

`logs`에서 Ctrl+C는 로그 보기만 종료합니다. 서비스 중지는 다음을 사용하며 데스크톱 복구는 `service.sh desktop`을 사용합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/service.sh stop
```

## C4에서 SSH로 수동 업데이트

최초 설치와 자동 실행 설정이 완료된 Pi에는 **C4 저장소 루트의 [scripts/pi_update.sh](../../../../../../scripts/pi_update.sh)를 수동 실행**합니다. 이 진입 스크립트가 패키지의 `scripts/deploy.sh`를 호출해 Pi에 파일을 전송하고 Pi의 `scripts/update.sh`로 적용합니다. PC에서는 MobaXterm으로 **C4에 SSH 접속**합니다. C4와 Pi가 같은 핫스팟에 있어 SSH로 통신할 수 있어야 하며, C4에 `ssh`, `scp`, `sha256sum`, `setsid`, Python 3가 필요합니다.

C4 저장소를 먼저 원하는 버전으로 업데이트합니다. 대시보드의 Git Pull 기능이나 기존 C4 업데이트 절차를 사용할 수 있습니다. 이 스크립트는 **C4에 저장된 현재 수신기 파일**을 전송하며 Git pull은 수행하지 않습니다.

이번 IP 표시 기능을 처음 반영할 때는 C4의 클러스터와 대시보드를 다시 시작합니다. C4를 재부팅해도 됩니다. 이후 Network 모드에서 Pi가 연결되면 대시보드에 주소가 나타나며 스크립트가 같은 주소를 자동으로 사용합니다.

MobaXterm의 C4 SSH 터미널에서 실행합니다. Pi가 클러스터에 연결되어 있으면 주소를 자동으로 가져옵니다.

```bash
cd /data/openpilot
bash scripts/pi_update.sh
```

현재 디렉터리에 관계없이 `bash /data/openpilot/scripts/pi_update.sh`로 실행할 수도 있습니다. 기존 `deploy.sh` 옵션은 그대로 전달됩니다.

Pi가 클러스터에 연결되지 않은 경우에는 주소를 직접 지정합니다. `ORANGE_PI_IP`를 대시보드에서 확인한 **Pi의 IP**로 바꿉니다. 이름이 해석되는 환경에서는 `orangepizero3w`나 SSH 설정의 호스트 별칭도 사용할 수 있습니다.

```bash
bash scripts/pi_update.sh ORANGE_PI_IP
```

기본 Pi SSH 계정은 현재 장비에서 사용 중인 **`root`**, 우선 접속 포트는 **`9122`**, 비밀번호는 **`orangepi`**입니다. 9122번의 연결 거부·시간 초과 등 연결 실패일 때만 **`22`**번을 시도합니다. 비밀번호·호스트 키 오류에는 포트를 바꾸지 않고 중단합니다. SSH 비밀번호는 자동 입력하며, 처음 접속하는 Pi의 호스트 키도 `StrictHostKeyChecking=accept-new`로 자동 등록합니다. 기존에 저장된 키가 달라지면 접속은 중단됩니다. [OpenSSH 호스트 키 확인](https://man.openbsd.org/ssh_config.5#StrictHostKeyChecking)

**22번으로 접속한 일반 업데이트가 성공하면** 새로 설치한 `scripts/ssh_port.sh`를 실행해 Pi SSH 포트를 **9122번으로 변경**합니다. 이후 22번 포트를 검색하는 SSH 접속 앱이 Pi를 대상으로 잡지 않도록, 변경 완료 시 9122번의 수신 대기와 22번의 종료를 확인합니다. 다음 Pi SSH 접속에는 9122번을 지정합니다. `--dry-run`은 접속·포트 변경을 수행하지 않으며, `--rollback`은 수신기 파일 복구만 수행합니다.

포트 변경만 따로 실행하려면 Pi SSH 터미널에서 다음을 사용합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/ssh_port.sh
```

이 명령은 `/etc/ssh/sshd_config`와 `sshd_config.d/*.conf`의 22번 포트 설정을 바꾸며 다른 SSH 포트는 유지합니다. 변경 전 설정은 `/var/lib/cluster-receiver/ssh-port/<시각.식별자>`에 보관하고 `sshd -t`로 검증합니다. 기본 포트가 주석으로만 있는 경우 전역 `Port 9122`를 추가합니다. 이미 9122번 또는 다른 포트만 사용하는 설정은 그대로 둡니다. [OpenSSH Port·Include 설정](https://man.openbsd.org/sshd_config.5#Port), [sshd 설정 검증](https://man.openbsd.org/sshd.8#t)

`ssh.socket` 또는 `sshd.socket`이 활성 상태이면 socket의 `ListenStream`도 실제 SSH 설정에 맞춰 변경합니다. 설정 적용이나 실제 포트 확인이 실패하면 이전 SSH 설정을 복구하고 오류를 반환합니다. 수신기 업데이트 후 포트 변경만 실패한 경우, 수신기 파일은 이미 새 버전입니다. [Ubuntu SSH socket activation](https://discourse.ubuntu.com/t/sshd-now-uses-socket-based-activation-ubuntu-22-10-and-later/30189)

별도 `sshpass` 설치 없이 임시 `SSH_ASKPASS` 도우미와 `setsid`로 처음 한 번 인증한 후 SSH 연결을 재사용합니다. 도우미 파일에 비밀번호를 쓰거나 SSH 명령 인자로 전달하지 않으며, 임시 도우미는 실행 종료 시 정리합니다. 비밀번호가 틀리거나 인증된 연결이 끊기면 입력 대기 없이 오류로 종료합니다. [OpenSSH SSH_ASKPASS](https://man.openbsd.org/ssh.1#ENVIRONMENT), [SSH 연결 재사용](https://man.openbsd.org/ssh_config.5#ControlMaster)

다른 비밀번호는 `CLUSTER_PI_PASSWORD` 환경 변수로 지정할 수 있습니다. 직접 입력하려면 다음처럼 `--ask-password`를 추가합니다.

```bash
bash scripts/pi_update.sh --ask-password
```

다른 계정이면 `--user orangepi`, 다른 포트면 `--port 2222`를 추가합니다. `--port`를 지정하면 그 포트만 사용하고 자동 재시도는 하지 않습니다. `--port 22`로 실행한 일반 업데이트에도 9122번 변경이 적용됩니다. 일반 계정에는 Pi의 `sudo` 권한이 필요하며 적용 단계의 sudo 비밀번호는 직접 입력합니다. 키 인증은 **C4에 있는 키 파일**을 `--identity /path/to/id_ed25519`로 지정합니다. 암호가 있는 개인키의 암호를 직접 입력하려면 `--ask-password`를 함께 지정합니다. [OpenSSH SSH 옵션](https://man.openbsd.org/ssh.1), [SCP 옵션](https://man.openbsd.org/scp.1)

전송할 파일 목록만 확인하려면 다음을 사용합니다. SSH에 접속하지 않습니다.

```bash
bash scripts/pi_update.sh --dry-run
```

Git 커밋 여부와 관계없이 C4에 저장된 수정 사항이 포함됩니다. 동작 순서는 다음과 같습니다.

1. 수신기 Python 파일, Bash 스크립트, README, requirements, 서비스 템플릿을 별도 폴더에 모으고 UTF-8/LF와 SHA-256 체크섬으로 준비합니다. openpilot 전체와 `.venv`, 테스트, 캐시는 제외합니다.
2. Pi의 임시 폴더에 전송한 뒤 체크섬, Bash 문법, OS Python의 컴파일·pygame 및 수신기 모듈 가져오기를 검사합니다. 이 단계가 실패하면 실행 중인 수신기를 중지하지 않습니다.
3. 기존 수신기 파일을 `/var/lib/cluster-receiver/updates/<시각.식별자>/previous`에 백업하고 서비스를 잠시 중지해 파일을 교체합니다.
4. 실행 중이거나 실패 상태였던 서비스는 다시 시작합니다. 현재 systemd 실행의 `Cluster receiver ready` 로그와 동일한 실행이 유지되는지 최대 약 20초간 확인합니다. 화면 준비 확인에는 C4 연결이나 Wi-Fi IP 할당이 필요하지 않습니다. 명시적으로 중지되어 있던 서비스는 중지 상태를 유지합니다.
5. 적용 또는 실행 확인이 실패하면 이전 파일을 복구하고 이전에 실행 중이던 서비스를 다시 시작합니다. 성공 시 `Receiver apply complete`와 백업 위치가 출력됩니다.

**실행 계정, 해상도·회전·인터페이스 옵션, 부팅 대상, 서비스 enable 상태는 유지합니다.** `/opt/cluster-receiver/cluster-hdmi.service` 템플릿만 새 파일이 되며 설치된 unit은 덮어쓰지 않습니다. 업데이트가 기존 `wifi.conf`를 보존하면서 절전 확인 명령을 추가하고 재연결용 dispatcher를 설치합니다. 현재 활성 Wi-Fi의 절전 속성만 적용하며 SSH 연결을 재시작하지 않습니다. 두 실행 설정 파일의 이전 내용·존재 여부도 백업하고 실패 복구·수동 rollback에서 복원합니다. 다른 systemd 설정 변경이 필요한 업데이트에서는 해당 설정을 검토한 뒤 `service.sh enable <계정>`으로 별도 적용합니다. 일반 파일 업데이트에는 `install.sh`나 apt를 다시 실행하지 않습니다.

업데이트 중에는 HDMI 화면과 C4 연결이 잠시 끊겼다가 복구됩니다. 백업은 자동 삭제하지 않으며 실행 결과에 백업 위치를 표시합니다. 파일 교체 전에 복구할 백업과 기존 서비스 실행 상태를 기록합니다. SSH나 전원이 끊겨 최종 결과를 받지 못했다면 Pi에 재접속해 `service.sh status`와 `service.sh logs`로 확인합니다. 미완료 업데이트가 남아 있으면 새 업데이트를 중단하므로 아래 `--rollback`으로 먼저 복구합니다. 중단 과정에서 서비스가 멈췄어도 원래 실행 중이었다면 복구 후 다시 시작합니다.

### 이전 버전으로 복구

가장 최근 성공한 업데이트 직전의 파일로 돌아가려면 **C4 SSH 터미널**에서 실행합니다. Pi 수신기가 중지되어 자동으로 IP를 확인할 수 없는 경우에도 사용할 수 있도록 주소를 직접 지정합니다.

```bash
bash scripts/pi_update.sh ORANGE_PI_IP --rollback
```

Pi SSH 터미널에서 직접 실행할 수도 있습니다. 복구용 스크립트는 수신기 디렉터리 밖에도 보관하므로 첫 업데이트 이전 버전으로 돌아간 뒤에도 사용할 수 있습니다.

```bash
sudo bash /var/lib/cluster-receiver/updates/update.sh rollback
sudo bash /opt/cluster-receiver/scripts/service.sh status
```

복구도 현재 파일을 먼저 백업하므로 다시 `rollback`을 실행하면 복구 직전 버전으로 돌아갑니다. 복구 시 이전 버전의 화면 준비 로그로 실행 상태를 확인합니다. 새 버전에서 추가한 파일도 복구 과정에서 정리하며, 패키지 외의 별도 설정 파일은 유지합니다. 자동 복구까지 실패하면 스크립트는 오류를 반환하고 백업 위치를 출력합니다. 이 경우 다음 `rollback`은 실패한 업데이트 직전의 백업을 우선 사용하며, 복구가 완료되기 전에는 새 업데이트를 적용하지 않습니다.

## 수신기 옵션

스크립트 뒤에 아래 옵션을 붙입니다. 스크립트 기본값은 `480x1920`, 회전 `90`이고 Python 수신기를 직접 실행할 때의 기존 기본값은 `1920x480`, 회전 `0`입니다.

```text
--interface wlan0       핫스팟 Wi-Fi 인터페이스
--host 192.168.43.10     C4 IPv4 직접 지정
--port 9200             C4 검색 포트
--scan-timeout 0.12     주소별 연결 제한 시간(초)
--scan-workers 32       병렬 검색 작업 수
--reconnect-delay 2     재검색 대기 시간(초)
--frame-timeout 2       프레임 전체 수신 제한 시간(초)
--width 480             실제 출력 폭
--height 1920           실제 출력 높이
--rotation 90           영상 시계 방향 회전: 0, 90, 180, 270
--touch-rotation 0      터치 시계 방향 보정(기본: 영상 회전의 역방향)
--log-touch             원본·보정 SDL 터치 좌표 로그
--brightness 80         시작 밝기: 10~100%, 10% 단위 (기본 8단계/80%)
--renderer auto         auto: SDL2 GPU 출력 시도, surface: 기존 CPU 출력
--display-index 0       SDL 디스플레이 번호; DRM card 번호와 별개
--windowed              창 모드
--show-cursor           포인터 표시; 터치 좌표 표시 기능은 아님
```

PC 창 모드 점검에서는 `requirements.txt`를 PC 가상 환경에 설치하고 Python 수신기를 직접 실행합니다. PC pip 설치와 Orange Pi OS pygame 설치는 별개입니다.

## 프레임 성능 확인

Network 모드는 **C4 UI와 같은 60 FPS를 목표**로 독립적인 출력 주기를 사용합니다. C4 UI의 `FPS` 환경 변수가 있으면 이를 따르되 최대 60 FPS로 제한합니다. 카메라와 모델은 각자의 원래 업데이트 주기로 들어오며, 새 카메라 영상이 없는 출력 주기에는 최신 영상을 사용합니다. 중간 카메라 프레임을 만들어 내는 보간은 하지 않습니다. USB 모드는 기존 20 FPS와 JPEG 품질 68을 유지하며, **Network JPEG 품질은 82**입니다. 해상도는 기존 USB `1920x462`, Network `1920x480`을 유지합니다.

전송·인코딩 대기가 있으면 C4가 새 영상을 계속 렌더링하지 않고 빈 슬롯을 기다립니다. Pi가 연결되지 않았을 때는 연결 시작용 영상을 초당 한 장만 준비합니다. 경로·차선 알파 합성은 색상별 lookup table을 재사용해 매번 단색 배열을 만들던 비용을 줄입니다. Cluster 프로세스의 OpenCV와 BLAS 작업 스레드 수를 1로 제한하고 Linux CPU 우선순위를 낮춥니다. manager가 NumPy를 이미 불러온 상태에서 fork했다면 cluster 자식 프로세스만 같은 PID로 새 Python 인터프리터에서 시작해 스레드 제한을 적용합니다.

C4와 Pi가 모두 새 버전이면 첫 프레임의 헤더와 ACK에 있는 예약 바이트로 스트리밍 지원을 확인합니다. 첫 프레임은 화면 출력 완료를 기다리고, 이후에는 Pi가 JPEG 본문을 받은 즉시 ACK를 보냅니다. C4는 기본 최대 **2장, 합계 512 KiB**의 ACK를 기다리는 동안 다음 프레임을 보낼 수 있습니다. 한 장이 512 KiB보다 크면 대기 프레임이 없을 때만 전송하며 프로토콜의 4 MiB 제한은 유지합니다. 오래된 ACK가 250ms 이상 대기 중이면 새 프레임 준비·전송을 기다립니다. 보내지 않은 영상도 생성 후 250ms가 지나면 버리고 최신 상태에서 다시 준비합니다. 이 값은 실제 출력 지연의 보장치가 아니라, 과거 영상을 더 쌓지 않기 위한 제한입니다.

2026-10-09 로그에서는 평균 수신 확인이 약 6.3 FPS이고 ACK 대기가 약 1.78초였습니다. 이전 12장 창은 느린 링크에서 약 2초 분량의 영상을 TCP에 쌓을 수 있었습니다. Pi가 대기 JPEG 한 장만 유지해도 TCP에 이미 들어간 과거 영상은 건너뛸 수 없으므로 전송 대기량을 줄였습니다. Pi는 별도 스레드로 수신하고 대기 JPEG 한 장만 유지하며, 출력이 늦어지면 대기 영상을 최신 영상으로 교체합니다. SDL 이벤트와 화면 출력은 수신기 메인 스레드에서 처리합니다. [SDL 화면 출력의 스레드 제약](https://wiki.libsdl.org/SDL2/SDL_RenderPresent)

Pi는 SDL2 texture를 재사용하며 회전·크기 조절·밝기 조절을 GPU에 맡깁니다. JPEG RGB24 영상을 같은 32bit ARGB Surface에 변환하고 같은 형식의 texture로 업로드해 매 프레임 전체 변환 버퍼를 새로 할당하던 비용을 줄입니다. `[CLUSTER_HDMI_UPLOAD] ... staging=reused_argb8888`로 새 경로를 확인합니다. 사용할 수 없으면 기존 Surface 출력으로 자동 전환합니다. `[CLUSTER_HDMI_RENDERER] texture` 또는 `surface` 로그로 선택된 경로를 확인합니다. 새 출력 경로에 문제가 있으면 `run_console.sh --renderer surface --log-touch`로 기존 경로와 비교할 수 있습니다. [pygame SDL2 texture·renderer API](https://www.pygame.org/docs/ref/sdl2_video.html), [Texture.update 형식 변환 구현](https://github.com/pygame/pygame/blob/2.6.1/src_c/cython/pygame/_sdl2/video.pyx#L721)

패킷 크기와 버전은 유지하므로 한쪽만 업데이트한 경우 기존의 화면 출력 완료 ACK 방식(`legacy`)을 사용합니다. 이 방식의 `network_avg`에는 Wi-Fi 전송뿐 아니라 Pi의 JPEG 디코딩·회전·화면 출력과 ACK 왕복 시간이 포함됩니다. 스트리밍 방식(`stream`)의 ACK는 수신 확인이며 실제 화면 출력 완료를 뜻하지 않습니다. JPEG 크기/FPS로 계산한 처리량도 Wi-Fi 링크 속도를 직접 측정한 값은 아닙니다.

수정된 수신기를 C4의 `bash scripts/pi_update.sh`로 반영하고 C4 클러스터도 다시 시작합니다. C4의 `[CLUSTER_NETWORK_MODE] stream`과 Pi의 `[CLUSTER_RX_MODE] stream`을 확인합니다. `legacy`이면 양쪽 파일 업데이트와 실행 중인 프로세스 재시작 여부를 확인합니다. 성능 로그는 다음과 같이 비교합니다.

| 로그 | 항목과 의미 |
| --- | --- |
| C4 `[CLUSTER_NETWORK_PERF]` | `fps`: 정상 ACK를 받은 프레임 수, `prep_avg`: JPEG 준비, `send_avg`: 두 `sendall` 호출, `ack_wait_avg/max`: 전송 호출 종료부터 ACK까지. `send_age_avg/max`: 생성부터 전송 시작까지, `frame_age_avg/max`: 생성부터 ACK까지이며 실제 화면 표시까지의 나이는 아닙니다. `encode_age_avg`·`encoded_wait_avg`는 생성→인코딩 완료·인코딩 완료→전송 시작, `window_wait_avg/max`는 전송 창 대기 시간입니다. `ack_gap_max`는 ACK 사이 최장 간격, `pending_frames/kb`·`oldest_ack_age`는 현재 전송 대기량·가장 오래된 ACK의 나이, `stale_drops`는 만료로 폐기한 영상입니다. `mode=stream`, `ack=receive`이면 수신 확인이고 `mode=legacy`, `ack=display`이면 출력 완료 확인입니다. `screen_off`는 Pi가 알린 화면 끄기 상태입니다. 정상 ACK가 있는 동안 약 10초마다 기록합니다. |
| C4 `[CLUSTER_MAIN_PERF]` | `target`: 목표 FPS, `transport_skipped`: 대기·화면 끄기 때문에 렌더링을 생략한 주기 수. `camera_copy_avg`: 영상 복사, `snapshot_avg`: 모델/HUD 상태 조회와 잠금 대기, `path_avg`: 경로·차선·리드 표시, `hud_avg`: PIL 변환과 HUD 합성. 약 10초마다 기록합니다. |
| C4 `[CLUSTER_RESOURCE_PERF]` | Cluster CPU·RSS·스레드 수, 기기 CPU·온도·메모리, 모델 드롭률·실행 시간, deviceMotion의 `inputsOK`·`posenetOK`를 함께 기록합니다. `onroad_started`는 deviceState의 주행 상태, `car_state_seen`은 차량 데이터 수신 여부, `model_age_ms`·`device_motion_age_ms`·`car_state_age_ms`는 마지막 수신 후 경과 시간입니다. `cpu_pct=100`은 CPU 한 코어를 계속 사용한 값입니다. `model_drop_max`와 해당 구간의 오류 업데이트 수로 잠깐 발생한 문제도 확인합니다. 아직 받지 못한 상태는 `n/a`로 표시하며, 미수신 중 오류 카운터 0은 건강 확인을 뜻하지 않습니다. |
| Pi `[CLUSTER_RX_PERF]` | `mode=stream`일 때 `fps`: 수신 FPS, `display_fps`: 실제 영상 출력 FPS, `dropped`: 대기 JPEG 교체 횟수입니다. 화면 끄기·메뉴 다시 그리기는 영상 출력 수에 포함하지 않습니다. `queue_wait_avg/max`: 수신 후 출력 대기, `display_gap_max`: 영상 출력 사이의 최대 간격, `display_max`: 최악의 화면 처리 시간도 기록합니다. `header_wait_avg`: 다음 헤더 대기, `receive_avg`: JPEG 본문 수신, `display_avg`: 전체 화면 처리, `ack_send_avg`: ACK 전송 호출입니다. `payload_wait_avg/max`·`payload_read_calls_avg`·`payload_read_max`는 JPEG 수신 중 readiness 대기와 읽기 호출을 구분합니다. `decode/rotate/scale/blit/flip`의 평균·최대값, `convert/upload/draw`의 평균·최대값도 기록합니다. `blit`에는 색상 변환·texture 업로드·draw가 포함되므로 이 세 값을 다시 더하지 않습니다. |

`send_avg`가 짧아도 커널 송신 버퍼에 들어간 JPEG가 아직 전송 중일 수 있습니다. `ack_wait_avg`만으로 Wi-Fi 병목을 판정하지 않고 Pi의 `receive_avg`와 표시 단계 시간을 함께 봅니다. `header_wait_avg`에는 C4가 다음 프레임을 준비하는 시간도 포함됩니다. readiness·소켓 읽기 호출의 시간에는 OS 대기·수신 스레드 스케줄·GIL 재획득도 포함되므로 무선 전송 시간만의 측정값으로 해석하지 않습니다. 스트리밍 로그의 수신 단계 평균은 수신한 프레임 수, 표시 단계 평균은 실제 출력한 프레임 수로 계산합니다. 송수신·인코딩·화면 처리는 겹쳐 동작하므로 모든 단계 시간을 더해서 FPS를 계산하지 않습니다. `[CLUSTER_RX_RUNTIME]`의 pygame·SDL·SDL_image 버전과 `[CLUSTER_RX_SOCKET]`의 실제 소켓 버퍼도 연결 환경 비교에 사용합니다.

수신 `fps`가 목표에 가까워도 `display_fps`가 낮으면 Pi 화면 처리 병목이 남아 있는 것입니다. 수신 `fps`도 낮다면 C4 렌더링·인코딩, JPEG 수신 시간과 Wi-Fi 상태를 확인합니다. 실제 60 FPS 출력은 C4·Pi·무선 링크에서 확인해야 합니다. CPU 우선순위·스레드 제한은 자원 경쟁을 줄이는 조치이며, 기존 로그만으로 model lagging이나 locationd 오류의 원인이 cluster라고 단정할 수는 없습니다. 새 자원 로그와 오류 발생 시각을 대조하고 같은 조건에서 cluster를 끈 주행과 비교합니다.

Pi 로그는 다음으로 확인합니다.

```bash
journalctl -u cluster-hdmi.service -b --no-pager | grep -E 'CLUSTER_HDMI_RENDERER|CLUSTER_RX_MODE|CLUSTER_RX_PERF'
```

C4 heartbeat의 `Dropped: encoded`는 대기 영상을 교체하거나 만료로 폐기한 횟수입니다. 그중 `Stale drops`는 생성 후 250ms를 넘겨 보내지 않은 영상입니다. TCP 패킷 손실 횟수가 아닙니다. 생성·대기 시간은 C4 벽시계가 보정되어도 영향을 받지 않도록 monotonic 시계를 사용합니다.

### Cluster 로그와 Pi 로그 함께 업로드

대시보드 **System Logs → Cluster Debug → UPLOAD**를 사용하면 C4 로그를 업로드한 뒤 Pi에 SSH로 접속해 로그를 수집하고 함께 업로드합니다. 두 파일의 시각·차량·기기 접두사는 같습니다.

```text
..._cluster_debug.log       C4 렌더링·전송·자원 상태
..._cluster_pi_debug.log    Pi 서비스 journal·출력 성능·수집 상태
```

Pi 파일에는 최근 `cluster-hdmi.service` 로그 최대 2500줄, 수집 시각·부팅 정보, 서비스 상태, Wi-Fi 절전·현재 링크 상태를 담습니다. UTC 시각과 최신 순서로 journal을 기록하며, 저장된 이전 부팅의 로그도 포함합니다. 전체 파일은 최대 512 KiB입니다. Wi-Fi 비밀번호나 전체 연결 프로필은 수집하지 않습니다. SSH 포트는 **9122를 먼저 시도**하고 연결 자체가 실패한 경우에만 22로 재시도합니다. 인증 실패나 Pi 명령 실패에는 다른 포트로 재시도하지 않으며 SSH 설정도 바꾸지 않습니다.

Pi는 수집할 때 켜져 있고 C4와 같은 핫스팟에 연결되어 있어야 합니다. 현재 연결된 IP가 없으면 C4 주행 로그의 마지막 Pi 연결 주소를 사용합니다. 다른 주소·계정을 쓰면 `CLUSTER_PI_HOST`, `CLUSTER_PI_USER`, `CLUSTER_PI_IDENTITY`, `CLUSTER_PI_PASSWORD` 환경 변수로 지정할 수 있습니다. 수집은 25초 제한을 공유하며 주소 검색도 로그 뒤에서 최대 32 MiB만 읽습니다. Pi 수집에 실패해도 C4 로그 업로드는 유지하고, Pi 파일에 실패 상태를 기록하며 대시보드에는 경고를 표시합니다. 명확한 USB 전용 로그는 SSH를 시도하지 않고 수집 생략 상태를 기록합니다. Pi journal이 재부팅 후 남아 있지 않으면 이전 주행 로그를 복구할 수는 없습니다.

C4 SSH에서 직접 업로드할 수도 있습니다. 이 명령도 두 로그를 함께 처리합니다.

```bash
cd /data/openpilot
bash scripts/log_upload.sh /data/log/cluster_debug.log
```

## 장비 확인 순서

1. HDMI 영상이 가로 설치 방향에 맞고 잘리거나 늘어나지 않는지 확인합니다.
2. `--log-touch`로 네 모서리 보정을 확인하고, 밝기 슬라이더·화면 끄기·재터치 복원을 확인합니다. 80% 밝기가 여러 프레임에 걸쳐 더 어두워지지 않는지도 확인합니다.
3. 정차 상태에서 Wi-Fi를 끊어 2초 이내에 마지막 운행 영상이 연결 대기 화면으로 바뀌고, 재연결하면 최신 화면으로 복구되는지 확인합니다.
4. C4 `[CLUSTER_NETWORK_PERF]`의 모드·전송 시간과 Pi `[CLUSTER_RX_PERF]`의 `display_fps`·`display_gap_max`를 함께 확인합니다. 목표는 C4 UI와 같은 60 FPS입니다. 화면을 끈 동안 C4 출력이 최대 5 FPS로 줄고 재터치 후 복원되는지도 확인합니다.
5. model lagging 또는 locationd 오류가 발생하면 같은 시각의 `[CLUSTER_RESOURCE_PERF]`와 openpilot 오류 로그를 함께 보관합니다.
