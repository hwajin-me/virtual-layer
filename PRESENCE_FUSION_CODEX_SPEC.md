# Presence Fusion — Home Assistant Custom Integration 구현 명세 / Codex 작업 프롬프트

문서 버전: 2.0  
작성 기준일: 2026-09-22  
Integration domain: `presence_fusion`

이 문서는 이전 대화의 구현 제안을 대체하는 실행 가능한 요구사항이다. 아래의 MUST는 필수, SHOULD는 합리적인 예외를 문서화할 수 있는 권장 사항이다. 수치 기본값과 GPS 판정식은 이 프로젝트를 위한 초기 휴리스틱이며, Home Assistant 공식 권장값이나 통계적으로 검증된 정확도 보장이 아니다.

## 0. Codex 실행 계약

현재 저장소에 실제로 설치 가능한 Home Assistant Custom Integration을 구현하라. 설계 설명, 의사코드, 파일 이름, TODO만 작성하고 작업을 끝내지 않는다. 이 문서의 필수 범위를 구현하고 실제 테스트를 실행한다.

작업 전에 저장소의 `AGENTS.md`, 기존 코드, 작업 트리 변경 사항, Python/HA 의존성 및 테스트 환경을 확인한다. 사용자 변경을 덮어쓰거나 관련 없는 파일을 재작성하지 않는다. 기존 기능이 있다면 보존하고 테스트를 추가한다. 기존 명세와 충돌하면 이 문서의 불변조건과 명시된 테스트 기대값을 우선한다.

지원할 Home Assistant Core 및 Python 버전을 먼저 확정한다. 저장소에 명시된 대상 버전이 있으면 이를 우선하고, 없으면 공식 배포 정보와 개발 문서로 확인 가능한 안정 버전을 선택한다. 정확한 테스트 버전과 최소 지원 버전을 README/테스트 보고서에 기록하고, 실제 설치한 HA 코드에서 사용 API와 타입을 확인한다. 막연히 “최신 버전 전체 지원”이라고 쓰거나 확인하지 않은 API를 만들어내지 않는다. 의존성 설치가 불가능하면 이를 구체적으로 기록하고 실행하지 못한 테스트를 통과로 보고하지 않는다.

내부 작업을 다음 순서로 나누되, 계획 작성에서 멈추지 않는다.

1. 요구사항·지원 버전·파일 변경 계획을 확정하고 주요 실패 시나리오의 테스트부터 작성한다.
2. Home Assistant에 의존하지 않는 순수 Python 판정 엔진과 단위 테스트를 구현한다.
3. HA 입력 어댑터, Config/Options Flow, 출력 엔티티를 연결한다.
4. 타이머, 설정 변경, 재시작, unload, 수동 선택 및 개인정보 보호를 구현한다.
5. 통합 테스트·회귀 테스트·정적 검사를 실행하고 오류를 수정한다.
6. 요구사항-구현-테스트 매핑과 실제 실행 결과, 미검증 사항을 보고한다.

비차단적인 선택은 보수적인 기본값을 적용하고 `docs/DECISIONS.md`에 이유를 남긴다. 범위의 핵심 기능을 임의로 “추후 구현”으로 바꾸지 않는다. 실제 HA 운영 인스턴스의 설정 변경·재시작·배포, git push, 외부 계정 연결은 수행하지 않는다.

## 1. 목적, 범위와 한계

한 사람에게 귀속된 여러 물리 기기의 GPS, Wi-Fi 연결, BLE presence, ESPresense room 엔티티를 통합한다. 최근 N분의 의미 있는 이동을 바탕으로 위치를 대표하는 Primary GPS를 선택하고, 분리됐던 기기와 다시 만났을 때 해당 장소에 실제로 모인 유효 기기 중 사용자가 정한 우선순위로 복귀한다.

한 Config Entry는 한 사람에 대응하고, 여러 사람은 서로 독립된 Config Entry로 지원한다. 한 사람의 기기를 다른 사람의 위치 증거로 사용하지 않는다.

v1은 **이미 Home Assistant에 존재하는 엔티티**만 읽는다. Apple Watch, iPhone, iPad 등은 실제로 독립적인 좌표 업데이트를 제공하는 엔티티가 있을 때만 GPS 입력으로 사용할 수 있다. 특정 하드웨어가 있다는 이유로 GPS 엔티티의 존재나 업데이트 주기를 가정하지 않는다.

Wi-Fi/BLE/room 소스는 선택 사항이다. 최소 하나의 GPS 후보 기기로 시작할 수 있어야 한다. 로컬 신호만 있는 부속 기기를 추가할 수 있으나, 그 기기는 좌표 공급자가 될 수 없다.

v1에 포함하지 않는 기능은 Apple/iCloud 로그인, IRK 추출·저장·해석, BLE 스캐닝, ESP32 펌웨어, ESPresense 서버, MQTT 직접 구독, UniFi API 호출, 외부 Python 데몬, 별도 데이터베이스, 지도 프런트엔드, Bayesian/ML 모델, 여러 GPS의 좌표 평균이다. 관련 통합이 만들어낸 HA 엔티티를 소비하는 어댑터만 만든다.

기기의 이동은 사람의 이동을 입증하지 않는다. 모든 기기를 두고 외출하거나, 다른 사람이 기기를 가져가거나, 여러 기기가 서로 다른 방향으로 움직이면 입력만으로 사람의 위치를 확정할 수 없을 수 있다. 이런 한계를 README와 진단에 표시한다. 정확도 백분율이나 “거의 확실”, “메모리 거의 없음”, “항상 정확한 방” 등의 검증되지 않은 보장을 하지 않는다. 제공 상태는 자동화용 추정이며 출입 보안 판단을 대체하지 않는다.

## 2. 반드시 유지할 불변조건

| ID | 필수 조건 |
|---|---|
| INV-01 | GPS/Wi-Fi/BLE/room은 물리 기기별로 묶는다. 사람의 모든 로컬 소스를 전역 OR 처리하지 않는다. |
| INV-02 | 사용자가 Watch를 가지고 외출한 것으로 선택되었다면 집에 남은 iPhone/iPad의 Wi-Fi/BLE/room은 사용자를 home으로 붙잡지 못한다. |
| INV-03 | Primary의 GPS가 stale이 되어도 공간적으로 분리된 집의 다른 기기로 자동 fallback하지 않는다. |
| INV-04 | 이동량이 0으로 감소하거나 사용자가 외부에서 정지했다는 이유만으로 고정 우선순위에 복귀하지 않는다. |
| INV-05 | 재수렴은 이전에 분리된 기기와의 재결합이다. 이미 함께 움직이던 기기끼리 가깝다는 이유로 재수렴시키지 않는다. |
| INV-06 | 소스의 HA 속성 변경 시각을 실제 GPS 측정 시각으로 무조건 취급하지 않는다. |
| INV-07 | 미설정, absent, unknown, unavailable, stale을 구분한다. 모름을 외출 증거로 바꾸지 않는다. |
| INV-08 | 엔진은 기기 식별을 추정한다. 떨어진 여러 이동 그룹 사이에서 근거 없이 Primary를 빼앗거나 확률 99% 같은 값을 출력하지 않는다. |
| INV-09 | 자동 GPS 출력 좌표는 선택된 한 소스에서만 가져온다. 평균, 집 중심 좌표로 스냅, 출처 불명의 보간 좌표를 만들지 않는다. |
| INV-10 | HA 상태 이벤트가 더 오지 않아도 stale, 후보 만료, debounce, override 만료가 처리된다. 타이머는 새 관측 샘플을 만들어내지 않는다. |
| INV-11 | 입력에 이 통합의 출력이나 `person`을 넣지 못하게 하여 직접적인 피드백 루프를 방지한다. 간접 순환 템플릿 입력도 지원하지 않는다고 문서화한다. |
| INV-12 | 설정, 상태, 타이머, 기기 ID, 출력 unique_id는 여러 사람 및 reload 사이에서 섞이거나 중복되지 않는다. |

## 3. 기기 및 입력 데이터 모델

### 3.1 물리 기기 구성

각 `TrackedDeviceConfig`는 Integration 내부의 변경되지 않는 UUID, 표시 이름, GPS 후보 여부, 사용자 우선순위, GPS 엔티티 최대 하나, Wi-Fi/BLE 엔티티 목록, room 엔티티 최대 하나를 갖는다. UUID를 엔티티 이름에서 만들지 않는다. GPS 없는 기기는 보조 증거 또는 home anchor 용도다.

v1은 같은 물리 기기의 여러 GPS 제공자를 별도 기기로 중복 집계하지 않는다. 사용자가 한 기기당 하나의 GPS 제공자를 선택한다. 후보 우선순위는 작은 정수가 우선이며 중복 값은 UI에서 거부한다. 동률 처리에 우연한 Python dictionary 순서를 사용하지 않는다.

소스 설정에는 엔티티 참조, 판정할 state 또는 attribute, 명시적 positive/negative 값, freshness 정책을 저장한다. SSID 센서도 지정된 집 SSID와 비교하는 방식으로 지원한다. 알 수 없는 문자열을 자동으로 home이나 absent로 바꾸지 않는다. 임의 Python/Jinja 코드를 실행하는 입력 형식은 제공하지 않는다.

엔티티 레지스트리에 등록된 소스는 registry 식별자와 현재 entity_id를 관리하여 이름 변경을 처리한다. 삭제·비활성화·일시적 미가용은 다른 상황으로 구분하며, unavailable이라는 이유로 저장된 설정 자체를 지우지 않는다.

### 3.2 GPS 관측

`GPSObservation`은 최소한 다음을 포함한다.

```text
tracked_device_id, source_entity_id
latitude, longitude, accuracy_m, accuracy_assumed
observed_at_utc, received_at_utc, timestamp_basis
validity, rejection_reason
```

위도/경도는 유한 수와 범위를 검증한다. 0도 좌표는 정상 값일 수 있으므로 falsy 검사로 거부하지 않는다. NaN/Infinity/범위 밖 좌표는 거부한다. accuracy가 없거나 0/음수이면 0m의 완벽한 정확도로 취급하지 않는다. 기본 보수값 100m를 쓰고 `accuracy_assumed=true`로 표시한다. 측정 정밀도가 확인되지 않은 관측만으로 GPS 재수렴을 확정하지 않는다.

`gps_accuracy` 등 실제 입력 속성은 대상 HA 및 소스에 맞게 매핑한다. 출력 TrackerEntity의 프로퍼티 이름과 입력 attribute 이름이 같다고 가정하지 않는다.

### 3.3 시각의 의미

GPS 측정 시각을 제공하는 신뢰 가능한 소스 attribute가 설정되어 있으면 그것을 `observed_at`으로 사용한다. 지원 형식은 timezone-aware ISO 8601 및 명시적으로 선택한 epoch seconds/milliseconds다. 단위를 추측하지 않는다.

측정 시각이 없는 일반 엔티티는 좌표/정확도 필드가 변경된 **새 위치 이벤트**를 받았을 때만 수신 시각을 제한적 대체값으로 사용하고 `timestamp_basis=received`로 표시한다. 배터리, 이름, 아이콘, 기타 무관한 attribute 변경은 위치 freshness를 갱신하지 않는다. `last_changed`, `last_updated`, `last_reported`를 모든 소스에 적용하는 만능 GPS timestamp로 사용하지 않는다.

동일 좌표라도 신뢰 가능한 측정 시각이 새로워졌다면 freshness는 갱신하되 이동거리는 증가시키지 않는다. 동일 timestamp/동일 데이터는 중복이고, 이전 timestamp 및 미래 허용 오차를 넘긴 샘플은 거부한다. 동일 timestamp의 충돌 좌표는 최초 유효 관측을 유지하고 진단한다.

재시작 시 읽은 HA 현재 상태는 새로운 측정이 아니다. 실제 측정 시각이 있으면 그 나이를 유지한다. 측정 시각이 없는 복원 상태는 신규 이벤트가 올 때까지 fresh한 위치로 승격하지 않는다. 이 제한 때문에 정지 중의 일부 GPS 소스가 stale이 될 수 있음을 문서화한다.

### 3.4 로컬 증거

로컬 증거의 값은 `present`, `absent`, `unknown`이고 별도로 configured/available/freshness 상태를 가진다.

기본 `source_managed` 정책은 원본 통합이 presence timeout을 관리한다고 가정한다. `on`이 오래 유지되었다는 이유만으로 `last_changed` 나이를 보고 absent로 바꾸지 않는다. 신뢰할 last_seen/heartbeat가 있는 소스는 `timestamp_ttl` 정책과 TTL을 추가로 선택할 수 있게 한다. 일반 state 갱신만으로 MQTT retained 메시지의 실제 측정 나이를 알 수 있다고 주장하지 않는다.

`unknown`/`unavailable`/삭제는 absent가 아니다. BLE와 room이 같은 신호에서 파생되어도 독립적인 확률 증거 두 개로 계산하지 않는다.

## 4. 상태 모델과 기본 파라미터

서로 다른 의미를 한 상태 문자열에 섞지 않는다.

```text
arbitration_mode: priority | dynamic | manual
tracking_health: ok | degraded | ambiguous | no_data
presence: home | nearby | arriving | away | 내부적으로 None(HA unknown)
room: 정규화된 room 문자열 | None
primary_device_id: UUID | None
active_device_ids: 사용자를 대표하는 연속성이 있는 그룹
separated_device_ids: 해당 세션에서 분리된 것으로 관측한 기기
pending_candidate / pending_reunion / transition_reason
```

여기서 active group은 “현재 움직이는 그룹”과 다르다. 외부에서 정지해도 유지된다.

모든 시간 설정의 단위는 초, 거리는 미터로 통일한다. 다음은 재현 가능한 시작값이며 튜닝 가능한 Options에 둔다. 모든 숫자를 메인 설정 화면에 나열하지 말고 기본/고급 설정으로 나눈다.

| 설정 | 초기값 | 의미 |
|---|---:|---|
| movement_window_s | 600 | 최근 이동량 창 |
| movement_bucket_s | 30 | 이동량용 정규화 시간 버킷 |
| minimum_movement_m | 100 | 이동 후보 최소 관측 경로 길이 |
| minimum_movement_segments | 2 | 독립적인 유효 이동 구간 수 |
| gps_stale_after_s | 300 | 이 나이 이상은 자동 GPS 선택 불가 |
| gps_fresh_full_s | 60 | freshness 가중치 1인 구간 |
| gps_max_accuracy_m | 100 | 자동 선택 가능한 accuracy 상한 |
| gps_missing_accuracy_m | 100 | accuracy 누락의 보수적 대체값 |
| gps_future_tolerance_s | 30 | 미래 timestamp 허용 오차 |
| max_observation_gap_s | 180 | 이보다 긴 관측 공백은 경로 연결 금지 |
| max_speed_m_s | 100 | 지상 이동을 가정한 점프 필터 시작값 |
| pair_max_skew_s | 60 | 두 기기 위치 비교의 최대 측정 시각 차이 |
| pair_max_age_s | 120 | 그룹 판정에 쓰는 관측의 최대 나이 |
| together_radius_m | 100 | 정확도 여유를 포함한 근접 기준 |
| separation_radius_m | 150 | 정확도 여유를 제외한 분리 기준 |
| group_confirm_s | 60 | 동행 그룹 인정 최소 관측 기간 |
| challenger_ratio | 1.30 | Primary 교체 비율 기준 |
| challenger_margin | 50 | score 절대 차이 기준 |
| challenger_hold_s | 45 | 후보 우위 지속 시간 |
| minimum_primary_hold_s | 120 | 일반적인 Primary 최소 유지 시간 |
| reunion_hold_s | 120 | 재수렴 지속 시간 |
| safe_companion_age_s | 120 | 장애 시 마지막 동행 증거 유효기간 |
| home_enter_hold_s | 5 | 유효 로컬 귀가 신호 확인 |
| gps_home_enter_hold_s | 60 | GPS-only 집 진입 확인 |
| home_exit_hold_s | 60 | 집 이탈 확인 |
| home_exit_margin_m | 50 | Home zone 밖 판정 여유 |
| nearby_enter_m / nearby_exit_m | 1000 / 1200 | 집 근처 진입/이탈 hysteresis |
| direction_window_s | 180 | 집 방향 계산 창 |
| direction_min_span_s | 60 | 방향 계산 최소 관측 시간 |
| direction_min_change_m | 50 | 의미 있는 집 거리 변화 |
| room_change_hold_s | 8 | 방 변경 debounce |
| room_missing_hold_s | 30 | 방 입력 소실 시 제한적 유지 |
| evidence_hold_s | 300 | 판정 근거 소실 후 마지막 presence 유지 |
| manual_override_default_s | 3600 | 수동 GPS 선택 기본 만료 |

Home의 중심과 반경은 `zone.home`을 기준으로 사용하고 100m를 하드코딩하지 않는다. v1에서 임의의 다른 zone을 Home으로 재정의하는 기능은 제공하지 않는다. 변경 이벤트에 따라 Home geometry를 재평가한다.

검증 규칙에는 양수/유한 수, fresh_full < stale_after, nearby_exit > nearby_enter > 현재 Home 반경, separation_radius > together_radius, 버킷 < 이동 창을 포함한다. 부적합한 설정을 조용히 보정하지 말고 사용자에게 필드 오류를 반환한다.

## 5. GPS 필터와 이동량 알고리즘

### MOV-01. 실제 위치와 이동량 경로를 분리

출력용 최근 유효 GPS 위치는 새 입력에서 갱신할 수 있다. 이동량/방향 계산은 정규화된 경로를 사용한다. 버킷 계산 때문에 로컬 귀가 감지를 30초씩 지연시키지 않는다.

이동량은 GPS로 **관측한 필터링 경로 길이의 추정치**다. 샘플 사이에서 관측되지 않은 우회·왕복 거리를 복원했다고 주장하지 않는다. 경로 길이와 시작점-끝점 displacement를 별도로 유지한다.

### MOV-02. 노이즈, 업데이트 빈도, 느린 이동

이동량용 샘플을 UTC 기준 고정 길이 버킷으로 정규화한다. 버킷마다 유효 샘플 중 accuracy가 가장 좋은 하나를 고르고, 같으면 가장 늦은 측정값을 고른다. 버킷 종료 후 확정하며 과거 버킷을 뒤늦게 수정하지 않는다. 이동 창 및 버킷 경계 처리 방법을 코드와 테스트에 고정한다.

확정 버킷 샘플은 마지막 **인정된 이동 anchor**와 비교한다. 기본 deadband는 다음과 같다.

```text
noise_threshold_m = max(10, anchor_accuracy_m + sample_accuracy_m)
```

anchor와 거리가 deadband 이하이면 이동을 더하지 않고 anchor도 이동시키지 않는다. 이를 넘어선 연속 관측에서 anchor를 새 위치로 옮기고 그 구간의 관측 거리 전체를 더한다. 각 작은 샘플에서 accuracy를 계속 빼는 방식으로 천천히 걷는 이동을 모두 없애지 않는다.

관측 공백 판단은 인접한 유효 관측 사이의 공백을 사용한다. 마지막 이동 anchor가 오래됐다는 이유만으로 연속해서 관측된 느린 이동을 지우지 않는다. 정지 중 anchor 역시 무제한 과거 데이터 보관 없이 관리한다.

### MOV-03. 점프 및 재획득

연속 유효 관측의 시간차 dt가 양수일 때 `speed = max(0, distance - previous_accuracy - current_accuracy) / dt`를 계산한다. 이 값이 `max_speed_m_s`를 넘으면 점프 후보를 격리한다. 격리된 단일 점프는 이동량, 그룹, Primary, 방향을 변경하지 않는다. 거부된 위치를 다음 필터의 정상 anchor로 사용하지 않는다.

관측 공백이 `max_observation_gap_s`보다 길면 이전 위치와 경로를 잇지 않는다. 먼 곳에서 위치가 다시 잡히면 새 구간에서 두 개 이상의 유효 관측이 최소 30초에 걸쳐 나타나고, 그 관측들끼리의 간격이 max_observation_gap 이내이며 위 속도 검증을 통과할 때 재획득을 확인한다. 첫 새 관측은 격리 후보이며 재획득 확정 전까지 출력용 위치로 승격하지 않는다. 새 관측끼리 다시 점프하면 재획득 후보를 초기화한다. 이 검증은 예전 도시의 anchor가 아니라 새 구간 내부를 기준으로 한다. 수 시간 GPS 소실 뒤 실제로 다른 도시에 있는 기기를 영원히 점프로 거부하지 않는다.

속도 상한은 비행기 등의 이동을 지원한다는 보장이 아니다. 사용자가 변경할 수 있고 제한을 문서화한다.

### MOV-04. 이동 창과 score

창 밖 경로를 제거하되 경계 계산용 샘플 하나는 유지한다. 창 경계를 가로지르는 유효 segment는 시간 비율로 거리 기여를 나누며, 이는 구간 내 일정 속도를 가정한 추정이라고 명시한다. 긴 공백의 양 끝을 가상의 이동 구간으로 연결하지 않는다.

기본 score를 아래처럼 구현한다.

```text
M = 최근 movement_window 내 필터링 경로 길이(m)
Q = clamp(25 / max(accuracy_m, 25), 0.1, 1.0)
F = 1                                  if age <= gps_fresh_full_s
F = (gps_stale_after_s - age)
    / (gps_stale_after_s - gps_fresh_full_s)  otherwise
F = clamp(F, 0, 1)
movement_score = M * Q * F
```

stale/invalid/정확도 상한 초과 소스는 자동 후보에서 제외한다. score는 순위용 값이지 이동 거리나 확률이 아니다. 최소 이동거리와 segment 수는 score와 별도로 검증한다. 일반적인 `recent_movement`는 최근 창 안의 의미 있는 이동이지 순간 속도가 아니다.

한 물리 기기의 샘플 수 또는 같은 그룹의 기기 수를 더해 순위를 올리지 않는다. 그룹 대표 이동 score는 그룹 내 유효 후보의 최댓값을 사용한다.

샘플링 빈도의 영향을 모든 실제 환경에서 완전히 제거한다고 주장하지 않는다. 동일한 합성 경로를 1/5/30초 간격으로 입력하는 회귀 테스트에서 편향이 제한되는지 검증한다.

## 6. 공간 관계와 동행 그룹

### GRP-01. 비교 가능한 관측만 사용

두 기기의 관측은 각각 pair_max_age 이내이고 관측 시각 차이가 pair_max_skew 이내일 때만 GPS 관계를 판정한다. 다른 시각의 오래된 좌표를 현재 위치처럼 비교하지 않는다.

거리 d, 정확도 a/b에 다음 보수적 guard band를 적용한다.

```text
close_evidence     := d + a + b <= together_radius_m
separated_evidence := d - a - b >= separation_radius_m
otherwise          := inconclusive
```

이 계산은 통계적 신뢰구간이 아니다. accuracy가 수백 미터라고 허용 반경을 무제한 늘려 멀리 떨어진 기기들을 같은 그룹에 넣지 않는다. assumed accuracy 관측만으로 close/reunion을 확정하지 않는다.

### GRP-02. 동행과 지속시간

동행 그룹 가입에는 최소 3개의 서로 다른 시각의 비교 가능한 근접 관측이 필요하고, 처음부터 마지막 관측까지 group_confirm_s 이상이어야 한다. 같은 캐시 좌표를 타이머로 반복 평가한 횟수를 독립 관측 수로 세지 않는다. 명확한 분리 증거가 발생하면 해당 관계를 해제하고, 자료 부족은 분리 확정이 아니라 관계 미확인이다.

군집은 소규모 deterministic complete-link 방식으로 구현한다. 그룹 안의 모든 기기 쌍이 근접 조건을 만족해야 한다. A-B와 B-C만 가깝다고 멀리 떨어진 A-C까지 하나로 묶는 단순 connected-component 방식을 사용하지 않는다. 그룹 생성·동률 처리는 안정된 UUID 순서로 고정한다.

활성 기기가 정지해도 active group을 유지한다. 신규 가입에는 실제 근접 증거가 필요하다. BLE-only 부속 기기의 home 동시 감지만으로 외부 GPS 동행을 추정하지 않는다.

## 7. Primary 선택과 세션 연속성

### ARB-01. 평가 순서

입력 정규화 → 이동 특징 → 공간 관계 → 재수렴/분리 → GPS 중재 → 활성 기기의 로컬 증거 선정 → 최종 presence 순서로 평가한다. 먼저 전체 Wi-Fi OR로 home을 결정한 뒤 외출 후보를 배제하는 순환 의존을 만들지 않는다.

GPS mode와 tracking_health는 별도 값이다. GPS 장애 때문에 mode 자체를 무조건 priority로 바꾸지 않는다.

### ARB-02. 초기화 / PRIORITY

초기 후보들이 신뢰할 근접 그룹에 있거나 동일한 Home 위치의 현재 기기별 로컬 증거로 묶일 때, 유효 GPS 후보 중 사용자 우선순위를 따른다. GPS 후보가 하나면 그 후보를 사용할 수 있다.

시작부터 여러 GPS 기기가 서로 다른 장소에서 정지해 있고 연속성 힌트나 이동 증거가 없으면 `ambiguous`로 처리한다. 떨어진 기기 중 우선순위 1번의 위치를 확정된 사람 위치로 간주하지 않는다. 사용자는 수동 선택으로 해소할 수 있다.

### ARB-03. 이동 분리 / DYNAMIC

같은 그룹에서 하나 이상의 기기가 분리되고, 의미 있는 이동 및 우세한 score가 challenger 조건을 충족하면 그 이동 그룹을 활성 그룹으로 선택하고 dynamic으로 전환한다. 초기 active group이 아직 없을 때도, 관측 가능한 그룹 중 하나가 최소 이동 요건과 충분한 우세를 만족하면 초기 동적 선택이 가능하다.

이때 남겨진 기기들은 세션의 separated 기기로 기록한다. 해당 기기의 home 신호는 사용자 재실 증거에서 제외한다. PRIORITY→DYNAMIC 진입은 minimum_primary_hold를 기다리지 않지만 challenger_hold는 충족해야 한다.

### ARB-04. 일반 교체

현재 score를 S, 후보 score를 C라고 할 때:

```text
C > max(S * challenger_ratio, S + challenger_margin)
```

조건을 연속 challenger_hold_s 동안 만족하고 현재 Primary의 minimum_primary_hold_s도 지나야 일반 교체한다. C가 임계값과 같으면 교체하지 않는다. 후보가 바뀌거나 우위가 사라지거나 자격을 잃으면 타이머를 초기화한다. 모든 score가 0이면 기존 Primary를 유지한다.

일반 후보는 현재 동행 그룹 내부이거나, **현재 그룹에서 분리되는 과정이 관측된 후속 그룹**이어야 한다. 사용자가 카페에 전화기를 두고 Watch를 들고 이동하는 경우를 허용해야 한다.

반대로, 현재 사용자와 떨어져 있던 다른 기기가 갑자기 원격지에서 이동한다고 현재 Primary를 빼앗지 않는다. 연속성을 설명할 수 없는 복수 이동 그룹은 기존 추적을 유지하고 `ambiguous` 이유를 표시한다. 기존 추적도 무효이면 추정 유지 기간 뒤 unknown으로 간다. 새 원격 그룹으로 자동 순간이동하지 않는다.

### ARB-05. 정지, 장애, 수동 선택의 우선순위

외부에서 정지해 이동 창의 모든 score가 0이 되어도 active group과 Primary의 정체성을 유지한다. fresh stationary GPS는 유효한 위치 소스다.

Primary가 stale/invalid일 때는 최근 safe_companion_age_s 이내에 동행이 확인되고, 현재 fresh이며, 반대되는 분리 증거가 없는 같은 그룹의 후보로 failover할 수 있다. 장애 failover는 일반 hold를 우회할 수 있고 이유를 남긴다. 분리된 집의 GPS, 가장 최근에 업데이트된 아무 GPS, 전역 우선순위 다음 기기로는 fallback하지 않는다.

안전한 대안이 없으면 선택된 Primary ID를 진단용으로 보존하되 유효 GPS 위치 공급자는 없는 것으로 처리한다. 최종 presence의 한정적 유지 및 unknown 전환은 9절을 따른다.

## 8. 재수렴과 우선순위 복귀

### REU-01. 대상

재수렴은 현재 active group과 이번 추적에서 분리된 것으로 관측한 기기 또는 그룹이 다시 만나는 사건이다. 단순히 “동행 중인 iPhone과 Watch가 계속 가깝다”는 재수렴이 아니다.

모든 등록 기기가 모일 필요는 없다. GPS가 꺼진 iPad나 다른 장소에 둔 기기 때문에 재수렴을 영원히 막지 않는다. 재수렴 확정 시 현재 실제로 함께 있다고 증명된 후보 집합에서만 우선순위를 적용한다.

### REU-02. 두 가지 허용 경로

GPS 경로: 양쪽의 신뢰 가능한 최신 관측이 close_evidence를 충족하며 reunion_hold_s 동안 유지되어야 한다. 재수렴 timer는 최초 유효 close 관측에서 시작하며, 일반적인 group_confirm이 끝난 뒤 다시 reunion_hold를 추가하는 방식으로 중복 대기하지 않는다. 확정에는 최소 3회의 서로 다른 시각의 관측 비교를 요구한다. 실제 관측이 기간에 걸쳐 갱신되어야 하며, 한 번의 근접 좌표를 오래 재사용해 확정하지 않는다. 집 외의 장소에서도 동작해야 한다.

Home 로컬 경로: **외출 중이던 active 기기 자체**의 유효 Wi-Fi/BLE home 신호가 새로 돌아오고, 다른 separated 기기도 현재 Home 로컬 증거가 있어야 한다. active 기기에서 이전 absent→present 전이가 관측되었거나 fresh GPS의 실제 귀환 증거가 필요하다. 집에 남은 iPad의 계속 켜진 Wi-Fi만으로 재수렴시키지 않는다. TTL 없는 신호의 한계와 GPS와의 강한 모순도 확인한다.

Home 로컬 경로는 실내 GPS가 stale인 상황에서도 재수렴 관계를 확인할 수 있지만, stale GPS를 Primary 좌표 공급자로 승격시키지는 않는다. 우선순위 1번의 GPS가 아직 stale이면 현재 유효 후보를 사용하고, 그 GPS가 유효해진 뒤 같은 장소임을 다시 확인하여 우선순위를 적용한다.

### REU-03. 확정 후

재수렴 확정 시 priority mode로 돌아가고, 재결합한 유효 후보 중 우선순위가 가장 높은 기기를 선택한다. 원격지의 더 높은 우선순위 기기는 후보가 아니다.

새 세션 기준 시각을 기록한다. 귀가 전의 큰 이동 score 때문에 곧바로 dynamic으로 되돌아가지 않도록 **재수렴 후의 새로운 분리·이동 증거**를 요구한다. 진단용 최근 N분 이동량은 유지할 수 있지만, 새 세션의 탈출 판단에는 이전 세션 이동을 재사용하지 않는다.

## 9. Presence / Room Fusion

### FUS-01. 사람에 사용할 로컬 증거의 범위

기기별 로컬 신호를 먼저 계산한 후 active group에 속한 기기의 신호만 사람의 home 근거로 사용한다. 귀환을 확인하는 현재 active 기기의 로컬 신호는 GPS stale 중에도 사용할 수 있다. separated 기기는 active 기기와 재결합한 것이 확인되기 전까지 home/room 판정에 참여하지 않는다.

초기 home 그룹은 각 기기의 현재 로컬 상태로 구성할 수 있으나, fresh한 GPS가 해당 기기를 명백히 멀리 표시하면 모순으로 처리한다. 집에 있다고 판단한 사전 결과로 GPS 분리 판단을 막지 않는다.

여러 활성 소스의 ANY는 허용하되, 소스는 해당 물리 기기에 매핑되고 현재 이용 가능한 home 전용 증거여야 한다. RSSI를 거리나 실내 존재의 절대적 증명으로 취급하지 않는다. Wi-Fi/BLE 범위가 집 밖까지 닿을 수 있다는 한계를 문서화한다.

### FUS-02. HOME 진입 및 강한 모순

적격한 로컬 home 신호가 home_enter_hold_s 동안 유지되면 home으로 진입한다. GPS의 작은 Home 경계 오차는 이를 막지 않는다.

같은 활성 기기의 fresh GPS가 정확도 여유를 제외하고도 Home 중심에서 `max(500m, home_radius + home_exit_margin)` 이상 떨어져 있고, 새 로컬 home 증거와 동시에 반복적으로 충돌하면 한쪽을 무조건 승자로 만들지 않는다. health를 ambiguous로 두고 `conflicting_local_and_gps`를 기록한다. 마지막 확정 presence는 evidence_hold_s까지만 유지하고 해결되지 않으면 unknown이다. 같은 모순 입력의 반복으로 유지 기간을 연장하지 않는다.

### FUS-03. GPS-only 지원 및 HOME 이탈

로컬 소스가 미설정인 경우 GPS-only로 정상 동작해야 한다. 알려진 accuracy를 가진 유효 GPS에서 `distance + accuracy <= home_radius`가 gps_home_enter_hold_s 유지되면 home으로 판단한다.

유효 GPS가 `distance - accuracy >= home_radius + home_exit_margin`를 home_exit_hold_s 동안 만족하고 적격한 로컬 positive 증거가 없으면 home에서 이탈한다. 로컬 unknown은 absent로 변환하지 않지만, 지속적인 유효 GPS 외부 증거 자체로 이탈 판정은 가능하다. 이 경우 이유에 `gps_outside_local_unknown`을 남기고 health를 degraded로 표시한다. 유효 로컬 positive가 있으면 작은 GPS 경계 이탈보다 로컬 신호를 우선하고, FUS-02의 큰 거리 조건을 충족할 때는 모순 처리를 따른다. 이 로컬 우선 정책은 Wi-Fi/BLE 수신 범위가 실제 집 경계와 같다는 보장이 아니다.

이탈 후 목적 상태는 거리/방향에 따라 nearby/arriving/away다. home에서 벗어났다는 이유로 무조건 away를 거치지 않는다.

### FUS-04. Nearby / Arriving / Direction

거리는 Home 중심까지의 미터 값이며 zone 경계까지의 거리가 아니다. Home이 아닌 상태에서 `distance <= nearby_enter`이면 nearby 범위에 진입하고, 이미 그 범위에 있으면 `distance > nearby_exit`에서 이탈한다. 두 값 사이에서는 기존 범위 소속을 유지한다. 초기 상태의 거리가 두 값 사이이고 이전 범위 소속이 없다면 범위 밖으로 간주한다.

방향은 동일 GPS 소스의 끊김 없는 최근 경로에서 최소 3개 관측, direction_min_span 이상의 기간을 요구한다. 처음-끝 Home 거리 차이가 `max(direction_min_change_m, first_accuracy + last_accuracy)`를 넘고, 시간에 대한 거리 선형 회귀 기울기가 -0.3m/s보다 작으면 towards, +0.3m/s보다 크면 away_from으로 판정한다. 정확도 내 거리 변화만 있고 충분한 자료가 있으면 stationary, 그 외 불충분한 경우 None이다. 기울기 threshold는 고급 설정 가능하게 한다.

nearby 범위이면서 towards가 유효할 때 arriving을 사용한다. Primary 교체, 긴 관측 공백, 재획득에서는 방향 기준을 초기화하고 새 소스의 충분한 경로가 쌓일 때까지 방향 unknown으로 둔다. 서로 다른 기기의 마지막 좌표를 이어 허위 귀가 방향을 만들지 않는다.

### FUS-05. 근거 소실

모든 적격 증거가 사라지거나 무효가 되면 마지막 확정 presence를 evidence_hold_s까지만 보존하고 `held=true` 및 소실 시작 시각을 표시한다. 소실 시작 시각은 연속 소실 구간 최초 한 번만 기록한다. 같은 오래된 상태의 재평가로 만료를 미루지 않는다.

유지 기간 이후 presence와 home binary sensor는 unknown이어야 한다. 추적 정보 소실은 away 또는 binary_sensor off가 아니다. 유효한 적격 Wi-Fi/BLE 증거가 계속 있으면 GPS 소실과 무관하게 home을 유지할 수 있다.

### FUS-06. Room

room은 presence와 분리한다. `presence=home, room=bedroom`처럼 표현한다. room 문자열을 presence enum이나 device_tracker state에 혼합하지 않는다.

room 입력에는 원본의 `not_home`을 absent로, `unknown`/`unavailable`을 모름으로 처리하는 명시적 매핑을 둔다. room 이름은 하드코딩하지 않고 선택적인 이름 매핑을 지원한다. 해당 기기의 room이 유효하면 home 전용 로컬 증거로 사용할 수 있지만 원본이 실제 timeout을 관리해야 한다.

기본 room 소스는 유효한 active Primary 기기의 room이고, 없으면 active group 안의 사용자 우선순위에 따른 소스를 사용한다. 동일 기기 내부의 room 변화에 room_change_hold_s를 적용한다. 주 기기 room이 unavailable이면 room_missing_hold_s 이후 active 대체 소스를 평가한다. 서로 다른 active 기기가 다른 방을 표시하는 경우 room_source와 모순을 진단하고 사람의 실제 방이라고 단정하지 않는다.

presence가 non-home이면 room은 None으로 즉시 해제한다. home이더라도 모든 적격 room 근거가 사라지면 room_missing_hold_s 이후 None이다. 집에 남은 기기의 room은 외출 중인 사람의 room으로 선택할 수 없다.

## 10. Home Assistant 출력 계약

사람당 기본 출력은 아래와 같다. 실제 entity_id는 사용자가 바꿀 수 있으며 예시 이름을 강제하지 않는다. `unique_id`는 Config Entry의 불변 식별자와 고정 엔티티 키로 만든다.

| 엔티티 키 | 플랫폼 / 의미 |
|---|---|
| gps | `device_tracker`: 현재 유효 Primary GPS의 실제 좌표와 정확도 |
| presence | `sensor`: home / nearby / arriving / away; 데이터 없으면 HA unknown |
| home | `binary_sensor`: home=True, 알려진 non-home=False, 불명=None |
| room | `sensor`: room 문자열 또는 None; 미설정 시 생성 생략 가능 |
| primary_gps | `sensor`: 선택된 소스 entity_id; 속성에 tracked_device_id, reason |
| gps_mode | `sensor`: priority / dynamic / manual |
| tracking_health | `sensor`: ok / degraded / ambiguous / no_data |
| distance_home | `sensor`: 미터 단위 거리 또는 None |
| direction | `sensor`: towards / away_from / stationary 또는 None |

`recent_movement` 및 기기별 상세 score는 기본 비활성 diagnostic 엔티티 또는 diagnostics에 둔다. 전체 history와 커다란 devices JSON을 매번 `extra_state_attributes`에 넣지 않는다.

TrackerEntity는 대상 HA 버전의 정상적인 GPS/zone 계약을 따른다. home/not_home/zone은 HA가 결정하게 하고 nearby/arriving/bedroom을 tracker state로 강제하지 않는다. GPS가 stale이면 GPS tracker를 unavailable로 표시하고 최신 좌표인 척 계속 갱신하지 않는다. 마지막 좌표가 필요하면 메모리의 last-known 데이터로만 구분 보관한다. presence의 유예 유지와 GPS tracker의 availability는 독립적이다.

로컬 evidence로 presence=home이면서 GPS zone은 일시적으로 not_home일 수 있다. 이를 숨기기 위해 GPS 좌표를 집 중심으로 덮어쓰지 않는다. README에는 자동화가 presence/home 엔티티를 사용하고, person/map은 GPS 출력을 사용한다는 차이를 설명한다. `person` 구성을 자동 수정하지 않는다. 원본 기기들을 person에 함께 연결하면 HA의 별도 tracker 선택 규칙이 적용될 수 있음을 설명한다.

센서가 정상 작동하면서 위치만 모르면 None을 사용한다. 엔진/입력 공급 기능 자체의 장애로 출력할 수 없는 경우의 unavailable과 구분한다. binary sensor unknown은 off가 되어서는 안 된다.

## 11. HA 통합 구조와 생명주기

추천 구조는 다음과 같다. 필요 없이 파일 수를 늘리지 말고, 핵심 엔진과 HA 접착 코드는 반드시 분리한다.

```text
custom_components/presence_fusion/
  __init__.py
  manifest.json
  const.py
  config_flow.py
  coordinator.py
  models.py
  adapters.py
  engine/
    movement.py
    grouping.py
    arbitration.py
    fusion.py
  sensor.py
  binary_sensor.py
  device_tracker.py
  diagnostics.py
  services.yaml
  strings.json
  translations/en.json
  translations/ko.json
tests/
  conftest.py
  unit/
  integration/
  fixtures/
docs/
  PRESENCE_FUSION_SPEC.md
  DECISIONS.md
  TEST_MATRIX.md
  TEST_REPORT.md
README.md
pyproject.toml
```

파일 이름 자체보다 책임 분리를 우선한다. 순수 엔진은 HA 객체, 실시간 시스템 시계, 네트워크, recorder DB에 의존하지 않는다. 관측 입력과 명시적인 시간으로 상태를 계산한다. UTC 시각과 debounce용 monotonic 시각을 주입 가능한 Clock으로 분리한다. 동일 입력/시간이면 동일 결과를 내야 한다.

Config Entry 런타임 데이터는 지원 버전의 `ConfigEntry.runtime_data`를 typed하게 사용한다. push 형태의 DataUpdateCoordinator 또는 동등한 HA 패턴을 사용하고, 실제 데이터가 없는 가짜 네트워크 polling을 구현하지 않는다. 엔티티 프로퍼티는 캐시만 읽는다.

등록된 입력에 한정된 `async_track_state_change_event` 계열 helper를 사용하고 attribute-only 위치 업데이트도 처리한다. 전체 HA state_changed 이벤트를 무차별 스캔하지 않는다. 입력 읽기는 초기 snapshot과 구독을 레이스 없이 구성한다.

상태 변화 외에도 만료 deadline 또는 주기적 maintenance timer가 필요하다. 최대 5초 지연 이내에 시간 기반 조건을 재평가하되, 정확한 경계 단위 테스트는 순수 엔진에서 수행한다. stale 재평가와 이벤트 발생을 새 GPS 관측으로 처리하지 않는다. 결과 snapshot의 의미 있는 값이 바뀔 때만 엔티티를 갱신한다. 매초 변하는 age/countdown을 기본 attributes로 발행하지 않는다.

한 Entry의 입력 처리와 타이머 처리를 직렬화하여 중첩 계산·오래된 결과 덮어쓰기를 막는다. unload 시 소스/registry/zone 리스너, 타이머, 예약된 저장 및 작업을 해제한다. setup 부분 실패도 이미 만든 자원을 정리한다. 다른 Entry의 서비스나 리스너를 제거하지 않는다.

Config Flow는 이름, 물리 기기 추가/편집/삭제, 소스 매핑, 우선순위, 옵션을 지원한다. 아직 unavailable인 기존 엔티티를 선택한 것만으로 설정을 거부하지 않는다. 없는 entity_id, 중복 매핑, 잘못된 타입, 이 통합의 출력, person 입력은 검증한다. 사용 가능한 엔티티가 아직 올라오지 않은 시작 상황에서 통합 전체가 영구 실패하지 않아야 한다.

Options Flow에서 실제 동작에 반영되지 않는 장식용 옵션을 만들지 않는다. 변경 후 reload 또는 안전한 재구독으로 즉시 반영하고 중복 엔티티/리스너를 만들지 않는다. 소스를 삭제하거나 GPS 후보를 끄면 관련 후보와 타이머를 정리한다. 이름 변경으로 unique_id가 바뀌면 안 된다.

manifest의 config_flow/version/분류 등을 대상 HA 규칙에 맞게 검증한다. MQTT/UniFi/Bluetooth가 설치되어야만 setup되는 필수 dependency를 만들지 않는다. HACS 배포는 선택 사항이며, 실제 저장소 URL·소유자 정보를 모르면 가짜 값을 작성하거나 공식 등록되었다고 주장하지 않는다.

## 12. 재시작, 보관, 진단 및 개인정보

기본 GPS 경로 history는 메모리에만 보관하며 제한된 deque/버킷/segment 자료구조를 사용한다. 이동 창과 경계용 데이터 외에 무제한 샘플을 보관하지 않는다. 대상은 사람당 최대 16개 물리 기기로 제한하고 UI에 표시한다. 제한 초과는 명시적 오류다.

재시작 후 동적 추적 연속성을 위해 최소 메타데이터만 HA의 비동기 저장 helper로 저장한다. 예: Primary 기기 UUID, active/separated UUID, mode, 세션 기준 시각, 마지막 확정 presence와 원래 시각, override의 원래 만료 시각. 이 런타임 복원 Store에는 정밀 좌표 history, MAC, SSID, IRK, 인증정보를 저장하지 않는다. 사용자 입력의 SSID 비교값과 entity 매핑은 기능 설정이므로 HA Config Entry에 로컬 저장될 수 있으며, 이를 별도의 관측 history 저장과 구분하여 개인정보 설명에 명시한다. 저장은 주요 전이 시 debounce하고 매 GPS 이벤트마다 디스크에 쓰지 않는다.

복원 메타데이터는 힌트이며 관측 증거가 아니다. pending challenger/reunion/debounce는 재확인한다. 복원 시간으로 만료를 연장하지 않는다. 새 GPS가 들어올 때 과거 hint와 좌표 사이에 가상의 이동거리를 만들지 않는다. 저장 스키마 버전을 두고 손상/만료/알 수 없는 버전은 안전한 cold start 또는 명시적 오류로 처리한다. 제거 시 Entry별 저장 데이터도 제거한다.

진단은 HA 다운로드 diagnostics로 제공하고, 제한된 reason history와 source 상태/score/제외 이유/데이터 품질을 보여준다. 기본 로그는 전이 또는 문제 발생 시만 출력하고 raw observation마다 로그를 남기지 않는다.

다운로드 diagnostics에서는 좌표, Home 위치, 원본 entity/person 이름, SSID, MAC, BLE 식별자, 실제 room 이름 및 기타 개인 식별 정보를 제거 또는 가명화한다. nested config와 예외 메시지에도 동일 규칙을 적용한다. 거리/이동 패턴 등의 상세 자료도 기본 공유 진단에서는 생략하거나 범주화한다. 민감한 일반 엔티티 attributes를 그대로 덤프하지 않는다. 사용자 UI가 의도적으로 표시하는 현재 위치와 외부에 공유할 진단 데이터의 정책을 분리한다.

통합 자체는 외부 네트워크 통신을 하지 않는다. 상위 GPS 통합이 클라우드를 쓰는지와 별개다. 또한 이 통합이 자체 좌표 history를 디스크에 저장하지 않아도 HA Recorder는 입력/출력 엔티티의 위치 상태를 기록할 수 있다. README에 이 구분과 사용자가 Recorder 보관·제외 정책을 확인해야 한다는 점을 명시한다. 통합이 기존 Recorder 설정을 자동 수정하지 않는다. 실사용 GPS 자료를 테스트 저장소에 넣지 않고 합성 fixture를 사용한다.

## 13. 수동 제어와 이유 코드

`presence_fusion.set_primary`와 `presence_fusion.clear_primary_override` action을 제공한다. 대상 Config Entry, 그 Entry 소속 tracked_device UUID, 유지시간을 명확하게 검증한다. 기본 유지시간은 3600초, 허용 범위는 1~86400초다.

수동 선택은 사람이 불확실성을 해소하는 수단이다. 유효한 수동 선택은 해당 기기를 추적 기준으로 설정하며, active group도 해당 기기와 실제 동행이 확인된 기기들로 재구성한다. 과거 active group의 원격 로컬 증거를 그대로 합치지 않는다. 사용자의 명시적인 선택에 한해서 자동 연속성 제약을 해소할 수 있다. 선택 시 사용 불가능한 GPS 후보는 읽기 쉬운 검증 오류를 반환한다. 수동 선택 중 source가 stale이 되어도 좌표를 조작하거나 다른 기기로 몰래 바꾸지 않는다. GPS unavailable 및 presence 유예 규칙을 적용한다. 만료/해제 시 현재 추적의 실제 관측으로 자동 모드를 재평가하고, 과거 pending challenger 타이머를 재사용하지 않는다. override 이전의 Primary를 근거 없이 복원하지 않는다. 재시작 후에도 원래 만료 시각을 연장하지 않는다.

수동 명령은 소스만 선택하며 사용자를 강제로 home/away로 만드는 action은 v1에 없다.

기계적으로 검증 가능한 reason code를 정의한다. 최소한 아래 상황을 구분한다.

```text
initial_priority
initial_ambiguous
movement_departure
movement_challenger
retained_stationary
safe_companion_failover
no_safe_fallback
reunion_priority
conflicting_clusters
conflicting_local_and_gps
local_home_evidence
gps_home_evidence
gps_outside_local_unknown
held_missing_evidence
evidence_expired
manual_override
```

유효하지 않은 관측의 rejection reason도 별도로 관리한다. 매 update마다 마지막 전이 이유를 일반적인 `evaluated`로 덮어쓰지 않는다.

## 14. 필수 테스트 매트릭스

테스트에는 가짜 시간과 합성 관측을 사용한다. 실제 sleep, 실제 GPS/MQTT/네트워크, 시간대에 따라 달라지는 값을 사용하지 않는다. 이동 지리 좌표 fixture는 검증된 테스트용 변환기로 만들고 최종 기대값을 production 판정 함수를 호출해서 생성하지 않는다.

아래 시나리오를 각각 식별 가능한 테스트 또는 parameterized test case로 구현한다. 표의 ID를 `docs/TEST_MATRIX.md`에서 실제 테스트 함수·관련 요구사항·구현 모듈과 연결한다. 시험 과정에서 GPS를 fresh하게 유지한다고 쓴 시나리오는 실제로 새 timestamp의 입력을 공급한다.

| ID | 입력 / 사전 조건 | 기대 결과 |
|---|---|---|
| T01 | 서로 함께 있는 fresh A/B/C, priority A>B>C, 의미 있는 이동 없음 | 확인 기간 이후 A, mode=priority. 기기 등록 순서를 바꿔도 동일. |
| T02 | 함께 있던 A=iPhone, B=Watch, C=iPad 중 B만 연속 1km 이동. A/C Wi-Fi와 BLE는 계속 home | B가 challenger 조건을 만족하면 dynamic Primary. A/C home 신호가 B의 외출을 막지 않음. |
| T03 | T02의 B가 외부에서 20분 정지. B는 동일 좌표·새 timestamp를 계속 제공 | 이동 창 만료 후에도 B 유지. C로 우선순위 복귀하지 않음. |
| T04 | Primary score 600, 같은 그룹 후보 620, 충분한 hold 경과 | 후보가 교체되지 않음. |
| T05 | Primary 600, 후보 850, minimum hold 이미 충족. t=0부터 우위 유지 | t=44초 교체 없음, t=45초 교체. 정확한 threshold와 동일 score는 교체 없음도 검증. |
| T06 | 후보 우위가 t=30에 끊기고 t=35에 복구하거나 후보가 다른 기기로 바뀜 | 이전 30초를 재사용하지 않고 후보 타이머 새로 시작. |
| T07 | 밖에서 동행이 확인된 A/B 중 A가 stale, B fresh, 최근 동행 증거 유효 | 같은 그룹 B로 장애 failover. 일반 hold를 우회하고 reason 기록. |
| T08 | 밖의 A stale. 집의 B만 fresh이며 Wi-Fi home, 동행 증거 없음 | B로 fallback 금지. GPS tracker unavailable, presence 한정 유지 후 unknown. |
| T09 | active A와 separated B가 재결합. C는 꺼짐. GPS가 0/60/120초에 새로 근접 확인 | 119초까지 dynamic, 120초에 재결합한 A/B 중 priority 선택. C는 재수렴을 막지 않음. |
| T10 | 외출 내내 active A/B는 가까움. separated C는 계속 집 | A/B의 근접만으로 priority 복귀하지 않음. |
| T11 | 집이 아닌 장소에서 active B가 separated A와 fresh GPS로 재결합 | 재수렴 후 그 장소의 A/B에 대해서만 priority 적용. |
| T12 | 집의 C Wi-Fi가 이미 on. 밖의 B GPS stale, B 로컬 귀환 증거 없음 | 재수렴 및 home 진입 없음. 이후 B 자신의 absent→present 귀환과 C home이 유지되면 재수렴 가능. |
| T13 | 재수렴 직후 B의 지난 10분 이동량이 A보다 큼 | 새 분리 이동이 없으면 priority 유지. 과거 score로 dynamic 재진입 금지. |
| T14 | 동행 active A/B 중 A는 카페에 남고 B가 새로 분리되어 이동 | 관측된 그룹 분리 연속성으로 B 선택 가능. 처음부터 원격지였던 C의 이동은 Primary 강탈 불가. |
| T15 | cold start에서 서로 먼 A/B가 모두 정지, 이전 힌트 없음 | 초기 ambiguous, 위치 확정 금지. 수동 선택 또는 명확한 새 이동 증거로 해소. |
| T16 | 정확도 20m, 한 점 주변 10m 이내의 jitter를 고빈도로 입력 | minimum_movement를 넘지 않고 허위 departure 없음. |
| T17 | accuracy 10m, 5초마다 3m씩 같은 방향으로 5분 이동 | 작은 각 step을 제거하더라도 누적 이동 후보가 인식됨. 결과가 0이 아님. |
| T18 | 관측된 A→B→A 왕복, 각 편도 300m, 모든 전환점이 버킷에 포함 | 경로 길이는 대략 600m, displacement 대략 0m. 사전 명시 오차 내 비교. |
| T19 | 동일 1km 직선 경로·동일 시작/끝을 1/5/30초 간격으로 입력 | 정상화 이동량 차이는 이 통제 fixture에서 max(30m, 10%) 이내. 샘플 수로 순위 편향하지 않음. |
| T20 | 정상 위치 중 한 번만 10km/10초 점프 후 원위치, accuracy 10m | 점프가 경로·Primary·방향에 반영되지 않고 정상 anchor 유지. |
| T21 | 관측 공백이 max_gap보다 길고 다른 장소에서 일관된 GPS 재획득 | 공백 이동거리 집계 없음, 새 위치는 재획득 후 사용 가능, 영구 점프 거부 없음. |
| T22 | 위경도 0, NaN, Infinity, 범위 밖, accuracy 0/누락/상한 초과 | 각각 명시된 정책대로 처리. 0도 좌표를 누락으로 보지 않고 accuracy 0을 완벽한 정확도로 보지 않음. |
| T23 | duplicate, out-of-order, 동일 시각 충돌, 과도한 미래 시각 입력 | history/freshness/후보 지속시간이 잘못 늘어나지 않음. rejection reason 검증. |
| T24 | GPS 좌표 변화 없이 배터리 속성만 10초마다 변경 | GPS freshness 연장 없음. stale 경계에서 정상 만료. |
| T25 | 같은 좌표에 새로운 신뢰 가능한 GPS 측정 timestamp를 지속 공급 | freshness 유지, 이동량 증가 없음. |
| T26 | 두 GPS의 측정 시각 차이가 pair_max_skew 초과 또는 accuracy 불량 | 동행/재수렴 확정하지 않음. 큰 accuracy로 근접 반경을 무제한 확장하지 않음. |
| T27 | A-B, B-C는 close지만 A-C는 close 아님 | 세 기기를 하나의 complete-link 그룹으로 묶지 않음. |
| T28 | 로컬 입력 미설정, 하나의 fresh GPS가 Home→외부→Home 이동 | GPS-only 진입/이탈 debounce 및 nearby/arriving 작동. 미설정 Wi-Fi/BLE가 차단 조건이 아님. |
| T29 | Wi-Fi 10초 로밍 단절, BLE 또는 GPS는 유효한 home 근거 | 불필요한 away 전환 없음. 단절/미가용을 같은 것으로 처리하지 않음. |
| T30 | 모든 소스 unknown/unavailable, 이후 이벤트도 전혀 없음 | maintenance timer만으로 유예 만료 후 presence/home binary unknown. off/away 금지. |
| T31 | GPS만 모두 stale이나 active 기기의 유효 로컬 home 신호 지속 | presence/home은 home 유지, GPS tracker만 unavailable. |
| T32 | active 같은 기기의 새 로컬 home과 매우 먼 fresh GPS가 반복 충돌 | reason=conflicting_local_and_gps, health ambiguous. 마지막 상태 무한 유지 금지. |
| T33 | Home 거리 900→700→500m, 60초 이상 및 3개 이상 유효 관측 | towards/arriving. 1000/1200m 경계 왕복 jitter에는 hysteresis 작동. |
| T34 | Primary A의 마지막 위치와 신규 B 위치가 집 방향으로 크게 다름 | 교체 순간 허위 arriving 없음. B의 새 연속 경로가 쌓인 후 방향 판단. |
| T35 | room이 4초마다 bedroom/livingroom 변경, 이후 livingroom 8초 유지 | 유지 조건 전에는 바뀌지 않고 조건 충족 후 변경. non-home이면 즉시 None. |
| T36 | 외출 active B의 room 없음, 집에 남은 A의 room=bedroom | 사용자 room이 bedroom으로 채워지지 않음. room source 소실 TTL도 검증. |
| T37 | rolling window 경계를 가로지르는 segment와 오래된 anchor, 연속된 느린 관측 | 경계 기여 및 공백 구분이 문서대로. 창 밖 이동 잔류나 느린 이동 소실 없음. |
| T38 | Config/Options Flow: 추가·편집·삭제·priority 중복·숫자 오류·자기 출력 입력 | 유효 설정은 반영되고 오류는 필드별 반환. 출력 entity unique_id 불변. |
| T39 | 입력 entity rename/remove/disable/unknown 상태 및 attribute-only GPS 변경 | 이름 변경 추적, 소실 처리, 속성 위치 갱신 작동. 자동화 이름 의존 없음. |
| T40 | Entry 두 개 동시 실행, 소스가 각각 이동, 하나만 unload | 다른 Entry의 상태·서비스·리스너·타이머 영향 없음. |
| T41 | setup→reload→unload 반복 및 setup 도중 예외 | 중복 리스너/엔티티/예약 작업 없음. unload 후 상태 이벤트로 출력 갱신되지 않음. |
| T42 | dynamic 외출 중 재시작, 원래 시각의 복원 데이터, 이어지는 fresh GPS | 가상의 이동·허위 귀가·원격 fallback 없음. pending 타이머는 새로 검증. |
| T43 | 손상/알 수 없는 storage 버전, 원래 유예시간 만료 후 재시작 | 안전한 초기화/명시적 오류. 부팅 시점으로 유예기간 연장 금지. |
| T44 | set_primary: 다른 Entry 기기/비후보/stale/잘못된 duration. 정상 선택 후 stale/만료/해제 | 잘못된 요청은 상태 변경 없이 오류. 정상 override와 원래 TTL 준수, 몰래 failover하지 않음. |
| T45 | Home zone 이동/반경 변경 및 서로 다른 HA timezone | 거리·경계 재계산, UTC 기반 창/monotonic debounce 유지. 잘못된 반경 관계 진단. |
| T46 | 출력 GPS/Home sensor, GPS stale, local-only home, known non-home | 표준 tracker state/availability와 presence/home unknown 의미가 계약대로. 좌표 스냅 없음. |
| T47 | 전체 diagnostics/logs를 합성 민감값으로 검사 | 좌표·Home·SSID·MAC·IRK·실명·실제 room 및 nested 원본 정보가 공유 진단에 노출되지 않음. |
| T48 | 8기기 × 1Hz × 가상 1시간 입력, 창 만료, 결과 불변 상태 반복 | history/버킷/예약 작업이 설정한 상한 안에 있음. 무의미한 HA state write 폭증 없음. 절대 메모리/시간 수치는 측정한 경우만 보고. |

통합 테스트는 이벤트 수신부터 최종 HA 엔티티까지 연결하여 최소 T02/T08/T09/T24/T28/T30/T31/T38~T47의 핵심 분기를 검증한다. 순수 함수 테스트만으로 HA Config Flow·lifecycle 검증을 대체하지 않는다.

테스트 순서나 랜덤 seed에 따라 결과가 달라지지 않게 한다. 시간 경계에는 직전/정확히 임계값/직후를 포함한다. 기하 거리 비교에는 단위를 명시한 합리적 부동소수점 오차만 허용한다. `skip`, `xfail`, mock으로 핵심 판정 함수를 우회해 테스트를 통과시키지 않는다.

## 15. 산출물과 완료 기준

필수 산출물은 실제 통합 코드, Config/Options UI 및 한영 번역, 단위/통합 테스트와 합성 fixture, 실행 가능한 개발 의존성 및 검사 명령, README, 결정 기록, 요구사항별 테스트 매트릭스, 실제 테스트 실행 보고서다.

README에는 설치·삭제·업데이트·reload, 기기별 소스 매핑, GPS timestamp 제한, 초기 ambiguous 해소, Primary 우선순위와 재수렴, GPS-only 사용, person/map과 presence/home의 차이, 수동 override, 개인정보 처리, 알려진 한계 및 트러블슈팅을 설명한다. 예시 자동화는 알림처럼 저위험 예시만 제공하고 unknown을 away로 처리하지 않게 한다.

실제 환경에 맞는 단일 테스트 명령을 제공한다. pytest 및 HA 호환 fixture 도구, lint/format, 필요한 정적 타입 검사의 버전을 충돌 없이 고정한다. 새 저장소라면 해당 명령을 실행하는 CI도 추가한다. 코드 커버리지는 순수 엔진 branch coverage 90% 이상을 목표로 하되 핵심 INV 시나리오는 수치와 무관하게 모두 검증한다. 목표 미달이나 실행 불가를 숨기지 않는다.

완료 판정은 다음 모두를 충족해야 한다.

- 필수 기능의 TODO/stub/NotImplementedError가 없다.
- Config Flow로 추가하고 실제 platform 엔티티를 생성할 수 있다.
- 필수 INV와 T01~T48이 구현 및 실제 테스트에 추적 가능하다.
- 실행한 테스트·lint·타입 검사 결과와 실패/미실행 항목이 구분되어 있다.
- 이벤트 루프 blocking, unload 누수, 임의 원격 fallback, left-behind home 고착이 없다.
- history/진단 상태 데이터가 제한되고 개인 위치 정보가 공유 진단에 노출되지 않는다.
- 호환성은 실제 검증한 버전 범위로만 주장한다.

최종 Codex 응답에는 변경 파일 요약, 핵심 설계 선택, 지원/테스트 HA·Python 버전, 실행한 명령과 실제 결과, 테스트 매트릭스 위치, 설치/초기 설정 방법, 남은 제한을 포함한다. 테스트 실행과 실장비 검증은 별개다. 실제 장비로 검증하지 않았다면 그렇게 명시한다. 테스트를 실행할 수 없었던 경우는 “구현 완료 및 검증 완료”라고 보고하지 않는다.

## 16. 공식 문서 참고

다음은 설계 검토 시 확인한 공식 문서다. 실제 구현 시 선택한 HA 버전에 맞는 문서와 설치된 코드를 다시 확인한다. 이 링크의 특정 API가 모든 과거 버전에 존재한다고 가정하지 않는다.

```text
Home Assistant state timestamps:
https://www.home-assistant.io/docs/configuration/state_object/

Home Assistant device tracker entity:
https://developers.home-assistant.io/docs/core/entity/device-tracker/

Home Assistant event helpers:
https://developers.home-assistant.io/docs/integration_listen_events/

Home Assistant runtime_data:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/

Home Assistant push coordinator:
https://developers.home-assistant.io/docs/integration_fetching_data/

Home Assistant Config Flow:
https://developers.home-assistant.io/docs/core/integration/config_flow/

Home Assistant unload:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-entry-unloading/

Home Assistant diagnostics/privacy:
https://developers.home-assistant.io/docs/core/integration/diagnostics/

Home Assistant entity attributes/recorder considerations:
https://developers.home-assistant.io/docs/core/entity/

Home Assistant Person:
https://www.home-assistant.io/integrations/person/

OpenAI Codex AGENTS.md:
https://developers.openai.com/codex/guides/agents-md/
```
