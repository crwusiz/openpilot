# TURZX USB 클러스터

[2026-10-17 USB 기기 로그 분석](PERFORMANCE_2026-10-17.md): USB 제출은 평균 약13.3~13.6FPS이며 RGB→NV12 변환이 평균 약56~58ms입니다. 다음 성능 개선은 이 변환 경로를 우선 점검합니다.

Dashboard의 **Toggles → Cluster Enable → USB (TURZX Display)**를 선택합니다. USB는 H.264 전용이며 코덱 선택 옵션은 없습니다. Pi 로그는 USB 모드에서 수집·업로드하지 않습니다.

H.264는 **C4에서 기존 카메라·계기·아이콘·경로를 합성한 전체 화면을 하드웨어 인코딩**해 TURZX 9.2인치 모니터로 보냅니다. Network H.264의 원본 카메라 전송/Pi 합성과 처리 위치가 다릅니다. 화면 배치와 `rotate_180` 설정을 유지합니다. 실제 디코딩·출력은 USB 모니터에서 수행하며 Orange Pi는 필요 없습니다.

## 빌드와 적용

Params 정리와 네이티브 공유 라이브러리가 포함되어 있으므로 **C4 코드를 업데이트하고 정상 openpilot 빌드·재시작을 완료한 후** USB 출력을 사용합니다. C4용 `comma_arm64` SCons 빌드에 `openpilot/system/loggerd/libcluster_h264_encoder_bridge.so`가 자동 포함됩니다. PC나 Orange Pi용 H.264 인코더는 설치하지 않습니다.

하드웨어 브리지가 없거나 V4L2 인코더를 열지 못하면 `cluster_debug.log`에 오류를 기록합니다. 자동 소프트웨어 인코딩은 실행하지 않습니다. 빌드·기기 문제를 수정한 뒤 Cluster를 껐다 켜서 다시 시도합니다. USB 모드는 기존 `cluster-render`·`cluster-usb` 의존성 그룹을 사용하고, Network의 aiortc 의존성을 요구하지 않습니다. 기존 USB/eGPU 실행 정책도 유지합니다.

USB 케이블만 연결한 부팅에서는 Cluster 활성화 설정을 유지합니다. `ChestnutLoading`은 일반 Type-C 케이블에서도 잠시 켜질 수 있으므로, 로딩 중 USB 차단은 실제 Chestnut의 VID/PID(ROM 부트로더 포함)가 확인될 때만 적용합니다. `ChestnutActive`가 켜진 실제 eGPU 사용 중에는 기존 차단을 유지합니다. 실제 Chestnut 충돌로 OFF가 저장된 경우에는 분리 후 사용자가 Cluster를 다시 켭니다.

## 초기 설정

| 항목 | 값 |
| --- | --- |
| 목표 합성·인코딩 속도 | 20FPS |
| 비트레이트 | 약 4.67Mbps (`7Mbps @ 30FPS`의 프레임당 기준) |
| 프레임 구조 | GOP 1, 독립 IDR 프레임 |
| 프로파일 / 슬라이스 | Constrained Baseline / 4096바이트 |
| 인코더 | Qualcomm V4L2, VBR-CFR, 실시간 우선순위 사용 안 함 |
| 패널 영상 / 인코더 크기 | 세로 462×1920 / 464×1920, SPS에서 오른쪽 2픽셀 제외 |
| USB 전송 | 최대 32KiB 청크, 기본 no-ACK, 수신 ACK 큐 배출·선택적 상태 확인 |

독립 프레임마다 SPS/PPS를 포함합니다. 대기 슬롯은 최신 화면만 보관하고 오래된 프레임을 생략하므로, USB 재연결 후 이전 주행 화면을 길게 재생하지 않습니다. 부분 전송 실패 시 연결을 다시 준비하며 모니터·공유 USB 허브를 reset하지 않습니다. 종료 시 송신·인코딩 작업이 끝난 뒤 하드웨어 핸들을 닫고 모니터 재생을 중지·화면을 지웁니다.

현재 렌더러는 RGB 화면을 CPU에서 NV12로 바꾼 뒤 하드웨어 인코더에 전달합니다. 참고 브랜치의 GLES/DMA-BUF 직접 렌더링은 이 버전에 포함하지 않았습니다. 하드웨어 인코딩이 곧 C4 부하나 지연 개선을 보장하지 않으므로 실제 로그로 비교합니다.

## 로그 비교

시작 단계·연결 오류·종료 이유는 C4 터미널에도 즉시 출력합니다. Python 예외는 `[CLUSTER_CRASH] stage=...`와 원인 traceback을 표시하고, 관리자에서 `[CLUSTER_PROCESS_EXIT] pid=... exit_code=... reason=...`를 한 번 출력합니다. 네이티브 오류는 `SIGABRT`·`SIGSEGV` 등 종료 신호와 해당 PID의 저장된 fatal stack을 확인할 수 있습니다. `SIGKILL`처럼 traceback을 남길 수 없는 종료도 신호는 표시하지만, 신호만으로 OOM 등 근본 원인을 단정하지 않습니다.

디스플레이 변경으로 정상 종료하면 `[CLUSTER_RESTART]`와 함께 다시 실행합니다. 오류 종료는 반복 재시작하지 않으며, 출력을 변경하거나 Cluster를 껐다 켜면 다시 시도합니다. 상세 기록은 `/data/log/cluster_debug.log`·`/data/log/cluster_crash.log`에 보관합니다.

- `[CLUSTER_START]`, `[CLUSTER_MODE]`: `transport=usb`, `codec=h264`, `compose=c4` 확인
- `[CLUSTER_USB_H264_ENCODER]`: 실제 하드웨어·해상도·인코딩 설정
- `[CLUSTER_USB_H264_ENCODE_PERF]`: 인코딩 완료 FPS, RGB 변환 시간, 입력부터 출력 준비까지 시간
- `[CLUSTER_USB_H264_PERF]`: USB 제출 FPS·바이트·시간·모니터 큐 상태·생략 수·ACK 배출 수
- `[CLUSTER_MAIN_PERF]`, `[CLUSTER_CAM_PERF]`: 합성 속도와 새 카메라 영상 준비 속도
- `[CLUSTER_RESOURCE_PERF]`, `[CLUSTER_INPUT_DIAG]`, `[CLUSTER_LOAD_GUARD]`: C4 CPU·RSS, modeld/locationd·통신 입력 상태와 부하 보호

`submitted_fps`는 USB로 완전히 보낸 횟수이며 모니터가 실제 표시한 FPS나 촬영부터 표시까지의 지연 측정값이 아닙니다. 카메라 준비 FPS, 입력 수신 여부, 조향 표시 반응과 함께 확인합니다. 차량·모델 입력이 없는 책상 테스트만으로 주행 부하를 비교하지 않습니다.

현재 검증은 모의 USB·네이티브 ABI와 실제 H.264 스트림의 SPS crop·디코딩 테스트입니다. C4 네이티브 빌드, 실제 패널의 영상 깨짐·지연·재연결과 주행 성능은 기기에서 확인해야 합니다. 2026-10-17 USB 기기 테스트에서 영상 출력을 확인했으며, 이후 검증은 H.264 프레임과 지연에 집중합니다.
