# Orange Pi 3W HDMI 클러스터 수신기

C4와 같은 미러링폰 핫스팟에 연결해 **H.264/WebRTC 카메라 영상과 TCP 상태 데이터**를 수신하고, Pi에서 **1920×480 클러스터 화면**을 합성해 HDMI 터치 모니터에 표시합니다. 확인된 HDMI 모드는 **480×1920**이며 가로 설치에 맞춰 90도 회전합니다. Python 3.10 이상, pygame, aiortc/PyAV, NumPy, Pillow, OpenCV가 필요합니다. openpilot 전체를 설치할 필요는 없습니다.

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

### 팬 상시 동작

`sun60iw2`의 PWM 팬은 **기본 출력 60%로 계속 동작**하고, 온도가 오르면 기존 커널 제어에 따라 속도를 높입니다. 실제 장치의 `cooling-levels`를 읽어 153/255(60%) 미만 값만 올립니다. 공식 보드의 `0 50 102 170 255`는 `153 153 153 170 255`가 되어, 낮은 온도에서는 60%, 고온에서는 약 67~100% 출력을 사용합니다. 60%는 PWM 출력 기준이며 회전수 측정값은 아닙니다. 기존 55°C 팬 제어 기준과 CPU 과열 보호를 유지합니다. 근거는 [공식 보드 설정](https://github.com/orangepi-xunlong/linux-orangepi/blob/orange-pi-6.6-sun60iw2/arch/arm64/boot/dts/allwinner/sun60i-a733-orangepi-zero3w.dts)과 [팬 드라이버](https://github.com/orangepi-xunlong/linux-orangepi/blob/orange-pi-6.6-sun60iw2/drivers/hwmon/pwm-fan.c)입니다.

설치·서비스 활성화·`pi_update.sh` 실행 시 부팅 overlay를 자동으로 설정합니다. **재부팅 후 적용**되며 C4의 `bash scripts/pi_update.sh`는 업데이트 성공 후 Pi를 재부팅합니다. 별도 팬 제어 프로세스는 필요 없고 클러스터가 꺼져 있어도 동작합니다. `dtc`가 없으면 Pi에서 `device-tree-compiler`만 추가 설치합니다. 기존 부팅 옵션과 다른 overlay는 보존하며, 업데이트 실패·rollback 시 팬 부팅 설정도 복구합니다. rollback으로 복구된 부팅 설정은 다음 재부팅에 반영됩니다.

Pi에서 팬 설정만 적용할 때는 아래 명령을 사용합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/fan_setup.sh install
sudo reboot
```

지원하는 PWM 팬 노드를 찾지 못하면 `[CLUSTER_FAN] Unsupported board/fan device tree` 경고를 남깁니다. 다음 `cluster_pi_debug.log`에는 부팅 overlay 설정, 실제 적용된 냉각 단계, 현재 `pwm1`, 온도와 thermal governor를 함께 수집합니다. 재부팅 후 `cooling-levels`가 모두 153 이상인지 확인하면 부팅 설정 적용 여부를 구분할 수 있습니다.

### 터치 하단 패널

화면을 터치하면 팝업 상단에 **`CPU [ 24% | 55.2°C ]` · `GPU [ 38% | 62.1°C ]` · `FAN 60%` · FPS**를 작은 글자로 표시합니다. CPU 사용률은 Pi 전체 사용률이며, 온도는 CPU 온도 영역 중 가장 높은 값입니다. CPU·GPU 묶음은 각 온도를 기준으로 **60°C 미만 초록 / 60~69°C 노랑 / 70~79°C 주황 / 80°C 이상 빨강**으로 표시하고, 읽을 수 없는 값은 중립색 `--`로 표시합니다. 표시 색상만 바꾸며 팬·과열 보호 기준은 변경하지 않습니다. 팬 회전수 센서가 있으면 RPM을 표시하고, 없으면 출력 비율만 표시하며 PWM 문구는 붙이지 않습니다. FPS는 새 영상/합성 화면의 실제 표시 횟수이며 대기 화면이나 메뉴만 다시 그리는 횟수는 포함하지 않습니다.

밝기는 **10~100%의 슬라이더·기본80%**를 사용하며 `밝기 80%`처럼 퍼센트만 표시합니다. 화면 끄기·터치로 다시 켜기는 유지합니다. 1920×480 기준 팝업은1840×340이며 화면 끄기·Git Pull·Pi 업데이트·로그 전송·닫기 버튼을 제공합니다. 패널은 마지막 조작 후8초에 닫힙니다. 업데이트·전송 중에는 진행 상태를 계속 표시하며, 닫기를 누르면 작업은 배경에서 계속됩니다. 이 터치 UI 변경은 **C4 업데이트 후 Pi Update**를 실행해야 적용됩니다.

별도 커밋 확인 버튼 없이 팝업을 열면 dashboard와 같은 C4 `scripts/commit_compare.sh`로 **자동 확인**하고, 팝업 사용 중에는60초 간격으로 다시 확인합니다. Git Pull 버튼은 **최신이면 초록색·차이가 있으면 빨간색·확인 중/미확인이면 노란색**이며, 확인이 성공하고 커밋이 다를 때만 누를 수 있습니다. Pi 업데이트 버튼도 **C4의 배포 패키지와 Pi 설치 버전**을 별도로 비교해 같은 색상 규칙을 사용하고, 차이가 확인된 경우에만 활성화합니다. 확인 실패나 버전 정보가 없는 상태에서는 업데이트 버튼을 활성화하지 않습니다.

알림 줄 **왼쪽에는 C4 현재/원격 커밋**, **오른쪽에는 Pi 버전**, 중앙에는 진행 상태를 표시합니다. Pi 버전은 UTC `YYYYMMDDHHMMSS`이며 구버전 설치처럼 공개 버전 기록이 없으면 설치 소스의 짧은 해시를 표시합니다. Git Pull은 C4의 `scripts/gitpull.sh`를 실행하며, 패널에는 **tmux 업데이트 시작**으로 표시합니다. 이는 업데이트 완료 표시가 아니며 C4는 기존 스크립트 절차에 따라 업데이트 후 재시작합니다. C4가 다시 연결되면 **Pi 업데이트**로 해당 C4 버전의 수신기를 배포합니다.

**Pi 업데이트**는 현재 C4에 설치된 저장소의 `scripts/pi_update.sh`를 실행합니다. 변경 파일 배포와 확인이 성공하면 자동 재부팅하고 다시 연결됩니다. 동일 버전이면 최신 상태를 표시하고 재부팅하지 않습니다. **로그 전송**은 대시보드의 Cluster 로그 업로드와 동일한 `scripts/log_upload.sh /data/log/cluster_debug.log`를 사용해 C4 로그와 존재하는 crash 원본, Pi 로그를 NAS로 전송합니다. crash·Pi 파일의 일부 수집·전송에 실패하면 부분 완료로 표시하고 보관·재시도 정책을 사용합니다. 패널 작업은 한 번에 하나씩 실행하며 중복 터치나 HTTP 응답 유실로 같은 작업을 두 번 시작하지 않습니다.

Pi 로그 수집·NAS 업로드는 **Cluster 활성화 + Network 디스플레이 선택**일 때만 수행합니다. Cluster가 꺼져 있거나 USB 모드이면 C4 로그만 전송하며, 보관된 Pi 로그 재전송도 건너뜁니다. 기존 보관 파일은 유지하고 다음 Network Cluster 활성 상태의 업로드에서 다시 전송합니다.

요청은 현재 연결된 C4의 대시보드(7000번 포트)로 보내며, 새 작업은 현재 C4 클러스터에 연결된 Pi에서만 허용합니다. Pi 배포 중 영상 연결이 끊겨도 접수된 작업 상태는 계속 조회할 수 있습니다. 상태 수집과 HTTP 요청은 별도 스레드에서 수행하고, 패널이 닫혀 있고 진행 중인 작업이 없으면 요청을 멈춥니다. Pi에 추가 Python 패키지는 필요 없습니다.

처음 이 기능을 적용할 때는 **C4 코드 업데이트·대시보드 재시작(또는 C4 재부팅) 후 `bash scripts/pi_update.sh`를 한 번 실행**합니다. 이후에는 터치 패널에서 사용할 수 있습니다. Pi 업데이트는 C4 저장소를 새로 내려받는 작업이 아니므로, 새 버전 배포 전 C4 코드를 먼저 갱신합니다.

## 연결 구조

```text
안드로이드 미러링폰 핫스팟
├── C4: TCP 0.0.0.0:9200에서 대기
│   └── H.264: 기존 도로 카메라 영상을 WebRTC로 전송, TCP로 접속 협상·HUD·경로 상태 전송
└── Orange Pi 3W: wlan0 대역에서 C4 검색
    ├── HDMI: 480x1920 모드에 회전한 영상 출력
    └── USB: 모니터 터치 컨트롤러 입력
```

대시보드에서 `Network (Orange Pi HDMI)`를 선택한 뒤 cluster를 켭니다. 저장된 `ClusterDisplayTransport`가 구성 기본값보다 우선합니다. `USB (TURZX Display)` 사용 중 Chestnut eGPU가 감지되면 `ClusterEnable`이 꺼지고 USB cluster가 중지됩니다. 부트로더 및 GPU 로딩 상태도 포함하며, eGPU를 분리해도 자동으로 다시 켜지지 않습니다. Network 모드는 eGPU 연결 여부와 관계없이 사용할 수 있습니다.

### H.264 전용 실행

대시보드의 **Toggles → Cluster Enable → Network (Orange Pi HDMI)**를 선택합니다. USB와 Network 모두 H.264 전용이므로 코덱 선택은 없습니다. 출력 방식을 바꾸면 Cluster가 정리 후 재시작합니다. 제거된 코덱 파라미터 파일은 새 Params 빌드 이후 manager 시작 시 미등록 키 정리로 삭제됩니다.

`cluster_run.py`가 스케줄링·스레드 제한·오류 로그를 준비하고 `cluster/main.py`가 선택한 출력만 불러옵니다. Network는 기존 C4 하드웨어 livestream 영상을 재사용하며 Pi가 카메라·HUD·경로를 합성합니다. USB는 C4에서 전체 화면을 합성해 별도 하드웨어 인코더로 보냅니다. Pi 디코더는 `aiortc_pyav_software`입니다. 카메라·모델 약20Hz에 맞춘 안정적인 표시가 목표이며 60FPS 영상 출력을 뜻하지 않습니다.

C4 코드를 업데이트하고 정상 빌드·재시작한 뒤 **C4에서** 다음 명령으로 Pi 수신기·공유 렌더러·폰트·아이콘도 갱신합니다. 터치 패널의 Pi 업데이트 버튼도 같은 작업을 실행합니다. 성공하면 Pi가 약5초 후 재부팅됩니다.

```bash
bash scripts/pi_update.sh
```

`install.sh`와 `pi_update.sh`는 OS의 `python3-aiortc`, `python3-av`, `python3-numpy`, `python3-pil`, `python3-opencv`를 필수로 확인·준비합니다. 업데이트는 **실행 중인 수신기를 중지하기 전에** 이를 검사하며, 준비에 실패하면 교체를 취소하고 기존 수신기를 유지합니다. 파일 교체 후 기동 실패 시 기존 백업·복구 절차를 사용합니다. 사용하지 않는 이전 수신 모듈은 적용 과정에서 제거하며 복구용 백업에는 보존합니다.

화면을 끄면 Pi 합성·표시는 쉬고 C4 상태 전송은 최대5Hz로 줄지만 **WebRTC 수신·디코딩·RGB 변환은 계속합니다.** 다시 터치하면 최신 수신 영상으로 표시를 재개합니다.

### 프레임과 안정성 로그

같은 주행의 C4·Pi 로그를 함께 확인하고 연결 직후·화면 OFF 구간을 구분합니다. 10월17일 사용자가 폰트·아이콘·팬·터치 업데이트·로그 전송 동작을 확인했습니다. 이후에는 실제 표시 FPS와 순간 중단, C4 주행 입력 상태를 확인합니다.

| 로그 | 확인할 항목 |
| --- | --- |
| C4 `[CLUSTER_H264_PERF]` | `video_fps`, `state_fps`, 소스 영상 age, 처리량, RTCP 손실·지터 |
| C4 `[CLUSTER_H264_SENDER]`, `[CLUSTER_H264_SEND_PERF]` | `api=track_send`, `framing=validated_annexb4`, 촬영 EOF 기준 RTP 시각, 실제 제출 FPS·잘못된 프레임·IDR 대기·`send_not_ready` 수 |
| C4 `[CLUSTER_H264_SEND_BACKPRESSURE]`, `[CLUSTER_H264_SEND_RECOVERED]`, `[CLUSTER_H264_ERROR]`; Pi `[CLUSTER_H264_STOP]` | 일시적 송신 실패·회복, 종료한 제어/영상 단계, 수신 종료 이유와 마지막 영상·상태 age |
| Pi `[CLUSTER_H264_RX_PERF]`, C4 `[CLUSTER_H264_PI_PERF]` | `display_fps`, RGB 변환·합성·출력 시간, 큐 대기·최대 표시 간격, 영상·상태 stale |
| C4 `[CLUSTER_RESOURCE_PERF]`, `[CLUSTER_LOAD_GUARD]`, `[CLUSTER_INPUT_DIAG]` | CPU·RSS, 모델·위치 입력 age, 통신 경고, 부하 보호와 복구 |
| Pi `[CLUSTER_PI_RESOURCE_PERF]` | CPU·RSS·스레드·온도·주파수·팬 PWM |

각 단계는 겹쳐 실행되므로 소요 시간을 단순 합산해 FPS를 계산하지 않습니다. C4 소스 age와 Pi 수신 후 age는 각 기기의 로컬 시계 기준이며 촬영부터 HDMI 표시까지의 종단 지연 측정값이 아닙니다.

### 2026-10-17 H.264 프레임·복구 보완

`14_07_40-5Qdyxqa6` 주행 로그의 정상 연결 구간에서 C4 송신 평균은 약19.95FPS, Pi 디코딩 수신은 약19.10FPS, 실제 표시는 약15.15FPS였습니다. 모델·경로 표시가 활성화된 뒤 Pi 합성 시간은 평균 약26ms, 화면 출력은 약15ms였습니다. 시작 구간을 제외한 최대 표시 간격은 약435ms였고, 일부 구간은 RTP 손실·디코더 경고와 겹쳤습니다. C4의 모델 지연·통신 경고는 이 로그에서 발생하지 않았습니다. locationd 입력 오류는 시작 시 약1.9초 동안 별도로 기록됐으므로 모든 끊김을 단일 원인으로 단정하지 않습니다.

이번 보완은 해상도·영상 비트레이트를 유지하면서 다음 처리를 줄입니다.

- Pi 합성·출력이 50ms 예산을 조금 넘었을 때 다음 50ms 슬롯까지 추가로 쉬던 주기 계산을 수정합니다. 한 번에 한 프레임을 처리하며 준비된 최신 디코딩 영상을 사용합니다.
- 앞차 거리·속도 글자를 기존 크기·색상·테두리 그대로 캐시하고, 수신 RGB 영상의 중복 복사를 줄입니다. HDMI 출력용 pygame Surface도 소유한 바이트 버퍼를 공유해 추가 복사를 줄입니다. [pygame 버퍼 API](https://www.pygame.org/docs/ref/image.html#pygame.image.frombuffer)
- RGB 변환은 별도 작업 스레드에서 순서대로 하나씩 처리해 WebRTC·상태 수신 루프를 덜 막습니다. 압축 참조 프레임을 디코딩 전에 버리는 방식은 사용하지 않습니다.
- Pi 디코더 오류를 감지하면 정기 1초 보고를 기다리지 않고 IDR을 요청합니다. 오류 묶음의 요청은 0.5초 간격으로 제한하며, C4는 새 IDR이 준비되는 동안 정상 소스 영상 전송을 유지합니다. 실제 C4 소스 누락·노후화 시의 참조 프레임 대기는 유지합니다.

Pi의 10초 `[CLUSTER_H264_RX_PERF]` 로그에는 `scene_camera_panel_avg`, `scene_path_avg`, `scene_hud_avg`와 `hdmi_rgb_surface_avg`, `hdmi_upload_avg`, `hdmi_draw_avg`, `hdmi_flip_avg` 등 합성·출력 단계 시간이 추가됩니다. 소프트웨어 출력에서는 해당하지 않는 GPU 단계가 생략됩니다. 새 `[CLUSTER_PI_RESOURCE_PERF]`에는 수신기 프로세스 CPU·RSS·스레드 수, CPU 온도·현재 주파수 범위, 팬 PWM·OpenCV 스레드 수를 기록합니다. 여기서 CPU 100%는 코어 하나를 모두 사용하는 기준으로, 터치 패널의 Pi 전체 CPU 비율과 기준이 다릅니다. 읽을 수 없는 값은 `n/a`입니다. 이 기록은 기존 온도·팬·CPU 주파수 정책을 변경하지 않습니다.

표시 주기·복구 동작과 화면 픽셀 보존은 자동 테스트로 확인했으며, 개선 후 실제 Pi FPS와 순간 중단은 다음 C4·Pi 로그로 확인해야 합니다.

### 2026-10-18 전송 경계·Pi 작업 스레드 보완

`20_31_28-338425xl`의 C4 송신은 시작 이후 약20FPS를 유지했습니다. Pi는 원본을
약19~20FPS로 받고도 경로 합성이 활성화되면 표시는 약17FPS, 합성은 약27~30ms로
느려졌습니다. 시작 이후에도 최대 표시 간격이 약300ms인 구간이 있고 디코더 오류가
반복됐습니다. Pi의 `opencv_threads=8`, 프로세스 CPU 약180~197%(한 코어100% 기준),
실제 경로 처리의 CPU 시간보다 긴 경과 시간이 함께 관측됐습니다.

첨부 사진은 PID12312가 `teleoprtc/stream.py`의 `send_frame(..., FrameInfo(...))` 호출
지점에서 SIGSEGV로 종료된 기록입니다. 첨부 C4 로그는 PID16960으로 사진과 다른 세션이며,
해당 종료의 네이티브 backtrace는 들어 있지 않습니다. 따라서 사진만으로 원인을
특정하거나 모든 깜빡임이 같은 오류라고 판단하지 않습니다.

- 클러스터 전용 송신 클래스에서 Annex B의3/4바이트 시작 코드를 검사하고4바이트로
  통일합니다. 빈 NAL·잘못된 헤더·크기/개수 초과 입력은 네이티브로 전달하지 않고 IDR을
  요청합니다. 이때 누락된 참조에 의존하는 P 프레임은 다음 IDR까지 보류합니다. 정상
  소스의 수신측 디코더 오류 복구에서는 기존처럼 전송을 계속합니다.
- RTP 시각은 전송 루프가 깨어난 시각 대신 카메라 EOF 간격을 사용합니다. 네이티브
  `FrameInfo` 호출을 우회해 RTP 설정의 시각을 명시하고 `Track.send()`로 기존 H.264
  packetizer·SR·PLI·NACK 체인을 사용합니다. 카메라 대기 후 연결 상태도 다시 확인합니다.
  [사용 중인 바인딩의 전송 API](https://github.com/shiguredo/libdatachannel-py/blob/2026.1.0.dev2/src/bind_libdatachannel.cpp),
  [네이티브 Track 구현](https://github.com/paullouisageneau/libdatachannel/blob/v0.24.0/src/track.cpp)
- 연결 종료 시 송신 작업·네이티브 콜백을 먼저 중지하고 그 뒤 카메라 소켓을 해제합니다.
  미디어 체인의 Python 래퍼도 스트림 종료까지 유지합니다.
- Pi 수신기는 합성을 시작하기 전에 OpenCV 작업 스레드를1개로 제한하고 OpenCL을
  끕니다. NumPy/OpenCV를 불러오기 전에 BLAS/OpenMP 작업 수도1개로 제한하므로 기존
  서비스 설정을 유지한 Pi Update에도 적용됩니다. WebRTC 디코딩과 RGB 변환
  작업은 유지하며 영상 해상도·목표 FPS·비트레이트는 이 변경에서 조절하지 않습니다.

C4 업데이트 후 **Pi Update도 실행**해야 Pi의 스레드 제한과 서비스 설정이 적용됩니다.
다음 기기 로그에서 `[CLUSTER_H264_SENDER] api=track_send`,
`[CLUSTER_RX_NATIVE] opencv_threads=1 | opencl=0`을 확인합니다. Python 예외와 입력
거부·연결 종료 경합을 자동 검증하고, 실제 IDR/P 프레임12장의 정규화 전후 디코딩 픽셀이
동일한 것도 확인했습니다. SIGSEGV 재발 방지와 실제 표시 FPS 개선 여부는 C4·Pi 기기
로그로 확인해야 합니다.

### 2026-10-19 크래시·연결 복구와 Pi 세션 정리

C4에서 14:15:17(PID6830), 15:06:44(PID4595)에 SIGSEGV 종료가 기록됐습니다.
첫 번째는 `Params.put → request_keyframe → request_recovery → _feedback` 스택이며,
두 번째는 traceback 자체가 잘려 같은 원인인지 판단할 수 없습니다. 네이티브 PLI 콜백과
asyncio 루프가 같은 Params 객체에 동시에 비동기 쓰기를 요청할 수 있던 경로를 수정해,
키프레임 요청·IDR 확인·종료 처리를 소유한 루프에서 순서대로 실행합니다. 반복 PLI는
합치고, 종료 후 예약된 콜백은 무시하며, IDR 하나의 확인을 중복 기록하지 않습니다.

별도로 `Native WebRTC track rejected the H.264 frame` 직후 연결이 닫힌 경우가 8회
있었습니다. 열려 있는 영상 트랙의 `send(False)` 한 번을 세션 종료로 처리하던 동작을
수정했습니다. 이제 일시적 실패에는 연결과 정상 소스 전송을 유지하고 IDR 요청은 초당
1회로 제한합니다. 이때 추가한 연속2초 실패 재접속 조건은 10월21일 로그 확인 후 아래와
같이 제거했습니다. 실제 트랙 종료·네이티브 예외에는 기존 재접속 경로를 사용합니다.
`False`는 마지막 RTP 전송 결과이며 그 자체로 ICE 연결
종료를 의미하지 않습니다. [사용 중인 네이티브 전송 구현](https://github.com/paullouisageneau/libdatachannel/blob/v0.24.0/src/impl/track.cpp)

주행 중 Pi는 영상을 약19FPS로 받아도 실제 표시는 평균 약16~17FPS였고, 일부 재접속 뒤
RSS가 약80MB씩 늘었습니다. 오류 traceback이 종료된 세션을 붙잡아 큰 폰트·렌더러·영상
버퍼의 해제가 지연되는 경로를 자동 테스트로 재현했습니다. 이제 세션 종료 시 이 자원을
즉시 해제하고, 종료한 asyncio 루프·작업 참조도 정리합니다. 실제 장비에서의 메모리 증가가
모두 같은 원인인지는 다음 로그에서 확인합니다.

수신 작업이 마지막 오류 검사 직후 종료되면 오류가 정상 반환처럼 처리되던 경합도
보완했습니다. `[CLUSTER_H264_STOP]`에는 종료 이유, 수신 프레임 수, 디코더 오류 수,
마지막 영상·상태 수신 이후 시간이 남고, 제어 패킷 시간 초과에도 구체적인 메시지를
기록합니다. 이 Pi 변경은 C4 업데이트 후 **Pi Update**를 실행해야 적용됩니다.

C4 영상 송신은 약20FPS였지만 Pi 표시는 약16~17FPS로, 연결 복구와 합성 처리 시간은
구분해서 확인해야 합니다. 모델 지연·통신 경고도 일부 실제 기록됐으며, 해당 로그만으로
클러스터가 원인이라고 확정하지 않습니다. 다음 주행에서 크래시·재접속 횟수, Pi RSS와
표시 FPS를 함께 확인합니다.

### 2026-10-20 상태 전송 지연과 디코딩 후 대기열 보완

오늘 C4 로그는 09:03:20부터 동일 PID20262가 유지됐고 crash 파일에는 시작 기록만
있었습니다. 대신 `phase=scene_state | TimeoutError`가 161회 발생했습니다. 기존 상태
전송은 TCP `drain()`이 250ms 안에 끝나지 않으면 WebRTC 영상까지 종료해 Pi의
`Connection reset by peer`와 반복 재접속을 만들었습니다. C4 송신 평균은 약19.99FPS,
Pi의 파일별 표시 평균은 약14.3~16.5FPS였습니다.

- 250ms는 지연 진단 기준으로 사용하고, 같은 TCP 쓰기를 기다리는 동안 영상 연결을
  유지합니다. 다음 상태를 계속 만들거나 쌓지 않으며, 복구 후에는 최신 모델·차량
  상태를 다시 읽습니다. 상태 쓰기가 연속5초 이상 막히거나 소켓 오류가 나면 종료합니다.
- asyncio에는 부분 전송 중인 패킷 하나만 남기고 TCP 송신 버퍼도 작게 제한합니다.
  연결 종료 시 정해진 시간 안에 flush되지 않는 transport도 해제해 소켓을 남기지 않습니다.
- Pi는 디코딩이 끝난 영상의 대기 슬롯을 한 장으로 제한합니다. RGB 변환 하나가
  진행되는 동안 들어온 디코딩 영상은 최신 영상만 다음 변환에 사용합니다. H.264
  디코더와 참조 프레임 처리는 계속 실행합니다.

`[CLUSTER_H264_STATE_BACKPRESSURE]`와 `[CLUSTER_H264_STATE_RECOVERED]`로 상태 전송
지연·회복을 구분합니다. Pi와 C4의 Pi 성능 로그에는 `decoded_fps`, `skipped_rgb`를
추가했습니다. `rx_fps`/`video_fps`는 RGB 변환 완료 기준이고 `decoded_fps`는 변환 전
디코딩 영상 기준입니다. `skipped_rgb`는 Pi 10초 보고에서는 구간 값, C4의 Pi 피드백에서는
현재 연결의 누계입니다. 영상 age와 큐 대기 시간에는 이제 RGB 변환 시간도 포함합니다.

짧은 상태 전송 중단 중에도 같은 영상 연결을 유지하고 최신 상태로 복구하는 동작,
장기 중단·취소 시 작업 정리, 디코딩 영상 묶음에서 RGB 변환 대기를 줄이는 동작을
자동 테스트로 확인했습니다. 실제 표시 FPS와 끊김 개선은 C4 업데이트와 **Pi Update**
적용 후의 기기 로그에서 확인해야 합니다.

### 2026-10-21 수신 중 불필요한 재접속과 로그 업로드 보완

오전08:32~09:14, 오후14:37~15:44 로그에서 각각10회·11회 연결 종료가 연속2초
`send(False)` 판정으로 발생했습니다. 예를 들어15:04:59에 C4가 연결을 끊었지만 Pi의
후속 종료 기록에서 마지막 영상 age는19.1ms였습니다. 이제 이 반환값만으로 연결을
종료하지 않고 전송·제한된 IDR 요청을 유지합니다. 실제 네이티브 연결 종료·예외,
C4 제어 피드백8초 시간 초과, Pi의 영상·상태8초 수신 정체 감시는 유지합니다.
첫 영상이 아직 없을 때는 기존15초 시작 제한을 적용합니다.

저녁18:10~18:36에는 재접속이 없었지만 실제 표시는 평균 약16.2FPS였습니다. Pi 합성은
평균 약35.5ms, 출력은 약8ms였으므로 연결 안정성과 합성 성능은 구분해 확인해야 합니다.
해상도·비트레이트·화질은 변경하지 않았고 실제 표시 FPS 개선은 다음 주행으로 확인합니다.
이번 전송·업로드 수정은 C4 코드에 적용되며 Pi 실행 코드는 변경하지 않았습니다.

오늘 crash 파일은 시작·정상 종료 기록만 담고 있었습니다. 이제 빈 파일과 완전한 정상
시작·종료 기록만 있는 파일은 업로드하지 않습니다. 실제 Python 오류·네이티브 스택,
일부만 남은 기록이나 알 수 없는 데이터는 원본 그대로 전송합니다. 이미 보관한 미전송
사본에도 같은 기준을 적용하며, 실행 중인 원본 파일을 지우거나 자르지는 않습니다.

`tmux_error.log` 자동 전송은 manager에서60초마다 재시도합니다. 내용 SHA256으로 성공
여부를 확인하고, 발생 시각·내용 해시를 NAS 파일명에 사용합니다. 성공 확인 파일이
유실되거나 FTP 응답이 끊겨 재시도해도 같은 파일명으로 전송하며 새 사본을 계속 만들지
않습니다. 일반 manager 재시작은 최상위 로그 파일을 초기화하고 미전송 보관 폴더는
유지합니다. 아래 Git Pull 전체 정리는 이 보관 폴더도 초기화하며 기존 NAS 파일은 삭제하지 않습니다.
저녁 로그의7월 시작 날짜는 부팅 직후 시계 보정 전 기록이므로 uptime·PID도 함께 봅니다.

### 2026-10-22 C4 표시 데이터 처리와 Pi 중복 합성 보완

14:19 업로드는14:38 로그의 앞부분이므로 중복을 제외하고3개 세션을 비교했습니다.
모두 연결1회·재접속0회·cluster 오류0회이며 C4 영상은 평균20FPS를 유지했습니다.
Pi RGB 완료는 평균19.1~19.3FPS, 합성 화면 표시는16.4~17.1FPS였습니다.
상태 전송의 짧은 TCP 대기는 합계500회 복구했고 영상 연결을 종료하지 않았습니다.

14:18:41부터 calibration 관련 입력 invalid와 통신 경고가 있었고,
14:18:47~14:19:00에는 `modeldLagging`과 최대5.4% 모델 프레임 손실이 기록됐습니다.
modeld·locationd·selfdrived PID는 유지됐고 모델 실행시간은 약40ms였습니다.
부하 보호는 경고 전에 이미 동작해 상태 전송을5Hz로 낮췄고 cluster CPU는 평소 약42%에서
33.5%로 줄었습니다. 모델 프레임 손실은 카메라 입력 frame ID 누락의 지표이므로
실행시간만으로 스케줄링·입력 지연을 배제할 수 없으며, 이 로그만으로 cluster를
직접 원인으로 확정하지 않습니다.

- Network에서는 입력 수신과 오류 감시는 기존 주기를 유지하고, 표시용 HUD·경로 값은
  상태를 전송할 때 최신 입력에서 만듭니다. 메시지를 쌓지 않고 변경된 서비스만 기억하며
  일회성 상태 변화도 보존합니다. USB는 기존 즉시 갱신 방식을 유지합니다.
- C4의 장면 geometry·수치·깊이 검증을1회로 합치고 float 좌표 반올림의 반복 타입 검사를
  줄입니다. 수치 정밀도·64KiB 제한·전송 형식·Pi 수신 검증은 유지합니다.
- Pi는 새 RGB 영상에 최신 HUD를 합쳐 표시합니다. 메타데이터가 먼저 도착했다고 이전
  카메라 화면을 다시 합성해 다음 영상 표시 시간을 차지하지 않습니다. 영상이 멈춰도
  HUD는100ms 주기로 갱신하며, 데이터 만료 전환은 별도로 표시 대상으로 만듭니다.

새 `camera_display_fps`는 새 RGB가 포함된 유효 카메라 화면의 실제 표시율입니다.
`display_fps`는 HUD만 바뀐 화면도 포함하므로 두 값을 구분합니다. 화면 OFF·미표시·
만료된 카메라 영상은 `camera_display_fps`에 포함하지 않습니다. C4의
`CLUSTER_H264_PI_PERF`와 Pi의 `CLUSTER_H264_RX_PERF`에 모두 기록합니다.

**C4 업데이트 후 Pi Update도 실행**합니다. 해상도·영상 비트레이트·경로 투영과
주행 프로세스의 경고 기준은 바꾸지 않았습니다. 실제 CPU 감소·새 카메라 표시 FPS와
모델 경고 재발 여부는 다음 주행 로그로 확인합니다. 표시 값 계산이 수집 스레드에서
상태 생성 경로로 이동하므로 `state_cpu_avg`만으로 부하 증감을 판단하지 않고 전체
`CLUSTER_RESOURCE_PERF cpu_pct`와 함께 비교합니다.

### 2026-10-23 Pi CPU 병렬 처리와 GPU 카메라 합성

10/23 업로드 7쌍은 누적 부분을 제외하면 3개 주행 세션입니다. C4 영상은 평균20FPS,
Pi 디코딩은17.7~18.2FPS, 전체 화면 표시는13.7~14.6FPS, 새 카메라 영상 표시는
11.1~11.9FPS였습니다. C4 cluster CPU는 전날 약41~42%에서 약37%로 줄었고
이번에는 modeldLagging·commIssue가 기록되지 않았습니다. Pi는 카메라 변환17~19ms,
경로 약15ms, HUD 약7ms, 출력 약8ms로 영상1장당50ms 예산에 근접했습니다.
Pi의 CPU 약132%는 전체가 아닌 코어1.3개 분량이며, 터치 패널의 전체 평균20%와
기준이 다릅니다. 최고 CPU 온도는69.7도였습니다.

- Pi의 SDL GPU에서 원본 센서 비율의 소수점 UV로 카메라를 잘라 확대합니다.
  CPU는 카메라 대비 LUT를 적용하고, 작업1개를 경로·HUD 준비와 병렬 실행합니다.
  프레임마다 이 작업을 완료한 뒤 표시하므로 작업 큐나 지연 영상이 누적되지 않습니다.
- CPU 경로·차선 raster는 기존 OpenCV strip 방식으로 색상과 coverage를 함께 만들고,
  Pi GPU가 카메라 → 경로 → HUD → 터치 메뉴 순서로 합성합니다. 경로의 알파를 RGB에
  미리 곱한 레이어와 일반 HUD 레이어를 구분해 색이 겹쳐도 올바르게 표시합니다.
  카메라의 확대·잘라내기는 CPU에서 반복하지 않으며 회전·밝기도 GPU에서 처리합니다.
- SDL의 geometry API나 blend mode를 지원하지 않거나 업로드에 실패하면 그 프레임부터
  CPU 장면 합성으로 전환합니다. 기존 전체 화면 GPU 표시는 가능하면 유지하고,
  그것도 실패하면 surface 방식으로 전환합니다. 영상 연결을 재시작하지 않습니다.
- OpenCV 기본 작업 스레드는 가용 CPU가4개 이상이면2개, 그보다 적으면1개입니다.
  `CLUSTER_OPENCV_THREADS`로1~4개를 지정할 수 있으며 가용 코어 수로 제한합니다.
  BLAS/OMP는1개를 유지하고 주행 프로세스 우선순위·CPU affinity는 변경하지 않습니다.
- IDR을 받은 직후에도 복구 요청의 최소1초 간격을 유지합니다. 그 사이의 새 복구 요청은
  보관했다가 후속 프레임에서 재시도하고 정상 P 프레임은 계속 전송합니다.
  native `send=False`만으로 IDR을 반복 요청하던 동작은 제거했습니다.

디코더의 `Invalid data` 오류와 RTP 순간 손실도 반복됐습니다. GPU 가속만으로
압축 데이터 손실이 복구되지는 않으므로 aiortc 내부 버퍼를 추측으로 바꾸지는 않습니다.
`CLUSTER_H264_SEND_PERF`에10초 구간의 실제 최대 access unit/IDR 크기와 IDR 횟수,
복구 요청·병합·지연 횟수를 추가했습니다. `CLUSTER_PI_RESOURCE_PERF`에는 전체 평균
`system_cpu_pct`와 코어별 `cpu_cores_pct`가 추가됩니다. `cpu_pct`의100%는 계속 코어1개입니다.
`gpu_camera=1`이면 새 GPU 카메라 합성이 실제 사용 중이며, `metadata_display_fps`는
HUD만 갱신한 표시율, `hidden_camera_fps`는 새 RGB를 받았지만 카메라가 만료된 표시율입니다.

**C4 업데이트 후 Pi Update도 실행**합니다. 기존 해상도·비트레이트·폰트·아이콘을 유지합니다.
데스크톱의 실제 SDL 합성 테스트에서 소수점 crop·회전·알파·밝기를 확인했지만 Pi 실측은
별도입니다. 다음 로그의 `camera_display_fps`, render+display 시간, 최대 표시 간격과
디코더 오류를 함께 비교합니다. GPU 업로드 비용도 포함해 판단하며 FPS 상승을 보장하지 않습니다.
[SDL geometry](https://wiki.libsdl.org/SDL2/SDL_RenderGeometry),
[SDL blend mode 지원](https://wiki.libsdl.org/SDL2/SDL_ComposeCustomBlendMode)

### 2026-10-23 19:53 업데이트 후 로그 보완

이번 로그의 업데이트 전후 표본에서 Pi 수신은 평균18.6~18.7FPS로 비슷했고,
새 카메라 표시는12.9→13.6FPS, 합성 시간은27.2→16.9ms로 개선됐습니다.
후반에는 합성 약13ms, 카메라 준비 약3ms이며 `gpu_camera=1`로 GPU 적용이 확인됩니다.
업데이트 뒤 수신기 재시작은0회입니다. RTP 손실·디코딩 오류는 남아 있고,
짧은 가동 로그이므로 장시간 주행 성능이나 경고 재발 여부를 확정하지 않습니다.

- GPU 표시에서는 새 RGB를 받는 시점에 맞춰 처리하고 순간 표시율은60Hz로 제한합니다.
  원본 카메라의20Hz나 C4 송신량은 늘리지 않습니다. Wi-Fi 지연 뒤 영상이 몰려올 때
  기존50ms 타이머 때문에 다음 RGB로 덮이는 구간을 줄입니다. CPU fallback의 기존
  표시 주기는 유지하고 영상은 계속 디코딩한 후 최신 RGB만 보관합니다.
- GPU에서 짧은 카메라 수신 공백은200ms까지 기다린 뒤 HUD만 갱신합니다. 이후 HUD는
 100ms 간격을 유지하며 데이터 만료는 이 대기와 별도로 즉시 처리합니다.
- GPU 통계 조회의 `a password is required`가37회 반복됐습니다. Pi Update 성공 후
  실제 서비스 계정에 `/usr/bin/head -c 4097 -- /sys/kernel/debug/pvr/status`만 허용하는
  전용 sudoers 파일을 `visudo` 검사 뒤 원자적으로 설치합니다. 다른 명령이나 파일의
  권한은 부여하지 않으며 기존 서비스 계정과 사용자 설정은 유지합니다.
  통계 설정 실패는 이미 성공한 수신기 업데이트를 취소하지 않습니다.
- GPU 통계를 읽지 못하면60초 뒤 재시도해 매초 sudo 오류와 프로세스를 반복하지 않습니다.

**C4 업데이트 후 Pi Update를 실행**합니다. 새 표시 정책은
`CLUSTER_H264_PRESENT_POLICY video=decoded_arrival`로 확인합니다. 실제 FPS 상승 폭은
다음 로그의 `camera_display_fps`·수신 FPS·표시 간격·디코딩 오류로 판단합니다.

### 2026-10-24 Pi 경로·HUD 병렬 처리

OpenCV의 작업 스레드 2개는 Pi 프로세스 전체를 2코어로 제한하는 설정이 아닙니다.
디코딩·RGB 변환·카메라 준비·화면 표시가 별도로 진행되며, 이번에는 GPU용 레이어를
만드는 과정에서 카메라 준비·경로 그리기·HUD 그리기를 동시에 진행합니다.
가용 CPU가 4개 이상이면 경로 전용 작업 스레드 1개를 추가합니다. OpenCV는 기본
2개를 유지해 여러 작업이 동시에 실행될 때 과도한 내부 스레드 경쟁을 피합니다.
Pi GPU는 카메라 확대·회전·밝기 및 카메라/경로/HUD 합성을 계속 담당합니다.

- 경로와 HUD는 서로 다른 픽셀 버퍼를 사용합니다. 공유 글꼴·텍스트 캐시는 잠금으로
  보호하고, 경로·차선·선행차 필터는 경로 작업만 갱신합니다.
- 프레임마다 카메라와 경로 작업을 완료한 뒤 표시합니다. 오류가 발생해도 작업을
  먼저 회수하며 연결 종료 시 스레드를 정리합니다. 대기 프레임 큐를 추가하지 않습니다.
- 경로 데이터가 없으면 큰 투명 레이어의 초기화·알파 검사를 생략합니다.
  오래된 모델·영상은 기존 만료 기준에 따라 숨기며 이전 경로를 재사용하지 않습니다.
- 시작 로그 `CLUSTER_SCENE_WORKERS path_workers=1`로 병렬 처리 설정을 확인합니다.
  `CLUSTER_H264_RX_PERF`의 `scene_path_wait_avg`는 HUD 완료 후 경로를 더 기다린
  시간입니다. 경로와 HUD 시간이 겹치므로 두 항목을 더해 전체 합성 시간으로 보지 않습니다.
  비교 진단이 필요하면 서비스 환경변수 `CLUSTER_PARALLEL_PATH=0`으로 직렬 처리합니다.

데스크톱에서 같은 차선·선행차 장면을 교대로 실행한 80개 표본의 합성 중앙값은
직렬 4.40ms → 병렬 3.85ms였습니다(OpenCV 2스레드, 입력 1280×720, 출력 1920×480,
20회 준비 후 측정). 이는 Pi 실측 FPS가 아니며 GPU 출력 시간도 포함하지 않습니다.
**C4 업데이트 후 Pi Update를 실행**하고 다음 로그의 새 카메라 표시율·전체 합성 시간·
표시 간격·코어별 사용률을 비교합니다. 영상 손실과 디코딩 오류는 별도로 확인합니다.

### 터치 팝업의 CPU·GPU 상태

터치 팝업의 상태 행에는 **CPU 사용률 / CPU 온도 / GPU 사용률 / GPU 온도 / FAN / FPS**를
표시합니다. CPU·GPU 온도는 각각의 센서에서 읽고 동일한 온도별 색상으로 구분합니다.
PowerVR GPU 사용률은 `/sys/kernel/debug/pvr/status`의 GPU 전체 사용률을 읽으며,
GPU devfreq가 제공하는 사용률도 지원합니다. root 전용 debugfs는 Pi Update가 설정한
전용 읽기 명령으로 조회하고0.5초 안에 응답하지 않으면 중단합니다. 실패 시60초 뒤
재시도합니다. 서비스 초기 설정과 재설치에서도 동일한 읽기 권한을 준비합니다.
권한이나 센서가 없으면 `--`로 표시하고 CPU 온도나 GPU 클럭으로 대체하지 않습니다.
표본 수집은 팝업이 열려 있을 때 기존 작업 스레드에서 약1초 간격으로 수행합니다.
[PowerVR 상태 항목](https://doc.kickpi.com/products/linux_customization/linux_core/)

### 대시보드에서 Orange Pi IP 확인

C4 대시보드의 **Toggles → Cluster Enable** 아래에 `Orange Pi 연결됨 · IP: ...`가 표시됩니다. 연결된 TCP 수신기의 실제 IPv4 주소를 1초마다 확인하므로 핫스팟에서 새 IP를 받아도 다시 연결되면 표시가 바뀝니다. 이 주소가 SSH 업데이트 대상이며 C4 주소와 다릅니다.

연결이 끊기면 `Orange Pi 연결 대기 중`으로 바뀝니다. C4 송신 프로세스가 비정상 종료해 상태를 지우지 못해도 마지막 갱신으로부터 10초가 지나면 연결된 것으로 표시하지 않습니다. Cluster가 꺼져 있거나 USB 모드이면 Network 모드를 켜라는 안내가 표시됩니다.

일부 핫스팟은 접속 장치 간 통신을 차단합니다. 같은 SSID에서도 연결되지 않으면 AP/client isolation을 확인합니다. TCP 접속 협상·상태 채널에는 별도 인증·암호화가 없으므로 차량 내부의 신뢰할 수 있는 핫스팟에서 사용합니다.

## 스크립트 목록

명령은 `scripts/`의 Bash 파일로 제공합니다. `bash`로 실행하므로 실행 권한 설정은 필요하지 않습니다. 파일은 LF 줄바꿈을 사용합니다. **`deploy.sh`는 C4에서**, 나머지 스크립트는 Orange Pi에서 실행합니다. PC에서는 MobaXterm 등 SSH 클라이언트로 접속하면 됩니다.

| 파일 | 용도 |
| --- | --- |
| [install.sh](scripts/install.sh) | OS pygame·필수 라이브러리 설치, `/opt/cluster-receiver`로 패키지 복사 |
| [prepare_h264.sh](scripts/prepare_h264.sh) | 필수 H.264 의존성 준비; 실패하면 기존 수신기 교체 취소 |
| [connect_wifi.sh](scripts/connect_wifi.sh) | 핫스팟 연결 및 IP 확인; 비밀번호는 대화형 입력 |
| [ensure_wifi.sh](scripts/ensure_wifi.sh) | 차량 `Android` 프로필 저장·재사용, 자동 연결·Wi-Fi 절전 해제 |
| [wifi_power_save.sh](scripts/wifi_power_save.sh) | 활성 Wi-Fi 프로필의 절전 설정만 변경하고 실제 인터페이스의 `off` 확인 |
| [wifi_boot_setup.sh](scripts/wifi_boot_setup.sh) | 수신기 시작·Wi-Fi 재연결 시 절전 해제 실행 설정 설치 |
| [fan_setup.sh](scripts/fan_setup.sh) | 부팅 시 팬 최저 출력 60% 설정, 기존 온도 제어·CPU 과열 보호 유지 |
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

H.264를 포함한 수동 배포본에는 공유 `cluster_renderer.py`·`cluster_render_support.py`와 `receiver_assets.txt`에 나열된 `assets/` 파일도 함께 있어야 합니다. 전체 저장소에서 설치하면 스크립트가 공유 파일을 찾아 복사합니다. C4의 `pi_update.sh`는 이 파일들을 자동으로 동봉하므로 기존 Pi 업데이트에 권장합니다.

```bash
sudo bash /opt/cluster-receiver/scripts/install.sh
```

Debian/Ubuntu의 OS `python3-pygame`을 설치하고 `/usr/bin/python3`로 실행합니다. 가상 환경은 필요하지 않습니다. 이미 수신기가 설치된 장비에는 Wi-Fi 절전 해제 실행 설정을 보완하며, 서비스 실행 계정·화면 옵션·데스크톱 부팅 대상은 유지합니다. 계기판·연결 대기 화면·터치 메뉴는 패키지에 포함된 `assets/fonts/NotoSansKR-Bold.ttf`를 공통으로 사용합니다. 기존 장비에도 새 폰트 자산이 포함되도록 수신기 패키지를 업데이트합니다.

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

밝기는 영상 색상을 어둡게 하는 방식이고, 화면 끄기는 검은 화면을 표시합니다. **HDMI 모니터의 물리 백라이트나 전원을 끄지는 않습니다.** 모니터가 검은 화면에서도 빛을 내면 모니터 자체 밝기 설정이 필요합니다. 화면이 꺼져 있는 동안 Pi 합성·출력은 쉬지만 WebRTC 참조 프레임 유지를 위해 영상 수신·디코딩은 계속합니다. 다시 터치하면 최신 수신 영상으로 표시를 재개합니다.

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

`pi_update.sh`의 일반 업데이트가 성공하면 **Orange Pi 기기 자체를 약 5초 뒤 자동 재부팅**합니다. 파일 적용·수신기 실행 확인·임시 파일 정리와 필요한 SSH 포트 변경이 끝난 뒤 Pi의 systemd 타이머로 재부팅을 예약합니다. 예약 결과를 SSH로 받은 후 접속을 종료하며, Pi 부팅이 끝나면 Wi-Fi와 HDMI 수신기가 자동 시작합니다. 업데이트 또는 재부팅 예약에 실패하면 오류를 표시합니다. 재부팅 예약 실패는 이미 성공한 파일 업데이트를 되돌리지 않습니다. `--dry-run`, `--help`, `--rollback`은 재부팅을 예약하지 않습니다. 패키지의 `deploy.sh`를 직접 사용할 때는 `--reboot`를 지정해야 같은 재부팅 동작을 사용합니다.

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

1. 수신기 Python 파일, Bash 스크립트, README, requirements, 서비스 템플릿을 LF와 SHA-256 체크섬으로 준비합니다. 폰트·아이콘은 원본에서 해시를 계산합니다. openpilot 전체와 `.venv`, 테스트, 캐시는 제외합니다.
2. Pi의 실제 설치 파일 해시와 비교해 **신규·변경 파일만 전송**합니다. 동일한 파일은 Pi 안에서 임시 패키지에 복사하고 전체 체크섬, Bash 문법, OS Python의 컴파일·pygame 및 수신기 모듈 가져오기를 검사합니다. 비교 후 설치 파일이 달라졌거나 검증이 실패하면 적용하지 않습니다. 구버전 Pi에도 현재 C4의 비교 도우미를 보내므로 별도 마이그레이션이 필요 없습니다.
3. 기존 수신기 파일을 `/var/lib/cluster-receiver/updates/<시각.식별자>/previous`에 백업하고 서비스를 잠시 중지해 변경 파일을 교체합니다. 동일 내용의 설치 파일은 다시 쓰지 않으며 전체 백업은 복구를 위해 유지합니다.
4. 실행 중이거나 실패 상태였던 서비스는 다시 시작합니다. 현재 systemd 실행의 `Cluster receiver ready` 로그와 동일한 실행이 유지되는지 최대 약 20초간 확인합니다. 화면 준비 확인에는 C4 연결이나 Wi-Fi IP 할당이 필요하지 않습니다. 명시적으로 중지되어 있던 서비스는 중지 상태를 유지합니다.
5. 적용 또는 실행 확인이 실패하면 이전 파일을 복구하고 이전에 실행 중이던 서비스를 다시 시작합니다. 성공 시 `Receiver apply complete`와 백업 위치가 출력됩니다.

적용·rollback 성공 시 `/var/lib/cluster-receiver/updates/version`에 **UTC `YYYYMMDDHHMMSS`와 패키지 체크섬**을 기록하고, 터치 UI가 읽을 수 있는 `/opt/cluster-receiver/receiver-version`에도 게시합니다. 체크섬 항목을 정렬해 full checkout·standalone bundle·rollback에서 같은 파일 내용은 같은 패키지 버전으로 비교합니다. 시각은 버전 확인용이며, 같은 시간대 수정이나 Pi의 파일 손상도 감지하도록 전송 여부는 실제 파일 해시로 판단합니다. 이전 적용이 완료된 상태에서 모든 파일이 같으면 `CLUSTER_PI_UNCHANGED`를 출력하고 백업 생성·서비스 재시작·Pi 재부팅을 생략합니다. 첫 적용은 기존 설치 설정을 갱신합니다.

전체 파일 전송이 필요하면 `bash scripts/pi_update.sh --full`을 사용합니다. `--full`은 증분 전송만 해제하며 동일 버전의 재시작 생략 규칙은 유지합니다. 별도 Pi 저장소/submodule 없이 현재 C4 checkout과 공유 렌더러·필수 에셋을 함께 배포합니다.

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
--frame-timeout 2       영상 최신성 제한·초기 협상 메시지 수신 제한(초)
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

## 로그 수집

### Git Pull 성공 후 로컬 로그 전체 초기화

`gitpull.sh`는 업데이트 성공과 커밋 일치 확인 후 `restart.sh --clear-logs`를 실행합니다.
교체용 tmux 세션에서 이전 실행을 종료한 뒤, 진행 중인 crash 전송·일반 로그 전송·Pi
수집이 끝나기를 기다리고 `/data/log`의 파일과 숨김 보관 폴더를 모두 정리합니다.
`.cluster-pi-pending`에 남은 이전 미전송 사본도 이때 제거합니다. `/data/log` 디렉터리와
권한은 유지하며 심볼릭 링크가 가리키는 외부 내용은 삭제하지 않습니다.

정리는 새 openpilot 실행 전에 수행하므로 이전 업로드 작업이 사본을 다시 만들지 않습니다.
잠금 대기는 최대150초이며 실패하면 tmux에 오류를 남기고 새 실행을 시작하지 않습니다.
`Log reset complete` 출력으로 전체 초기화 완료를 구분할 수 있습니다. Git Pull이 실패하면
이 정리를 수행하지 않습니다. 일반 `restart.sh`와 manager 재시작은 기존 미전송 사본
보존 정책을 유지합니다. NAS에 이미 전송된 파일은 이 로컬 초기화로 삭제되지 않습니다.

### Cluster·crash·Pi 로그 함께 업로드

대시보드 **System Logs → Cluster Debug → UPLOAD**, C4 설정의 **커뮤니티 → Cluster 로그 업로드**, Pi 터치 패널의 **로그 전송**은 같은 업로드 경로를 사용합니다. C4 `cluster_debug.log`를 전송하고, 같은 디렉터리에 `cluster_crash.log` 또는 회전 보관본 `cluster_crash.log.1`이 있으면 실제 오류 기록이 있는 파일만 별도로 함께 전송합니다. 빈 파일과 완전한 정상 시작·종료 기록만 있는 파일은 생략하며 USB·Network 모두 적용됩니다. 알 수 없는 데이터나 잘린 스택은 오류로 보존하고 원본을 수정하지 않습니다. **Pi SSH 수집과 보관된 Pi 파일의 재전송은 Cluster Enable이 켜져 있고 Network 디스플레이일 때만 수행합니다.**

보관 경로를 사용할 수 있으면 작업별로 C4·crash 파일의 사본을 먼저 만들어 원본이 바뀌어도 재전송에는 당시 내용을 사용합니다. 같은 작업의 모든 파일은 시각·작업 ID·차량·기기 접두사가 같으며, 같은 분에 다시 업로드해도 새 작업 ID로 이전 파일을 덮어쓰지 않습니다.

```text
..._cluster_debug.log       C4 렌더링·전송·자원 상태
..._cluster_crash.log       C4 크래시 원본 (오류 기록이 있을 때)
..._cluster_crash.log.1     C4 이전 크래시 원본 (오류 기록이 있을 때)
..._cluster_pi_debug.log    Pi 서비스 journal·출력 성능·수집 상태
```

`cluster_debug.log`에는 현재 오류의 간단한 내용·PID·종료 신호·crash 경로를 남깁니다. 전체 Python traceback과 네이티브 fatal stack은 crash 원본에 보관하며, 새 세션의 debug 로그나 Pi 보고서에 다시 복사하지 않습니다. crash 파일은 텍스트를 요약하거나 자르지 않고 수집한 사본 그대로 전송하므로 긴 스택은 별도 파일에서 확인합니다. 원본 크래시 기록 자체가 불완전한 경우 업로드가 누락된 스택을 복원할 수는 없습니다.

Pi 파일에는 최근 `cluster-hdmi.service` 로그 최대 2500줄, 수집 시각·부팅 정보, 서비스 상태, Wi-Fi 절전·현재 링크 상태를 담습니다. UTC 시각과 최신 순서로 journal을 기록하며, 저장된 이전 부팅의 로그도 포함합니다. 전체 파일은 최대 512 KiB입니다. Wi-Fi 비밀번호나 전체 연결 프로필은 수집하지 않습니다. SSH 포트는 **9122를 먼저 시도**하고 연결 자체가 실패한 경우에만 22로 재시도합니다. 인증 실패나 Pi 명령 실패에는 다른 포트로 재시도하지 않으며 SSH 설정도 바꾸지 않습니다.

서비스 PID·monotonic 시작 시각·재시작 횟수, 커널 버전과 설치 소스 지문도 함께 기록합니다. Wi-Fi station의 재시도·실패·beacon 손실 통계는 연결 이후의 누계이며, 인터페이스 통계는 인터페이스 생성 이후의 누계입니다. 수집 시 한 번 읽는 값이므로 한 통계 창의 손실률로 해석하지 않습니다. station의 peer 주소·SSID는 제외합니다. 부팅 초기 시계가 보정되면 journal 벽시계 간격이 달라질 수 있으므로 uptime·부팅 ID·서비스 monotonic 시작 시각을 대조합니다. 초기 양수 `signal`처럼 드라이버가 이상한 값을 보고한 경우도 있어 RSSI 하나로 링크 상태를 판단하지 않습니다.

Pi는 수집할 때 켜져 있고 C4와 같은 핫스팟에 연결되어 있어야 합니다. 현재 연결된 IP가 없으면 C4 주행 로그의 마지막 Pi 연결 주소를 사용합니다. 다른 주소·계정을 쓰면 `CLUSTER_PI_HOST`, `CLUSTER_PI_USER`, `CLUSTER_PI_IDENTITY`, `CLUSTER_PI_PASSWORD` 환경 변수로 지정할 수 있습니다. 수집은 25초 제한을 공유하며 주소 검색도 로그 뒤에서 최대 32 MiB만 읽습니다. 수집 시작부터 보고서를 원자적으로 저장해 프로세스가 중단돼도 `collecting` 상태와 당시 수집 정보를 남깁니다. Pi 수집에 실패해도 C4 로그 업로드는 유지하고, Pi 파일에 실패 상태를 기록하며 대시보드와 커뮤니티 화면에는 경고를 표시합니다. 명확한 USB 전용 로그는 SSH를 시도하지 않고 수집 생략 상태를 기록합니다. Pi journal이 재부팅 후 남아 있지 않으면 이전 주행 로그를 복구할 수는 없습니다.

crash·Pi 파일의 NAS 전송에 실패하면 `/data/log/.cluster-pi-pending/pending.<작업 ID>/`에 사본과 접두사를 보관합니다. 다음 명시적 Cluster 업로드에서 이전 작업을 최대 2개, 총 20초 범위로 재전송합니다. 이미 수집한 파일은 다시 수집하거나 덮어쓰지 않고 원래 C4 파일의 접두사를 사용하며, 성공한 파일은 표시해 실패한 파일만 재시도합니다. 중단된 Pi 수집은 당시 실패 보고서로 전송하며 나중의 Pi 상태를 이전 주행 로그로 붙이지 않습니다. 모든 파일의 전송이 끝나야 해당 작업의 사본을 정리합니다.

보관 예산은 최대 64 MiB 또는 16개 작업이며 crash 사본과 진행 중인 Pi 보고서 공간도 계산합니다. 한도에 도달하면 이전 파일을 보존하고 신규 C4 파일과 crash 파일의 별도 전송을 시도하며 새 Pi 수집은 생략합니다. 보관 경로를 사용할 수 없어 crash 전송 실패의 재시도 사본을 남기지 못한 경우에도 명시적으로 경고합니다. `/data/log/.cluster-pi-pending/history.log`에는 수집·전송·재시도 단계 요약을 최대 약 64 KiB로 기록합니다. `CLUSTER_LOG_UPLOAD` 출력과 이 파일로 SSH 수집 실패와 NAS 전송 실패를 구분할 수 있습니다. Dashboard·C4 설정·Pi 터치 패널은 `CLUSTER_LOG_WARNING`과 기존 `CLUSTER_PI_LOG_WARNING`을 부분 완료로 표시합니다.

C4 SSH에서 직접 업로드할 수도 있습니다. 이 명령도 해당하는 로그를 함께 처리합니다.

```bash
cd /data/openpilot
bash scripts/log_upload.sh /data/log/cluster_debug.log
```

## 장비 확인 순서

1. HDMI 영상이 가로 설치 방향에 맞고 잘리거나 늘어나지 않는지 확인합니다.
2. `--log-touch`로 네 모서리 보정을 확인하고, 밝기 슬라이더·화면 끄기·재터치 복원을 확인합니다. 80% 밝기가 여러 프레임에 걸쳐 더 어두워지지 않는지도 확인합니다.
3. 정차 상태에서 Wi-Fi를 끊어 2초 이내에 마지막 운행 영상이 연결 대기 화면으로 바뀌고, 재연결하면 최신 화면으로 복구되는지 확인합니다.
4. C4 `[CLUSTER_H264_PERF]`와 Pi `[CLUSTER_H264_RX_PERF]`의 영상 수신·표시 FPS와 최대 표시 간격을 확인합니다. 화면 OFF·재터치 후 복원, 네트워크 재연결도 확인합니다.
5. model lagging 또는 locationd 오류가 발생하면 같은 시각의 `[CLUSTER_RESOURCE_PERF]`와 openpilot 오류 로그를 함께 보관합니다.
