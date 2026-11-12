# CAN / SPI 주행 진단 로그

호스트 pandad와 Panda 펌웨어를 함께 빌드·업데이트하면 자동으로 기록한다. 별도 설정은 없다. `rlog.zst`의 `logMessage`에서 `CAN_DIAG`와 `SPI_DIAG`를 찾는다. 정상 통계와 대기 중 통계는 DEBUG 로그라 기본 tmux 화면이나 qlog에는 나오지 않는다. 최초 SPI 실패의 `SPI_DIAG`는 ERROR 레벨로 남겨 tmux와 qlog에서도 확인할 수 있다.

펌웨어가 진단 요청을 지원하지 않으면 `CAN_DIAG unavailable ret=...`를 한 번 남기고 해당 조회만 중단한다. 통신 상태를 복구하거나 제어를 재개하지 않는다.

## CAN_DIAG

초당 10회, 추가된 읽기 전용 요청 `0xD9`(param1=1, length=64)로 64바이트를 읽고 호스트에서 한 줄을 출력한다. 기존 CAN/health 패킷 형식과 버전은 유지한다. 통계 배열의 순서는 **bus 0, bus 1, bus 2**다.

| 필드 | 의미 |
| --- | --- |
| `panda_us` | Panda의 스냅샷 시각. 32비트 µs 타이머이므로 약 71.6분마다 순환한다. |
| `read_us` | 호스트의 진단 조회 시간. SPI 잠금 대기도 포함한다. |
| `queued` | 현재 소프트웨어 TX 큐에 대기 중인 프레임 수. 최대 415개다. |
| `peak` | 직전 조회 이후 최대 TX 큐 점유량. 짧은 정체도 보존한다. |
| `wait_max_us` | 직전 조회 이후 꺼낸 프레임의 최대 큐 대기 시간과 현재 가장 오래 대기한 프레임의 나이 중 큰 값. 실제 선로의 ACK 완료 시간은 아니다. |
| `tx_max_us` | `process_can()` 한 번의 최대 소요 시간. 기존 임계 구역 안에서 측정한다. |
| `rx_safety_max_us` | 수신 프레임의 MPU 열기 → FWD/RX safety 검사 → MPU 닫기 구간의 최대 소요 시간. safety 두 복사본의 계산을 포함한다. |
| `dropped` | 직전 조회 이후 해당 TX 큐 포화로 넣지 못한 프레임 수. RX 큐 손실과는 구분된다. |
| `hw_free` | 조회 시점의 FDCAN 하드웨어 TX FIFO 여유. 소프트웨어 큐와 구분한다. 매핑이 없으면 65535다. |
| `spi_nacks` | 직전 조회 이후 펌웨어가 새 endpoint 3 요청에 NACK한 횟수. 캐시된 응답의 재전송은 추가로 세지 않는다. |
| `first_nack_queued` | 해당 조회 구간에서 **최초 NACK 순간**의 버스별 TX 큐 점유량. NACK가 없으면 0이다. |
| `spi_write_max_us` | 수락된 SPI CAN 송신 배치의 `comms_can_write()` 최대 소요 시간. 배치 파싱·TX safety 검사·큐 삽입을 포함한다. |

`peak`, `dropped`, 각 최대 시간과 NACK 통계는 정상적인 진단 조회가 처리될 때만 초기화한다. 첫 스냅샷은 부팅 이후의 누적 구간이다. 큐 내용·포인터·프레임별 삽입 시각과 기존 health 카운터는 초기화하지 않는다. 잘못된 진단 버전이나 길이로는 통계를 소비하지 않는다. µs 최대 시간 중 16비트 필드와 `dropped`는 65535에서 포화하며, `wait_max_us`는 32비트다. 타이머 차이는 unsigned 뺄셈으로 계산한다.

## SPI_DIAG

요청별 수치를 수집하며, 정상 시 스레드별 최대 초당 1회, 지연/NACK 중에는 최대 초당 10회 출력한다. 해당 스레드에서 연속 실패가 시작되는 첫 실패는 주기 제한과 별개로 출력한다. NACK 재시도가 끝나지 않는 동안에도 `pending=1`로 중간 기록을 남긴다. 새 로그 출력은 SPI 잠금을 해제한 뒤 수행한다.

| 필드 | 의미 |
| --- | --- |
| `ep`, `id`, `tx`, `rx`, `tx_prefix` | endpoint, 마지막 시도의 요청 ID, 송신/최대 수신 데이터 길이, 송신 데이터 앞 8바이트. 전체 배치 식별에 사용한다. |
| `ret`, `pending` | 현재 결과와 재시도 진행 여부. `pending=0`은 요청 종료다. |
| `elapsed_us` | 요청 전체 경과 시간. 잠금 대기·재시도 backoff를 포함한다. |
| `lock_us`, `lock_max_us` | 요청 내 모든 시도의 잠금 대기 합계와 최댓값. mutex와 flock 대기를 합쳐 측정한다. |
| `transfer_us` | 잠금 대기를 제외한 SPI 시도 시간 합계. ioctl과 응답 검사 등을 포함하며 재시도 backoff는 제외한다. |
| `ioctl_us`, `ioctl_max_us` | 저수준 SPI 교환 시간 합계와 최댓값. |
| `tries`, `nacks`, `ioctls`, `ioctl_errors` | SPI 시도 수, 호스트가 받은 NACK 수, 저수준 교환 수, 저수준 오류 수. |
| `polls`, `invalid`, `stale` | 응답 읽기 수, 유효한 응답으로 인정하지 못한 수, CRC는 맞지만 ID가 다른 응답 수. |
| `expired_at_lock` | 기존 deadline이 이미 지난 뒤 잠금을 획득한 시도 수. deadline 계산에는 관여하지 않는다. |

`pending=1` 기록은 누적 통계다. 서로 다른 줄의 `nacks`, `lock_us` 등을 다시 합산하지 않는다. NACK 이후에는 원래 코드대로 새 ID를 사용하므로 한 요청의 중간 기록에서도 `id`가 바뀔 수 있다.

## 원인 구분에 사용할 조합

- `queued`/`peak`/`wait_max_us` 증가와 `hw_free=0`: 하드웨어 FIFO가 비지 않는 송신 정체가 있는지 본다. 이것만으로 선로 원인을 확정하지 않는다.
- 큐 대기 증가와 `rx_safety_max_us`/`tx_max_us` 증가: 처리 시간 변화 및 기존 `pandaStates.interruptLoad`와 대조한다.
- `first_nack_queued`: SPI가 송신을 거절한 순간 어느 버스의 큐가 차 있었는지 확인한다.
- `lock_max_us` 증가와 `expired_at_lock>0`: 응답 대기 전에 잠금 대기로 시간이 소모됐는지 확인한다.
- `ioctl_max_us` 증가 또는 `invalid`/`ioctl_errors` 증가: 교환 지연·응답 손상과 잠금 대기를 구분한다.

다음 오류 시각 전후의 **rlog**가 필요하다. qlog와 사진만으로는 정상 시점부터의 큐·처리 시간 변화를 복원할 수 없다.

## 변경 범위와 검증

기존 SPI deadline, NACK 재시도/backoff, ID 재사용, 통신 불량 고정 상태, safety 판정과 CAN 송수신 조건은 변경하지 않는다. 진단 통계 수집에 약 5 KiB RAM, 추가 시계 읽기와 통계 갱신, 100 ms마다 한 번의 진단 조회가 필요하다. 실기에서 계측 부하는 별도로 확인해야 한다.

Panda 큐 테스트는 실제 큐 코드의 FIFO·포화·버스 분리·스냅샷 비변경·타이머 및 링 순환·NACK 당시 큐 보존·잘못된 조회 처리를 검증한다. 호스트 테스트는 미지원 펌웨어 처리와 로그 필드 순서를 검증한다. SPI 테스트는 실제 재시도 함수를 컴파일해 ACK, NACK, CRC 손상, 무응답, 잠금 지연, 수신, 오류 고정 상태를 검증한다.
