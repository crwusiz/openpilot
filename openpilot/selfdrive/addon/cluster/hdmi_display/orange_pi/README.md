# Orange Pi Zero 3W HDMI 클러스터 수신기

이 폴더는 C4가 미러링폰 핫스팟을 통해 보내는 클러스터 JPEG 프레임을 받아 Orange Pi Zero 3W의 HDMI에 연결된 10.3인치 1920x720 모니터에 전체 화면으로 표시하는 독립 실행 패키지입니다. openpilot 전체를 Orange Pi에 설치할 필요는 없습니다.

## 연결 구조

```text
안드로이드 미러링폰 핫스팟
├── C4: TCP 0.0.0.0:9200에서 대기, 1920x720 JPEG 송신
└── Orange Pi Zero 3W: wlan0 대역에서 C4 검색
    ├── HDMI: 10.3인치 1920x720 영상
    └── USB: 모니터 터치 컨트롤러 입력
```

C4의 `cluster_config.py`에서 `CLUSTER_DISPLAY_TRANSPORT = "network"`를 선택해야 합니다. `"usb"`를 선택하면 네트워크 송신기를 만들지 않고 C4에 직접 연결된 TURZX 9.2 디스플레이만 사용합니다.

> 일부 안드로이드 핫스팟은 접속 장치 간 통신을 차단합니다. C4와 Orange Pi가 같은 SSID에 있어도 연결되지 않으면 AP/client isolation 설정을 먼저 확인하십시오.

## 설치

Orange Pi OS 64-bit에서 HDMI 모드가 1920x720으로 인식되는지 먼저 확인합니다.

```bash
cat /sys/class/drm/card*-HDMI-A-*/modes
```

이 폴더의 내용을 Orange Pi의 `/opt/cluster-receiver`로 복사한 뒤 설치합니다.

```bash
sudo mkdir -p /opt/cluster-receiver
sudo cp -a orange_pi/. /opt/cluster-receiver/
sudo chown -R "$USER":"$USER" /opt/cluster-receiver
sudo apt update
sudo apt install -y python3-venv iproute2 libdrm2 libgl1 libgbm1 libinput-tools
python3 -m venv /opt/cluster-receiver/.venv
/opt/cluster-receiver/.venv/bin/python -m pip install -r /opt/cluster-receiver/requirements.txt
```

Wi-Fi 연결과 터치 장치 인식을 확인합니다.

```bash
sudo nmcli device wifi connect "핫스팟_SSID" password "핫스팟_비밀번호" ifname wlan0
ip -4 addr show wlan0
sudo libinput list-devices
```

터치 컨트롤러는 보통 HDMI가 아니라 별도 USB 케이블로 연결됩니다. SDL finger 이벤트는 수신기에서 계속 처리되며, 현재는 `last_touch`와 선택적 `touch_handler`로 전달됩니다. 실제 버튼 동작은 UI 요구사항이 정해진 뒤 연결하면 됩니다.

## 수동 실행

```bash
cd /opt/cluster-receiver
PYGAME_HIDE_SUPPORT_PROMPT=1 SDL_VIDEODRIVER=kmsdrm ./.venv/bin/python cluster_receiver.py --interface wlan0
```

기본값은 1920x720 전체 화면입니다. 데스크톱 세션에서 확인할 때는 `--windowed`를 추가하고 `SDL_VIDEODRIVER`를 해당 세션의 드라이버로 설정할 수 있습니다. 터치 좌표와 포인터를 함께 확인하려면 `--show-cursor`를 사용합니다.

## 자동 실행

서비스 파일은 기본 사용자 이름을 `orangepi`로 가정합니다. 이미지의 실제 사용자 이름이 다르면 `User`와 `Group`을 수정합니다.

```bash
sudo cp /opt/cluster-receiver/cluster-hdmi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cluster-hdmi.service
journalctl -u cluster-hdmi.service -f
```

서비스는 DRM/KMS와 터치 입력을 위해 `video`, `render`, `input` 그룹을 사용하며 tty1에서 실행됩니다. 데스크톱 로그인 화면과 동시에 실행하지 마십시오.

## 실행 옵션

```text
--interface wlan0       핫스팟 Wi-Fi 인터페이스
--port 9200             C4 검색 포트
--scan-timeout 0.12     주소별 연결 제한 시간(초)
--scan-workers 32       병렬 검색 작업 수
--reconnect-delay 2     재검색 대기 시간(초)
--width 1920            출력 폭
--height 720            출력 높이
--display-index 0       SDL 디스플레이 번호
--windowed              창 모드
--show-cursor           터치 점검용 포인터 표시
```

프로토콜에는 인증이나 암호화가 없습니다. 차량 내부의 신뢰할 수 있는 핫스팟에서만 사용하십시오.

