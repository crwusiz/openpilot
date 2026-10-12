# 2026-10-12 network cluster 주행 분석

현재 병목은 TCP 영상 수신 대기와 C4 화면 합성 양쪽에 있다. 먼저 JPEG 품질 68·최대 20FPS로 전송량과 반복 작업을 줄여 검증한다. 이는 20FPS 달성을 보장하는 설정이 아니다. WebRTC 카메라 전송과 Pi 합성은 장기 후보이며, Pi 하드웨어 디코더 성능은 아직 실증하지 않았다.

통계는 로그에 기록된 약 10초 통계 창의 산술평균이다. 개별 프레임을 모두 합산한 평균과 다르며, 아래 범위도 통계 창의 범위다. `frame_age`는 C4 렌더 시작부터 수신 ACK까지로, 카메라 촬영 시각부터 실제 HDMI 표시까지의 지연은 아니다.

12:32 C4 파일의 776줄 전체가 17:07 C4 파일의 prefix와 동일하므로 중복 집계에서 제외했다. Pi journal의 UTC에 9시간을 더하고, 헤더의 현재 boot ID와 receiver PID를 확인해 당일 현재 PID의 기록을 C4 시간과 맞췄다. 세 Pi의 boot ID는 서로 다르다. 초기 `00:05` 시각은 시간 동기화 전 기록이므로 주행 시작이나 긴 장애 시간으로 사용하지 않았다. 누적 파일의 나머지 주행 및 offroad 구간은 아래 현재 Pi 대응 구간과 분리했다.

| 현재 Pi 대응 구간(KST) | 12:07~12:32 | 16:36~17:07 | 18:09~18:36 |
| --- | ---: | ---: | ---: |
| C4 RESOURCE / Pi RX 통계 창 수 | 150 / 135 | 182 / 154 | 164 / 155 |
| `model_seen=1`인 C4 창 | 144/150 | 182/182 | 164/164 |
| C4 render 평균 | 58.7ms | 66.0ms | 55.8ms |
| C4 Path / HUD 평균 | 30.1 / 23.1ms | 36.0 / 24.5ms | 26.2 / 23.5ms |
| JPEG 준비 평균 | 28.6ms | 28.9ms | 28.1ms |
| C4 ACK 대기 평균 | 472.9ms | 309.8ms | 256.3ms |
| C4 frame age 평균 | 581.3ms | 428.3ms | 363.2ms |
| Pi 표시 FPS 평균(창 범위) | 3.73 (1.15~6.48) | 4.86 (1.45~6.98) | 5.73 (3.20~8.45) |
| Pi JPEG 본문 수신 / payload wait 평균 | 245.9 / 242.8ms | 141.7 / 139.0ms | 97.6 / 95.7ms |
| Pi 디코딩 / 전체 표시 처리 평균 | 23.5 / 34.3ms | 22.2 / 32.4ms | 19.2 / 29.3ms |
| Pi 수신 완료→표시 시작 대기 평균 | 1.2ms | 1.7ms | 2.3ms |
| Pi 표시 간격의 관측 최대 | 1882ms | 1709ms | 1892ms |
| C4 프로세스 CPU 평균 | 49.5% | 57.4% | 60.9% |

`seen=1`은 한 번 이상 수신했다는 뜻이며, 현재 입력이 신선하다는 보장은 아니다. 입력 중단은 별도의 monotonic age로 확인했다. CPU 100%는 한 코어를 가득 사용한다는 뜻이다.

Pi 본문 수신 시간의 98.0~98.7%가 `payload_wait`이고 표시 큐 대기는 짧다. 수신 경로가 현재의 큰 지연 비중을 차지한다. 이 시간에는 소켓 대기와 스케줄 지연이 포함되므로 Wi-Fi만을 원인으로 확정할 수 없다. GPU 회전·스케일은 0.0ms, flip은 약 0.4ms, texture upload는 약 3ms이다. 표시 실패나 GPU fallback 기록은 없었다. 대표 수신 지연은 [오전 Pi 로그 179줄](D:/_My_/Downloads/26-10-12-12_32_07-VtmkBHbc_HYUNDAI_IONIQ5_PE_ff2e9a526f365c14_cluster_pi_debug.log:179)에서 확인할 수 있다.

세 Pi 모두 `Power save: off`, GPU texture 및 `staging=reused_argb8888` 기록이 있다. C4는 raw 카메라 수신 약 20Hz를 유지하면서 전처리를 약 7~9Hz로 줄였고 `receive_fps/skipped`가 출력된다. GPU 업로드와 카메라 backpressure 변경은 반영된 상태다. 현재 로그의 설정은 모두 JPEG 품질 82·목표 60FPS다. Pi `CLUSTER_RX_RUNTIME`은 pygame/SDL 버전 기록이며 CPU·RSS·온도 시계열이 아니므로 Pi 과부하 여부는 이 자료만으로 판단하지 않았다.

C4 합성 평균 56~66ms는 20FPS의 50ms 예산을 초과한다. JPEG 품질을 낮춰도 Path/HUD 비용 자체는 남는다. 대표 합성 기록은 [오전 C4 로그 138줄](D:/_My_/Downloads/26-10-12-12_32_07-VtmkBHbc_HYUNDAI_IONIQ5_PE_ff2e9a526f365c14_cluster_debug.log:138)이다. 현재 JPEG 크기로 20FPS 전송에는 payload만 약 15~20Mbps가 필요하지만 관측 처리량은 약 3.7~4.6Mbps다. 품질 68·20FPS 튜닝 후 같은 주행 조건에서 크기, ACK/frame age, 수신 대기, 합성 시간을 다시 비교해야 한다. WebRTC만 교체하면 C4 합성 비용은 유지된다. 카메라 압축 전송과 Pi 합성까지 옮기는 구조는 디코더·합성 성능을 별도로 검증해야 한다.

모델/location 입력 문제는 전송 문제와 분리해 봐야 한다. 12:42:08에는 모델 drop peak 2.4%, lagging 갱신 40회가 기록됐지만 같은 창의 NETWORK는 6.41FPS·ACK 222ms로 네트워크 최악 구간이 아니었다. [오후 C4 로그 1077줄](D:/_My_/Downloads/26-10-12-17_07_05-9k9X42in_HYUNDAI_IONIQ5_PE_ff2e9a526f365c14_cluster_debug.log:1077)

`inputsOK=0`은 13:43:23~13:58:14의 90개 창에서 16913회, 18:16:42~18:25:13의 52개 창에서 10162회 기록됐다. 각각 다음 창인 13:58:24와 18:25:23에 복구했다. 저녁에는 직전 모델·motion 입력 age가 85초까지 증가한 뒤 model execution peak 785ms와 함께 재수신됐다. [저녁 C4 로그 1585줄](D:/_My_/Downloads/26-10-12-18_36_42-0YzzFzTS_HYUNDAI_IONIQ5_PE_ff2e9a526f365c14_cluster_debug.log:1585)

같은 구간에도 carState는 수ms로 신선하고 카메라는 약 20Hz를 유지했다. 모델 입력 중단 중 Cluster CPU가 급증한 패턴도 없어 클러스터 업데이트 스레드 전체 정지와 맞지 않는다. 그러나 자원 수치의 상관관계만으로 클러스터의 영향이나 발행 프로세스 재시작을 확정할 수 없다. `model_lagging_updates=0`도 새 모델 메시지의 drop 경고가 없다는 뜻이며, 발행 중단을 배제하지 않는다. 다음 분석에는 해당 시간의 **manager, modeld, locationd 원로그**를 반드시 함께 수집해 프로세스 종료·재시작, 모델 초기화, 입력 검증 오류를 대조해야 한다.
