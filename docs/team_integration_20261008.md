# 2026-10-08 팀 연결 작업 기록

## 확인한 소스

| 저장소/브랜치 | 확인 커밋 | 내용 |
|---|---|---|
| shared / feature/donghan-placement-planner | fe1e135c826cad8b134c637342e4ca5373ccdc73 | 이번 수정의 기준 |
| shared / claude/pensive-pasteur-dwbu3g | e7ed1f93fa617d9b61006db036d0637f9252a35b | c0f32d6 이후 하중·후보 방향·high-level 수정 포함 |
| shared / feature/jaesung-dataset-generator | cc4c075daa3dd4c7f80510042732763fc9502933 | 기존 AHEAD 생성기 |
| shared / feature/jaesung-physics-simulator | 7f4ae37621ea20527c09a707390db9b0f1317aa3 | 독립 PyBullet 서버 |
| dlwotjd1289-cloud/pac2026-ahead / main | fa3115a903117eeb57009defd12a201d5290345d | 작업셀, 현대 서브모듈, 로봇 어댑터, 물리 모델 |
| pac2026-ahead / fix/hyundai-submodule-init | 0c8fb80f5674e6202489209a743f8b82ce915262 | 서브모듈 초기화 수정; main과 별도 |

shared/main은 아직 제목만 있다. 새 ahead 저장소의 물리 서버 핵심 파일들은
shared 물리 브랜치와 동일하지만 작업셀/로봇/서브모듈 자산은 새 저장소에 더 있다.
`ros2_ws/src/hdr_*`는 일반 파일이 아닌 서브모듈 상대 심볼릭 링크다.

## 무엇을 바꿨는가

- `team_bridge.py`: 태현님의 `generate_with_report`와 `context_with_ems`를 호출한다.
  기존 `DonghanPlacer`는 backend.context만 넘겨 EMS 특징이 대체값으로 계산됐다.
  이제 실제 EMS를 공급하고, 이전 상태의 후보 ID/좌표/버전이 섞이면 거부한다.
  `TeamPlacer`는 high-level의 4-인자 callback과 맞고 버퍼 상태 원본을 사용한다.
- `scene_bridge.py`: 후보 모서리 -> 박스 중심 -> 물리 세계 좌표를 명시적으로 변환한다.
  `workcell.yaml`의 목재 중심 높이 0.075m에 두께/2를 더해 적재면 0.15m를 만든다.
  팔레트 가로·세로가 다르면 자동 축소/보정하지 않고 거부한다. TCP 목표는 아직 아니다.
- `planning_service.py`, `pac_planning_interfaces`, launch: ROS service 호출 경로를 추가했다.
  입력은 공통 자료형의 JSON, 출력은 PLANNED 결과이며 robot validation은 항상 필요하다.
  실제 ROS 빌드/통신은 이 환경에서 실행하지 않았다.
- `stage_planner_workspace.py`: 같은 이름의 pac_common/pac_planning이 두 벌 빌드되는 것을
  피하려고 새 작업공간에 소유자별 패키지 4개만 연결한다. 기존 파일을 덮어쓰지 않는다.
- `run_team_replay.py`: 재성님 데이터 -> 태현님 Rule/후보/검사 -> 동한님 평가를 실행하고
  후보별 특징·점수·탈락·상태 버전을 JSONL에 기록한다. 모든 결과는 SIMULATED다.
- `patches/ahead_zero_top_load.patch`: 팀원 검토용 작은 패치다. 물리 저장소에는 자동 적용하지 않았다.
  0 N 입력을 허용하고, 상부 하중 0이면 OK, 양수이면 OVERLOAD로 표시한다.
  0으로 나눈 Infinity는 JSON에 넣지 않는다. 카드보드 변형 모델은 추가하지 않는다.

최신 하중 수정에 대한 비교·재현·검증은 [load_regression_8e934a4.md](load_regression_8e934a4.md)에 기록했다.
이 문서와 함께 제공된 TeamPlacer/scene_bridge/ROS service/0N 패치는 아직 원격 미반영이다.

## 최초 연결 작업에서 실제 실행한 검증 (태현 c0f32d6 기준)

Ubuntu 24.04에서 별도로 설치한 Python 3.10.22를 사용했다. ROS2는 설치되어 있지 않다.
PyBullet 3.2.7, NumPy 2.2.6, PyYAML 6.0.3, pytest 8.4.2를 사용했다.

- 이전 Python 3.12 환경의 PyBullet 소스 빌드가 컴파일러 부재로 실패했다.
  Python 3.10 wheel 설치로 해결했고 실제 Bullet 계산을 실행했다.
- 변경된 플래너와 연결/물리 테스트: 50 passed.
- 최신 태현님 테스트: 148 passed, 2 skipped. Gymnasium/PyTorch 선택 의존성 테스트는 미실행.
- 새 ahead/main 테스트: 12 passed. 이 테스트 통과는 MoveIt 실행 성공을 뜻하지 않는다.
- 생성기 S0001의 첫 5박스 -> high-level Rule -> TeamPlacer: 5/5 가상 배치,
  NG 0, 기록된 기하 안전 문제 0. 실제/측정 작업시간이 아닌 high-level 가정 시간이다.
- 실제 Bullet 시험: 선택된 단일 박스를 solid/slatted 팔레트에 각각 생성하고 2초 계산.
  목표 대비 이동 1cm 미만, roll/pitch 2도 미만. 입력 실제 상태를 갱신하지 않음.
  이것은 접촉/안정화 검증이며 로봇 pick & place 시험이 아니다.
- 0N 파싱 거부와 무하중에서도 OVERLOAD가 되는 기존 문제를 재현한 뒤 패치에서 검증했다.

## PPO 연결 시 주의

태현님 정책은 `value_provider=proxy`, `placer=dblf`로 학습됐다.
proxy는 동한님 미래 모델의 출력이 아니라 배치 후 평탄도다.
기존 `DonghanValue(model_path=None)`는 ranking 모드의 미래가치 0을 반환한다.
이를 학습된 미래 예측값이라고 보고하지 않는다.

TeamPlacer는 `name=donghan_ems_rollout_v1`이다. 기존 정책의 metadata를 이 이름으로
고쳐 호환되는 척하지 않는다. 입력 특징/전이가 달라지므로 이 placer를 넣고 재학습하거나,
분리된 오프라인 비교 실험으로 전환 효과를 검증해야 한다.
`check_policy_contract()`는 placer/value_provider가 맞지 않으면 거부한다.
이번 기본 실행은 Rule+TeamPlacer이며 기존 PPO 정책은 수정하지 않았다.

## 재현: Python 연결부터

아래 예시는 서로 다른 브랜치를 별도 checkout으로 읽는다. main에 병합하지 않는다.

```bash
git clone --branch feature/donghan-placement-planner https://github.com/yang8988/pac-mission1-shared.git planner_checkout
git clone --branch claude/pensive-pasteur-dwbu3g https://github.com/yang8988/pac-mission1-shared.git candidate_checkout
git -C candidate_checkout switch --detach e7ed1f93fa617d9b61006db036d0637f9252a35b
git clone --branch feature/jaesung-dataset-generator https://github.com/yang8988/pac-mission1-shared.git generator_checkout
export PAC_PLANNER_ROOT="$PWD/planner_checkout/pac-mission1-shared"
export PAC_TEAM_ROOT="$PWD/candidate_checkout"
export PAC_GENERATOR_ROOT="$PWD/generator_checkout/pac-mission1-shared/tools/ahead_dataset_generator"
cd "$PAC_PLANNER_ROOT"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
export PYTHONPATH="$PAC_PLANNER_ROOT/ros2_ws/src/pac_common:$PAC_PLANNER_ROOT/ros2_ws/src/pac_planning:$PAC_TEAM_ROOT/ros2_ws/src/pac_candidates:$PAC_TEAM_ROOT/ros2_ws/src/pac_highlevel"
python -m pytest -q
python "$PAC_GENERATOR_ROOT/scripts/generate_dataset.py" --config "$PAC_GENERATOR_ROOT/config/default.yaml" --mode sample --sample-per-family 1 --output "$PAC_PLANNER_ROOT/runs/source_dataset"
python scripts/donghan/run_team_replay.py --team-root "$PAC_TEAM_ROOT" --dataset runs/source_dataset --max-boxes 5 --output runs/team_replay
```

정상 출력은 `scope=OFFLINE_SIMULATION`, `placed=5`, `robot_execution=NOT_RUN`이다.
`runs/team_replay/plans.jsonl`에 후보 평가/탈락 이유가 기록된다.
물리 패키지가 import되지 않으면 선택적 물리 테스트는 skip된다. 물리 검증은 아래를 사용한다.

```bash
git clone --recurse-submodules https://github.com/dlwotjd1289-cloud/pac2026-ahead.git ahead_checkout
export PAC_AHEAD_ROOT="$PWD/ahead_checkout"
git -C "$PAC_AHEAD_ROOT" apply --check "$PAC_PLANNER_ROOT/patches/ahead_zero_top_load.patch"
# 팀원과 검토 후 로컬 작업 브랜치에서 적용. main에 자동 반영되지 않는다.
git -C "$PAC_AHEAD_ROOT" apply "$PAC_PLANNER_ROOT/patches/ahead_zero_top_load.patch"
python -m pip install 'pybullet>=3.2.6,<4' 'networkx>=2.8,<4' 'aiohttp>=3.8,<4'
export PYTHONPATH="$PYTHONPATH:$PAC_AHEAD_ROOT/ros2_ws/src/pac_simulation"
python -m pytest -q tests/test_team_physics.py
```

새 ahead의 pac_common/pac_planning 경로는 위 PYTHONPATH에 추가하지 않는다.

## 다음 실행: ROS2 Humble (아직 현장 검증 필요)

Ubuntu 22.04 시스템 Python/ROS 환경을 사용한다. 위 venv에서 나오고 새 터미널을 권장한다.
PAC_PLANNER_ROOT와 PAC_TEAM_ROOT는 위 실제 checkout 경로로 다시 설정한다.

```bash
source /opt/ros/humble/setup.bash
python3 "$PAC_PLANNER_ROOT/scripts/donghan/stage_planner_workspace.py" --team-root "$PAC_TEAM_ROOT" --output "$HOME/ahead_planner_ws"
cd "$HOME/ahead_planner_ws"
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
ros2 launch pac_planning placement_planner.launch.py candidate_config:="$PAC_TEAM_ROOT/config/taehyeon/candidates.yaml" planner_config:="$PAC_PLANNER_ROOT/config/default.yaml" use_sim_time:=false
```

별도 터미널에서 동일 ROS/setup을 source하고:

```bash
ros2 service list
ros2 interface show pac_planning_interfaces/srv/PlanPlacement
```

정상이라면 `/pac/plan_placement`가 보인다. 이것만으로 전체 통합 완료가 아니다.
요청 JSON은 `plain(SystemState)`, `plain(PlanningContext)`이며 `expected_state_version`을
같이 보낸다. 최신 실제 버전이 바뀌었는지 실행 직전 State Manager가 다시 확인해야 한다.
Gazebo와 연결할 때만 simulation clock bridge를 확인하고 use_sim_time=true로 바꾼다.

## 남은 순서와 담당 경계

1. 위 ROS service를 Humble에서 build/호출하고 실제 결과 JSON을 확인한다.
2. 작업셀과 모델의 치수를 통일한다. Gazebo/YAML은 1.2x1.0m, Bullet 기본은 1.1x1.1m.
   최대 높이/팔레트 총중량/박스 허용하중은 단일 명시 설정으로 공급한다.
   옛 750N/150kg 어댑터를 팀의 McKee/1000kg 시험 설정에 자동 덮어쓰지 않는다.
3. 새 ahead의 target=HDP160-31, proxy=HDR50-22 표기가 팀의 현재 로봇 결정과 맞는지 정리한다.
4. 작업셀/MoveIt 담당이 TCP 장착, planning scene, 실제 controller와 MoveIt backend를 연결한다.
   현재 Hdr50_22SimAdapter는 backend가 없으면 거부하며 command_from_candidate는 모서리를
   그대로 복사한다. 이 파일에 중심/TCP/TF 변환을 명시적으로 연결해야 한다.
5. 무게 측정 완료 상태와 카메라 관측을 box source에 연결한다. 모델의 mass와 측정값은 구분한다.
6. 단일 박스 pick/attach/move/place/detach/retreat를 실행하고 실제 pose로 상태를 확정한다.
7. 실패·시간초과·중복 실행·오래된 후보를 막고 5~10박스를 반복한다.
8. 그 후 PPO를 실제 사용할 placer/value_provider로 재평가·재학습한다.

추가 발견: ahead StateManager에는 pending candidate/version 대조와 중복 execution 차단이
아직 없다. 실행 성공 callback을 받을 때 이 검증 없이 바로 상태에 append하지 않는다.
서브모듈 초기화 수정 브랜치는 pinned detached checkout을 쓰지만 기존
verify_hdr50_proxy.sh는 `.git` 디렉터리와 humble 브랜치 이름을 요구한다.
이 검증 스크립트도 서브모듈 SHA 기준으로 맞춰야 한다. 이번에 팀원 파일을 자동 변경하지 않았다.
