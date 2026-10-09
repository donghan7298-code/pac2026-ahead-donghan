# PAC 2026 팀 공통 개발 기준서

> **프로젝트:** HD현대로보틱스 미션 1 — Mixed Palletizing  
> **문서 버전:** v0.2  
> **상태:** 통합 테스트 반영 초안  
> **목적:** 3인이 독립적으로 모듈을 개발해도 공통 데이터 계약을 통해 쉽게 통합되도록 한다.

---

# 1. 최우선 원칙

1. **내부 구현은 자유, 모듈 입출력 계약은 고정한다.**
2. **State Manager만 실제 시스템 상태를 변경한다.**
3. 모든 모듈은 동일한 단위, 좌표계, 자료형, 오류코드를 사용한다.
4. 실제 상태와 계획/시뮬레이션 상태를 절대 섞지 않는다.
5. 공통 인터페이스 변경은 Breaking Change로 취급하고 팀에 공유한다.

문서 표현:

- **MUST**: 반드시 준수
- **SHOULD**: 특별한 이유가 없으면 준수
- **MAY**: 선택 가능

---

# 2. 공통 개발 환경

| 항목 | 기준 |
|---|---|
| OS | Ubuntu 22.04 LTS |
| ROS | ROS 2 Humble |
| Python | Python 3.10 |
| 형상관리 | Git / GitHub |
| Python Test | pytest |
| Python Style | PEP 8 |
| 설정 | YAML |
| 테스트 데이터 | JSON |
| 로그 | JSONL 권장 |
| Encoding | UTF-8 |

의존성은 `requirements.txt` 또는 `pyproject.toml` 중 팀에서 하나를 선택해 저장소 전체에서 동일하게 사용한다.

---

# 3. 저장소 구조

ROS 2 통합을 전제로 한 monorepo 권장 구조:

```text
pac2026/
├── README.md
├── docs/
│   └── common_development_standard.md
│
├── config/
│   ├── default.yaml
│   ├── robot.yaml
│   └── local.example.yaml
│
├── test_data/
│   ├── scenario_001_basic.json
│   └── ...
│
├── ros2_ws/
│   └── src/
│       ├── pac_common/
│       ├── pac_perception/
│       ├── pac_planning/
│       ├── pac_robot/
│       └── pac_bringup/
│
├── tests/
└── .gitignore
```

각 ROS package에는 필요에 따라 `package.xml`, `setup.py/setup.cfg`, `resource/`, launch 파일을 둔다.

알고리즘을 ROS와 독립적으로 테스트할 수 있도록 핵심 계산 로직과 ROS Node wrapper를 분리한다.

---

# 4. Canonical Runtime Model

## 4.1 MUST

모듈 내부에서 서로 주고받는 공통 Python 자료형은 **`pac_common`의 dataclass**를 기준으로 한다.

금지:

```text
A 모듈: raw dict
B 모듈: 자체 class
C 모듈: ROS msg
```

권장:

```text
ROS Message
    ↓ adapter
pac_common dataclass
    ↓
Algorithm
    ↓
pac_common dataclass
    ↓ adapter
ROS Message
```

JSON/YAML은 저장/설정용이며 알고리즘 모듈 간 직접 계약으로 사용하지 않는다.

---

# 5. Naming Convention

| 대상 | 규칙 |
|---|---|
| Class | PascalCase |
| Function | snake_case |
| Variable | snake_case |
| Constant | UPPER_SNAKE_CASE |
| Python file | snake_case.py |
| Package | snake_case |

ID 예:

```text
P001
B001
T001
S0012-B003-C005
```

후보 ID는 가능하면 `state_version + box_id + candidate index`를 포함한다.

---

# 6. 단위

내부 계산은 SI 단위를 사용한다.

| 물리량 | 단위 |
|---|---|
| Length | m |
| Mass | kg |
| Time | s |
| Linear velocity | m/s |
| Acceleration | m/s² |
| Force | N |
| Angle | rad |
| Angular velocity | rad/s |

표시 UI에서 mm 또는 degree가 필요하면 출력 단계에서만 변환한다.

---

# 7. 시간 기준

서로 다른 종류의 시간을 구분한다.

## 7.1 상태/센서 Timestamp

ROS 시스템에서는 **ROS Clock**을 사용한다.

```text
stamp_sec
```

시뮬레이션에서 `/clock`을 사용하면 모든 모듈이 동일한 ROS time을 사용한다.

## 7.2 계산시간 측정

성능 측정은:

```python
time.perf_counter()
```

를 사용한다.

### MUST

`ROS timestamp`와 `perf_counter()` 값을 서로 직접 빼지 않는다.

---

# 8. 좌표계

오른손 좌표계를 사용한다.

```text
        +Z
         ↑
         |
         O────→ +X
        /
       /
     +Y
```

기본 frame:

```text
world
pallet
conveyor
robot_base
camera_2d
camera_3d
gripper
box_<id>
```

모든 Pose에는 반드시 `frame_id`가 존재해야 한다.

---

# 9. 공통 자료형

아래 코드는 계약의 의미를 나타낸다. 실제 정의는 `pac_common` 한 곳에서만 작성한다.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict


class BoxStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    DETECTED = "DETECTED"
    MEASURED = "MEASURED"
    ON_CONVEYOR = "ON_CONVEYOR"
    READY_FOR_PICK = "READY_FOR_PICK"
    PICKING = "PICKING"
    IN_TRANSIT = "IN_TRANSIT"
    PLACED = "PLACED"
    BUFFERED = "BUFFERED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Size3D:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Pose3D:
    frame_id: str
    x: float
    y: float
    z: float
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0


@dataclass(frozen=True)
class BoxState:
    box_id: str
    sku_id: str
    size: Size3D
    weight_kg: float
    pose: Pose3D
    allowed_yaws_rad: tuple[float, ...]
    status: BoxStatus
    confidence: float
    stamp_sec: float
    source: str
```

## 9.1 초기 Orientation 규칙

초기 범위에서는 박스의 위/아래를 유지하고 수평 회전만 허용한다.

따라서:

```python
allowed_yaws_rad = (0.0, 1.57079632679)
```

처럼 표현한다.

Full 3D orientation이 필요해지면 기존 필드 의미를 몰래 바꾸지 않고 인터페이스 버전을 변경한다.

---

# 10. PalletState

```python
@dataclass(frozen=True)
class PlacedBox:
    box_id: str
    sku_id: str
    size: Size3D
    weight_kg: float
    pose: Pose3D


@dataclass(frozen=True)
class PalletState:
    pallet_id: str
    size: Size3D
    boxes: tuple[PlacedBox, ...]
```

팔레트 위 박스 pose의 `frame_id`는 기본적으로:

```text
pallet
```

이다.

---

# 11. InventoryState

상태 정보를 여러 bucket list에 중복 저장하지 않는다.

```python
@dataclass(frozen=True)
class InventoryState:
    tracked_boxes: dict[str, BoxState]
    remaining_by_sku: dict[str, int]
```

의미:

- `tracked_boxes`: 실제로 관측되어 개별 ID가 생긴 박스
- `remaining_by_sku`: 아직 개별 ID가 없거나 미투입 상태인 SKU별 잔여 수량

관측된 박스의 처리 상태는:

```text
tracked_boxes[box_id].status
```

를 단일 원본으로 사용한다.

---

# 12. SystemState와 소유권

```python
@dataclass(frozen=True)
class SystemState:
    state_version: int
    stamp_sec: float
    pallet: PalletState
    inventory: InventoryState
```

## 12.1 Single Writer Rule

**State Manager만 새로운 `SystemState`를 확정할 수 있다.**

다른 모듈:

```text
SystemState snapshot 입력
→ 계산
→ Result/Event 반환
```

State Manager:

```text
Result 검증
→ 새 상태 생성
→ state_version 증가
```

여러 모듈이 실제 상태를 직접 수정하는 것을 금지한다.

---

# 13. ACTUAL / PLANNED / SIMULATED

반드시 구분한다.

```text
ACTUAL
PLANNED
SIMULATED
```

Look-ahead는 `ACTUAL`을 직접 수정하면 안 된다.

### 금지

```python
simulation_state = actual_state.copy()
```

중첩된 list/dict가 공유될 수 있다.

### 권장

- frozen dataclass + `dataclasses.replace`
- 또는 필요한 경우 `copy.deepcopy`

---

# 14. PlacementCandidate

```python
@dataclass(frozen=True)
class PlacementCandidate:
    candidate_id: str
    box_id: str
    target_pose: Pose3D
    base_state_version: int
    score: float | None = None
    score_detail: dict[str, float] = field(default_factory=dict)
```

### MUST

`target_pose.frame_id == "pallet"`

계획이 만들어진 상태 버전을 반드시 기록한다.

---

# 15. Validator 결과

```python
class RejectCode(str, Enum):
    OUT_OF_BOUND = "OUT_OF_BOUND"
    HEIGHT_LIMIT = "HEIGHT_LIMIT"
    BOX_COLLISION = "BOX_COLLISION"
    LOW_SUPPORT = "LOW_SUPPORT"
    LOAD_VIOLATION = "LOAD_VIOLATION"
    COG_VIOLATION = "COG_VIOLATION"

    PAYLOAD_EXCEEDED = "PAYLOAD_EXCEEDED"
    IK_FAIL = "IK_FAIL"
    ROBOT_COLLISION = "ROBOT_COLLISION"
    APPROACH_FAIL = "APPROACH_FAIL"
    RETREAT_FAIL = "RETREAT_FAIL"

    TIMEOUT = "TIMEOUT"
    INVALID_STATE = "INVALID_STATE"
    STALE_PLAN = "STALE_PLAN"

    SENSOR_UNCERTAIN = "SENSOR_UNCERTAIN"
    TRACKING_LOST = "TRACKING_LOST"
    SIZE_MISMATCH = "SIZE_MISMATCH"
    WEIGHT_MISMATCH = "WEIGHT_MISMATCH"

    EXECUTION_FAIL = "EXECUTION_FAIL"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass(frozen=True)
class ValidationResult:
    success: bool
    codes: tuple[RejectCode, ...] = ()
    details: dict[str, object] = field(default_factory=dict)
```

복수 탈락 사유를 허용한다.

---

# 16. 모듈 Interface

## 16.1 Perception

```python
def perceive_box(sensor_input) -> BoxState:
    ...
```

Perception 결과 자체가 실제 상태를 직접 수정하지 않는다.

State Manager가 관측 결과를 받아 상태에 반영한다.

---

## 16.2 Candidate Generator

```python
def generate_candidates(
    box: BoxState,
    state: SystemState
) -> list[PlacementCandidate]:
    ...
```

---

## 16.3 Hard Constraint Validator

```python
def validate_constraints(
    box: BoxState,
    candidate: PlacementCandidate,
    state: SystemState
) -> ValidationResult:
    ...
```

최소 검사:

```text
boundary
height
box collision
allowed orientation
support
load
```

---

## 16.4 Candidate Evaluator

```python
def evaluate_candidate(
    box: BoxState,
    candidate: PlacementCandidate,
    state: SystemState
) -> PlacementCandidate:
    ...
```

점수는 필수 제약 통과 후보에 대해서만 계산한다.

---

## 16.5 Look-ahead

```python
def evaluate_future_value(
    box: BoxState,
    candidate: PlacementCandidate,
    state: SystemState,
    seed: int
) -> float:
    ...
```

가상 상태에서만 계산한다.

---

## 16.6 Robot Feasibility

```python
def validate_robot_motion(
    box: BoxState,
    candidate: PlacementCandidate,
    state: SystemState,
    robot_state: RobotState
) -> ValidationResult:
    ...
```

가장 먼저:

```python
candidate.base_state_version == state.state_version
```

인지 확인한다.

다르면:

```text
STALE_PLAN
```

을 반환한다.

그 후:

```text
payload
IK/reachability
collision
approach
place
retreat
```

을 검사한다.

---

# 17. 실행 결과

```python
@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    box_id: str
    candidate_id: str
    actual_pose: Pose3D | None
    codes: tuple[RejectCode, ...]
    stamp_sec: float
```

명령 전송 성공과 실제 배치 성공을 구분한다.

`PLACED`는 실제 완료 확인 후에만 기록한다.

---

# 18. 상태 갱신 흐름

```text
Candidate 생성
     ↓
Hard Constraint
     ↓
Score / Look-ahead
     ↓
Candidate Ranking
     ↓
Robot Validation
     ↓
Execution
     ↓
Result 확인
     ↓
State Manager
     ↓
state_version += 1
```

현재 상태가 바뀌면 기존 후보는 실행 전에 다시 버전을 확인한다.

---

# 19. 설정 파일

공통 설정은 다음으로 통일한다.

```text
config/
├── default.yaml
├── robot.yaml
└── local.example.yaml
```

`local.yaml`은 개인 경로/장치명처럼 환경별 값만 저장하고 Git에서 제외한다.

예:

```yaml
schema_version: 1

pallet:
  x_m: 1.2
  y_m: 1.0
  max_height_m: 1.5

constraint:
  min_support_ratio: 0.70

planning:
  max_candidates: 30
  lookahead_depth: 3
  rollout_count: 20
  timeout_sec: 1.0
```

---

# 20. 입력 Validation

공통 모델 생성 또는 외부 입력 변환 시 최소 검사:

```text
size.x/y/z > 0
weight >= 0
0 <= confidence <= 1
NaN 금지
inf 금지
유효하지 않은 frame_id 금지
remaining count >= 0
```

잘못된 입력을 조용히 보정하지 않는다.

---

# 21. Logging

JSONL 권장.

최소 필드:

```text
schema_version
run_id
scenario_id
git_commit
timestamp
state_version
box_id
candidate_id
constraint_result
reject_codes
score
score_detail
robot_validation
planning_time_sec
execution_result
seed
```

선택/탈락 이유를 재현할 수 있어야 한다.

---

# 22. Reproducibility

무작위 알고리즘은 반드시 seed를 입력받는다.

```python
seed = 42
```

결과 저장:

```text
scenario_id
algorithm_version
config
seed
git_commit
metrics
```

---

# 23. Git Workflow

```text
main
  ↑
Pull Request
  ↑
feature/*
fix/*
refactor/*
test/*
docs/*
```

`main`은 항상 실행 가능한 상태를 유지한다.

직접 push하지 않는 것을 원칙으로 한다.

---

# 24. Branch 이름

```text
feature/perception-tracking
feature/extreme-point
feature/lookahead
feature/robot-validation

fix/support-check
refactor/common-model
test/integration-basic
docs/common-contract
```

---

# 25. Commit Message

Conventional Commits 스타일 권장:

```text
feat:
fix:
refactor:
test:
docs:
chore:
```

예:

```text
feat: add extreme point candidate generator
fix: prevent simulated state mutation
test: add stale plan integration test
docs: define pallet coordinate frame
```

---

# 26. 공통 파일 변경 규칙

다음은 공통 계약 파일로 취급한다.

```text
pac_common/*
config/default.yaml
docs/common_development_standard.md
```

공통 계약 변경 시:

```text
1. 변경 이유 작성
2. 영향 모듈 확인
3. 테스트 수정
4. 다른 팀원 1명 이상 확인
5. Merge
```

Breaking Change이면 문서의 interface version을 올린다.

---

# 27. 테스트

## Unit Test

각 모듈 내부 기능.

## Contract Test

모듈 A 출력이 모듈 B 입력과 정확히 호환되는지 검사.

## Integration Test

```text
Perception
→ State Manager
→ Planner
→ Validator
→ Robot
→ Result
→ State Update
```

## Regression Test

수정 전 성공하던 공통 scenario가 이후에도 성공하는지 확인.

---

# 28. 최소 공통 Scenario

```text
scenario_001_basic
scenario_002_random_order
scenario_003_heavy_box_late
scenario_004_same_sku_sequence
scenario_005_out_of_bound
scenario_006_low_support
scenario_007_robot_infeasible
scenario_008_size_corrected
scenario_009_actual_pose_error
scenario_010_timeout
scenario_011_stale_plan
scenario_012_tracking_lost
```

---

# 29. 반드시 유지할 통합 테스트

최소 아래 항목은 자동 테스트로 유지한다.

- [ ] Perception 출력 orientation을 Planner가 그대로 읽을 수 있음
- [ ] 모든 Pose에 frame_id가 있음
- [ ] Validator가 box size/weight/current state를 사용할 수 있음
- [ ] Look-ahead가 ACTUAL state를 수정하지 않음
- [ ] state_version이 바뀌면 오래된 후보가 `STALE_PLAN` 처리됨
- [ ] 같은 박스가 서로 모순되는 두 처리 상태를 동시에 갖지 않음
- [ ] JSON fixture → common model 변환 성공
- [ ] common model → ROS adapter 변환 성공
- [ ] error code가 문자열 자유입력이 아니라 Enum으로 관리됨

---

# 30. 공통 메인 흐름

```text
Sensor / Simulation
        ↓
    Perception
        ↓
 Observation Event
        ↓
   State Manager
   (single writer)
        ↓
 immutable SystemState
        ↓
 Candidate Generator
        ↓
 Hard Constraint
        ↓
 Score / Look-ahead
        ↓
 Ranked Candidates
        ↓
 Robot Feasibility
        ↓
     Execution
        ↓
 Execution Result
        ↓
   State Manager
        ↓
 state_version + 1
```

---

# 31. 개발 시작 전 체크리스트

- [ ] Ubuntu / ROS / Python 버전 동일
- [ ] GitHub repo 생성
- [ ] `pac_common` 생성
- [ ] dataclass / Enum 공통 정의
- [ ] 좌표계와 `frame_id` 확정
- [ ] `allowed_yaws_rad` 의미 확정
- [ ] State Manager 담당 확정
- [ ] Validator 함수 signature 확정
- [ ] Robot interface signature 확정
- [ ] `default.yaml` 작성
- [ ] `scenario_001_basic.json` 작성
- [ ] pytest contract test 작성
- [ ] main branch 보호 규칙 설정
- [ ] 첫 통합 dry-run 수행

---

# 32. 변경 이력

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-10-06 | 최초 공통 개발 기준 |
| v0.2 | 2026-10-06 | 모의 3인 통합 테스트 결과 반영: Validator 입력, frame, orientation, state copy, inventory, single-writer 수정 |

---

# 33. 핵심 결론

> **각자 다른 알고리즘을 작성해도 된다.  
> 하지만 같은 `SystemState` snapshot을 읽고, 같은 공통 자료형으로 결과를 반환하며, 실제 상태 변경은 State Manager 한 곳에서만 수행한다.**

이 규칙을 지키면 세 사람이 병렬로 개발하면서도 마지막 통합 시 데이터 구조와 상태 관리 때문에 전체 코드를 다시 작성할 가능성을 크게 줄일 수 있다.
