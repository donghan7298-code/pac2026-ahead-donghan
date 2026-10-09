# 5-③~⑥ 연결 계약

기준은 [공통 v0.2 원문](common_development_standard.md)입니다.
기존 클래스의 필드 이름과 필수 함수 인자는 유지했습니다. 다음 보충사항은 **최초 PR에서 팀 확인이 필요한 추가 계약**입니다.

## 1. 좌표 의미: 이 구현의 명시적 약속

길이는 m, 질량은 kg, 힘은 N, 각도는 rad입니다. `target_pose.frame_id == "pallet"`입니다.
팔레트 원점은 사용 가능한 적재 면의 한쪽 아래 모서리이고, `z=0`은 팔레트 적재 면입니다.
`PalletState.size.z`는 목재 자체 두께가 아니라 최대 적재 높이입니다.

이 구현에서 `target_pose.xyz`는 **회전 후 박스 AABB의 최소 x/y/z 모서리**입니다.
`yaw`로 x/y 치수를 바꾸되 상하를 유지합니다. 후보 yaw는 박스의 `allowed_yaws_rad`와 일치해야 합니다.
박스 중심 좌표를 기대하는 로봇/비전 모듈에는 `pac_planning.geometry.center_pose(box, candidate)`를 사용하세요.
이 변환은 TF 변환이 아닙니다. `pallet → robot_base` 변환과 TCP/그리퍼 offset은 로봇 담당자가 적용합니다.

기본 v0.2는 Pose 원점이 모서리인지 중심인지 명시하지 않았기 때문에 이 항목을 숨기지 않고 리뷰 대상으로 둡니다.

## 2. 기본 공통 모델 + PlanningContext

알고리즘은 `pac_common` dataclass를 사용합니다. JSON은 fixture/로그, YAML은 설정에서만 사용합니다.
`InventoryState`의 dict는 입력 시 방어적으로 복사해 쓰기를 막습니다.

| 추가 필드 | 의미와 공급 담당 |
|---|---|
| `catalog[sku] → SkuSpec` | 크기·질량·허용 yaw·상부 허용하중 N. 종류만 있는 잔여 재고를 가상 박스로 만들 때 사용 |
| `pallet_max_weight_kg` | 팔레트 허용 총중량 |
| `capacity_overrides_n[box_id]` | 실측/상태별 허용하중. 예: 변형 윗면에 0N. NG 라우팅은 앞단 담당 |
| `observed_preview` | 순서가 실제 확인된 `BoxState` 접두부. 추측 순서는 넣지 않음 |
| `ems_upper_by_candidate` | 생성기가 제공하는 EMS 상한 좌표. 없으면 `FOOTPRINT_COLUMN_PROXY` 특징과 `ems_known=0` 사용 |
| `robot_time_sec_by_candidate` | 로봇 담당의 예상 사이클 시간. 없으면 좌표 기반 대용 시간 |
| `buffer_capacity` | 버퍼 정원. 버퍼 박스는 `tracked_boxes`에서 status `BUFFERED`로만 조회 |
| `uncertain_box_ids` | 불확실성 처리 대상. 참조 검사기는 이를 안전 통과로 간주하지 않고 SENSOR_UNCERTAIN 반환 |
| `distribution_status` | 외부 모듈의 `IN_DISTRIBUTION` / `OOD` 판정. 자동 OOD 검출기라고 주장하지 않음 |

`remaining_by_sku`는 **아직 개별 ID가 없는 재고**입니다. 현재 박스, preview, 버퍼 박스는 여기에 다시 포함하지 않습니다.
planner는 현재 박스의 입고 시점 재고 감소를 또 수행하지 않습니다. 센서 → State Manager가 먼저 반영합니다.
확정 MISSING은 Supervisor/State Manager가 잔여 수량을 고쳐서 알립니다. 이 파트는 미입고를 자동 MISSING으로 바꾸지 않습니다.

## 3. Hard Validator가 전달할 안전 근거

호출 signature는 그대로입니다.

```python
def validate_constraints(box: BoxState, candidate: PlacementCandidate,
                         state: SystemState) -> ValidationResult:
    ...
```

실패하면 공통 `RejectCode`와 사유를 반환합니다. 성공하면
`ValidationResult(True, details={"evidence": ConstraintEvidence(...)})`를 반환합니다.

| evidence 필드 | 범위 / 뜻 |
|---|---|
| `support_ratio` | 0~1, 지지면 비율 |
| `cog_margin_ratio` | 0~1, 검사기가 검증한 최소 무게중심 지지 경계 여유. reference는 지지 사각형 반폭으로 정규화 |
| `load_margin_ratio` | 0~1, 전체 하부 구조의 최소 잔여 압축 하중 비율 |
| `pallet_load_margin_ratio` | 0~1, 팔레트 잔여 총하중 비율 |
| `max_load_ratio` | 0 이상, 검사기가 계산한 최대 상부하중/허용하중 |
| `support_centering` | 0~0.5, OPAL 지지 영역 중심 여유. 바닥은 0.5 |
| `dependency_count` | 이 후보 밑으로 이어지는 하부 의존 박스 수 |
| `source` | `LBCP_...` 등 실제 사용한 검사 방법. 참조기는 `REFERENCE_FULL_SINGLE_SUPPORT` |

성공 표시만 있고 evidence가 없으면 INVALID_STATE로 제외합니다. 누락된 안전 지표를 1로 채우지 않습니다.
팀원의 LBCP에서 CoG 불확실성·치수 오차를 반영한 뒤 이 값들을 내보내면 됩니다.
후보의 version·box ID는 planner에서도 다시 검사합니다. 필수 검사를 시간 예산 때문에 생략하지 않습니다.

## 연결 예제

```python
from pac_planning import PlacementPlanner, load_config

planner = PlacementPlanner(
    context=context,  # pac_common.PlanningContext
    config=load_config("config/default.yaml"),
    generate_candidates=team_generator.generate_candidates,
    validate_constraints=team_validator.validate_constraints,
    model_path="models/dual_head_ranker.json",
)

# High-level이 이번에 놓기로 정한 현재 박스 또는 버퍼 박스
result = planner.plan(box, system_state, supplied_candidates, seed=42)

for candidate in result.ranked:
    if candidate.base_state_version != latest_state.state_version:
        # STALE_PLAN: 새 snapshot으로 다시 요청
        break
    verdict = robot_validator.validate_robot_motion(
        box, candidate, latest_state, robot_state
    )
    if verdict.success:
        # 실행 모듈로 전달. 여기서 PLACED를 기록하지 않음.
        submit_to_executor(candidate)
        break
```

v0.2의 단일 후보 인터페이스도 제공합니다.

```python
scored = planner.evaluate_candidate(box, candidate, state)
future_value = planner.evaluate_future_value(box, candidate, state, seed=42)
```

단일 후보 evaluator는 필수 제약 검사 후 현재 점수를 반환하며,
future evaluator는 가상 상태에서 동일 시나리오 롤아웃 평균을 반환합니다.
일반 runtime에서는 `plan`을 사용해야 후보 사이에 공통 난수와 공통 완료 라운드가 유지됩니다.

## 반환과 소비

- `ranked`: Top-K 중 최종 점수순 `PlacementCandidate` tuple. teacher 모드는 전체 유효 후보.
- `score_detail`: 다섯 가중 기여도. 합이 최종 score입니다.
- `evaluations`: 특징, rank logit, 미래 성과와 출처 `ROLLOUT / AI_ESTIMATE / CURRENT_ONLY`.
- `rejected`: 공통 Enum 기반 탈락 이유. stale 후보를 다른 version으로 몰래 고치지 않습니다.
- `diagnostics`: 후보 수, 공통 완료 시나리오 수, 소프트 예산, 모델/폴백 상태, 계산시간.
- `requires_robot_validation=True`, `state_mode=PLANNED`: 로봇 검증 전 계획임을 명시합니다.

로봇 검증에서 모든 반환 후보가 탈락하면, 같은 실제 snapshot에서
`excluded_candidate_ids`에 해당 후보들을 넣고 다시 호출해 다음 후보들을 검사할 수 있습니다.
실제 상태가 바뀌었으면 새 version으로 전부 다시 생성합니다.
후보가 없으면 빈 `ranked`와 사유를 반환합니다. HOLD/BUFFER/PALLET_CLOSE/NG를 임의 선택하지 않습니다.

High-level에서 서로 다른 박스 행동을 비교할 때 rank logit은 비교하지 마세요.
회귀 값은 `추가 적재부피 / 팔레트 용량`으로 같은 단위입니다. 현재 박스 부피, 버퍼/재취급 비용은 High-level에서 함께 계산해야 합니다.

## 실제 상태와 모의 상태

입력은 바꾸지 않습니다. rollout은 `SimulationSnapshot(mode=SIMULATED)`에서 새 중첩 객체로 계산합니다.
가상 version을 실제 version처럼 증가시키지 않고 입력 version을 출처로 유지합니다.
실제 실행 후 `ExecutionResult` 확인과 실제 pose 보정, 재고/버퍼/CoG 확정은 State Manager의 책임입니다.

`pose_to_ros_pose_stamped`는 optional ROS adapter입니다. 메시지의 frame과 quaternion·ROS timestamp 변환을 테스트했습니다.
custom ROS message/action과 ROS node wrapper, ROS 네트워크 통합은 아직 없습니다.

## 모델의 평가 조건

제공 모델은 참조 검사기, horizon 3, 시나리오 7개, alpha 0.2로 학습했습니다.
기본 YAML도 이 조건과 같습니다. 다른 horizon/시나리오 수/alpha 설정의 모델 값은 그대로 섞지 않고 추론 폴백 처리합니다.
실제 EMS/LBCP 검사기로 바꾼 뒤에는 참조 모델의 성능을 가정하지 말고 교사 데이터를 재생성해 학습하세요.
그 전에는 `model_path`를 생략하거나 context를 OOD로 지정해 실제 검사기와 휴리스틱·롤아웃으로 연결할 수 있습니다.
