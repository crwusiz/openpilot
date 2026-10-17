# TURZX USB 클러스터

Dashboard의 **Toggles → Cluster Enable → USB (TURZX Display) → Cluster USB codec**에서 JPEG / H.264를 선택합니다. `ClusterUsbCodec`는 Network 코덱과 별도로 저장하며 기본은 기존 JPEG입니다. 코덱을 바꾸면 실행 중인 클러스터가 정리 후 재시작합니다. Pi 로그는 USB 모드에서 수집·업로드하지 않습니다.

H.264는 **C4에서 기존 카메라·계기·아이콘·경로를 합성한 전체 화면을 하드웨어 인코딩**해 TURZX 9.2인치 모니터로 보냅니다. Network H.264의 원본 카메라 전송/Pi 합성과 처리 위치가 다릅니다. 기존 JPEG 화면 배치와 `rotate_180` 설정을 유지합니다. 실제 디코딩·출력은 USB 모니터에서 수행하며 Orange Pi는 필요 없습니다.

## 빌드와 적용

새 Params 키와 네이티브 공유 라이브러리가 포함되어 있으므로 **C4 코드를 업데이트하고 정상 openpilot 빌드·재시작을 완료한 후** USB H.264를 선택합니다. C4용 `comma_arm64` SCons 빌드에 `openpilot/system/loggerd/libcluster_h264_encoder_bridge.so`가 자동 포함됩니다. PC나 Orange Pi용 H.264 인코더는 설치하지 않습니다.

하드웨어 브리지가 없거나 V4L2 인코더를 열지 못하면 `cluster_debug.log`에 오류를 기록합니다. 자동 소프트웨어 인코딩이나 코덱 전환을 하지 않으며 Dashboard에서 USB JPEG를 선택해 복귀할 수 있습니다. USB 모드는 기존 `cluster-jpeg`·`cluster-usb` 의존성 그룹을 사용하고, Network의 aiortc 의존성을 요구하지 않습니다. 기존 USB/eGPU 실행 정책도 유지합니다.

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

- `[CLUSTER_START]`, `[CLUSTER_MODE]`: `transport=usb`, `codec=h264`, `compose=c4` 확인
- `[CLUSTER_USB_H264_ENCODER]`: 실제 하드웨어·해상도·인코딩 설정
- `[CLUSTER_USB_H264_ENCODE_PERF]`: 인코딩 완료 FPS, RGB 변환 시간, 입력부터 출력 준비까지 시간
- `[CLUSTER_USB_H264_PERF]`: USB 제출 FPS·바이트·시간·모니터 큐 상태·생략 수·ACK 배출 수
- `[CLUSTER_MAIN_PERF]`, `[CLUSTER_CAM_PERF]`: 합성 속도와 새 카메라 영상 준비 속도
- `[CLUSTER_RESOURCE_PERF]`, `[CLUSTER_INPUT_DIAG]`, `[CLUSTER_LOAD_GUARD]`: C4 CPU·RSS, modeld/locationd·통신 입력 상태와 부하 보호

`submitted_fps`는 USB로 완전히 보낸 횟수이며 모니터가 실제 표시한 FPS나 촬영부터 표시까지의 지연 측정값이 아닙니다. 카메라 준비 FPS, 입력 수신 여부, 조향 표시 반응과 함께 확인합니다. 차량·모델 입력이 없는 책상 테스트만으로 주행 부하를 비교하지 않습니다.

현재 검증은 모의 USB·네이티브 ABI와 실제 H.264 스트림의 SPS crop·디코딩 테스트입니다. C4 네이티브 빌드, 실제 패널의 영상 깨짐·지연·재연결과 주행 성능은 기기에서 확인해야 합니다. JPEG는 비교·복구용으로 유지합니다.
