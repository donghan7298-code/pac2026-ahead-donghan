# 동한 업로드·SB3 정책·ROS 의존성 연동 점검 — 2026-10-08

## 현재 원격과 변경

공유 저장소 5개, 별도 ahead 2개 브랜치 HEAD를 읽었다. 이전 점검 대비 동한과 태현만 변경됐다.

| 저장소/브랜치 | 이전 → 현재 | 변경 |
|---|---|---|
| shared / feature/donghan-placement-planner | 146797e → 7860043228c81aec517c5a1966d475d36e12f68a | 생성기 학습 코드·실험 모델·보고서 업로드 |
| shared / claude/pensive-pasteur-dwbu3g | 098251f → e75e2744510e9e3c85af58fc8d5ac2c99c19df59 | rosdep manifest 수정, sb3 모델·평가 게시 |
| shared / feature/jaesung-dataset-generator | cc4c075daa3dd4c7f80510042732763fc9502933 | 동일 |
| shared / feature/jaesung-physics-simulator | 7f4ae37621ea20527c09a707390db9b0f1317aa3 | 동일 |
| shared / main | 1eb10252ffcbd47b1b9f24990aa6e9e5f523a0a4 | 동일 |
| ahead / main | fa3115a903117eeb57009defd12a201d5290345d | 동일 |
| ahead / fix/hyundai-submodule-init | 0c8fb80f5674e6202489209a743f8b82ce915262 | 동일 |

동한 업로드: 2026-10-08 20:21:59 KST. 팀 manifest 수정: 20:43:16 KST. SB3 게시: 20:55:05 KST.
동한 현재 tree의 blob 86개를 SHA로 대조했고 전부 일치하는 로컬 snapshot을 확보했다.
새 프로젝트 파일 17개는 앞서 학습 제공본의 파일과 SHA가 모두 일치한다. 팀 변경 UTF-8 파일 8개도 hash를 확인했다.
SB3 zip은 원격 tree/비교 결과에서 확인했으며 바이너리를 이 환경에 내려받거나 실행하지 않았다.

기존 TeamPlacer/scene_bridge/ROS service와 이번 학습 연결은 원격에 존재한다.
1905 진단 및 2004 policy handoff 후속 변경은 원격에 없으므로 이번에 7860043 기준으로 다시 묶었다.
루트 안내문에는 이미 적용된 146797e 패치를 다시 적용하라는 설명과 없는 training_result 경로가 남아 있어 수정했다.

## SB3 평가와 계약

새 sidecar는 77개 feature, 6개 action, buffer 4칸, value_provider=proxy, placer=dblf,
candidate_config=candidates.yaml 계약이다. 학습 기록: train 42 시나리오, 150,000 step,
imitation 120 episode, 관측 정규화 사용. 정규화 mean/var 길이·유한값·비음수 분산·count/eps/clip을 정적으로 검사했다.
정규화 count=163462.0001. 정책과 함께 highlevel_sb3.zip 및 highlevel_sb3.contract.json을 모두 사용해야 한다.
다른 모델의 sidecar를 섞거나 통계/계약 값을 수동으로 고치지 않는다.

팀 보고서 test 9개 시나리오×3회, 27 에피소드 결과:

| 정책 | 팔레트 상당량(낮을수록 좋음) | 실제 사용 팔레트 수 평균 | 가상 작업 시간 |
|---|---:|---:|---:|
| Rule | 4.0880 | 4.9259 | 1141 s |
| NumPy PPO | 4.2451 | 5.0741 | 1174 s |
| SB3 PPO | 4.2256 | 5.0741 | 1176 s |

SB3−Rule=+0.14, 개선10/같음3/악화14, p=0.54로 보고됐다. 기본 정책은 Rule을 유지한다.
이는 팀의 DBLF 환경 평가이며 이번 로컬 재실행 결과가 아니다. time_s는 세계의 가정된 시간이다.
동한 새 smoke 모델+SB3 전체 에피소드 성능 또는 실제 로봇 시간으로 해석하지 않는다.
PPO의 proxy+DBLF 학습 계약을 바꾸거나 기본 정책을 SB3로 바꾸지 않았다.

## 연동 영향과 수정

| 항목 | 검토 결과/동한의 최소 보완 |
|---|---|
| 중복 pac_common/pac_planning | planner 전용 workspace에 동한 패키지와 태현 후보/고수준 패키지만 5개 stage. XML상 이름 중복 없음. ahead의 동명 패키지를 동시에 넣지 않음 |
| ROS 의존성 | 동한 pac_common/package.xml, pac_planning/package.xml의 잘못된 buildtool_depend=ament_python 제거. export build_type=ament_python 유지. 태현 최신 수정과 Humble 공식 예제에 맞춤 |
| 단위/좌표 | backend/scene bridge source 변경 없음. m, kg, N, rad 유지. planner 하단 모서리를 박스 중심으로 변환한 뒤 world/TCP 변환해야 함 |
| 팔레트 크기 | ahead standalone Bullet 1.10×1.10m, workcell/default 1.20×1.00m로 여전히 다름. adapter의 불일치 거부를 유지하고 실제 scene 하나에 맞춰 상태/설정을 통일해야 함 |
| EMS evidence | 실제 EMS backend로 current/buffer snapshot 인계, 모두 EMS_SUPPLIED |
| 버퍼/state_version | buffer 4칸 정책에 fixture 맞춤. 선택된 실제 box/status/version 유지; stale snapshot 거부; 원본 상태 불변 |
| 모델 계약 | 후보 runtime/config 변경 없음. 기존 실험 모델의 backend fingerprint 일치. 학습 출처 fd683e56은 보존 |
| SB3 실행 입력 | check_policy_handoff.py --policy-kind sb3 추가. 팀의 공식 load_policy가 zip/sidecar와 정규화를 읽음. 확률 반환이 없는 SB3에서도 선택 행동 mask는 검사; 확률 검사는 NOT_PROVIDED_BY_POLICY로 구분 |
| MoveIt | ahead adapter의 backend=None이면 hdr50_22_moveit_backend_not_loaded로 실패. 실제 MoveIt 실행 backend와 성공 후 상태 commit은 이번 변경으로 추가되지 않음 |

ament_python 의존성 삭제는 manifest 수준 수정이다. rosdep/colcon 성공을 확인한 것은 아니다.
ahead 쪽 다른 package.xml에도 같은 표기가 남아 있으나 팀원 영역은 수정하지 않았다.

기존 불확실 관측 XY_MARGIN_NOT_COVERED 문제는 미해결이다. 중심 기준 필요 여유 6.025mm에
현재 경계 여유 4mm가 부족하다. 후속 진단/회귀 검사를 복원했으며 팀 설정을 임의 변경하지 않았다.
물리 repo의 0N 입력 허용 패치는 별도 협의/적용 후 검사해야 한다. 동한 브랜치에 패치 파일이 있는 것과
실제 시뮬레이터 구현에 적용된 것은 다르다.

## 재현: Python 연결 검사

PAC_PLANNER_ROOT는 동한 저장소 안의 pac-mission1-shared 폴더,
PAC_TEAM_ROOT는 최신 태현 checkout의 config/taehyeon이 보이는 루트다.
두 checkout은 별도 디렉터리/worktree로 준비한다. --team-ref는 기록용이며 checkout을 대신하지 않는다.

```bash
cd "$PAC_PLANNER_ROOT"
python3 scripts/donghan/check_policy_handoff.py \
  --team-root "$PAC_TEAM_ROOT" \
  --team-ref e75e2744510e9e3c85af58fc8d5ac2c99c19df59 \
  --policy-kind numpy \
  --model models/team_fd683e56_smoke.json \
  --planner-config config/team_fd683e56_smoke.yaml \
  --output reports/highlevel_handoff_local.json

export PYTHONPATH="$PAC_PLANNER_ROOT/ros2_ws/src/pac_common:$PAC_PLANNER_ROOT/ros2_ws/src/pac_planning:$PAC_TEAM_ROOT/ros2_ws/src/pac_candidates:$PAC_TEAM_ROOT/ros2_ws/src/pac_highlevel"
python3 -m pytest tests/test_team_training.py tests/test_team_bridge.py tests/test_highlevel_handoff.py -q
```

실행 결과: Python3.12.14 / NumPy2.3.5, 21 passed, 0.40초.
NumPy snapshot 2건 PASS: PLACE_CURRENT, RETRIEVE_BUFFER(0), ranked_count=4,
EMS_SUPPLIED, model_status=PROVIDED, state_unchanged=true. stale snapshot 2건과 공급자 불일치 1건 거부.
관련 코드에 앞선 1905/2004 수정이 포함된 상태로 검사했다. 전체 suite, 새 학습, 80박스 재평가는 하지 않았다.

SB3 확인은 PyTorch 설치된 학습용 venv에서 아래 명령으로 추가 실행해야 한다. 이번 환경에서는 미실행이다.
팀장 checkout에 실제 zip과 동일 버전 sidecar가 모두 필요하다. ROS 시스템 Python의 NumPy를 교체하지 않는다.

```bash
python3 -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install sb3-contrib gymnasium
python3 scripts/donghan/check_policy_handoff.py \
  --team-root "$PAC_TEAM_ROOT" \
  --team-ref e75e2744510e9e3c85af58fc8d5ac2c99c19df59 \
  --policy-kind sb3 \
  --model models/team_fd683e56_smoke.json \
  --planner-config config/team_fd683e56_smoke.yaml \
  --output reports/highlevel_sb3_handoff_local.json
```

fixture에서 BUFFER/CLOSE 등 행동을 선택하면 저수준 인계에 도달하지 않아 검사 실패할 수 있다.
이는 선택 행동을 강제로 바꾸어 통과시킬 문제가 아니다. 보고된 action/mask를 검토하고 실제 여러 snapshot을 추가 평가한다.
확률을 제공하지 않는 SB3 경로는 NOT_PROVIDED_BY_POLICY로 표시되고 정책 행동 mask/box/version 검사는 유지된다.
새 옵션의 SB3 실제 모델 로드/추론 성공은 아직 검증하지 않았다.

## 재현: ROS2 Humble에서 다음에 검사할 부분

Ubuntu22.04 / ROS2 Humble 시스템 Python 환경에서, 새 planner 전용 workspace로 의존성 설치·build·service launch부터 확인한다.
아래는 제안 명령이며 이번 실행 결과가 아니다. pip가 없다면 Ubuntu에서 python3-pip/python3-venv 설치가 먼저 필요하다.

```bash
source /opt/ros/humble/setup.bash
cd "$PAC_PLANNER_ROOT"
python3 scripts/donghan/stage_planner_workspace.py \
  --team-root "$PAC_TEAM_ROOT" --include-highlevel \
  --output "$HOME/AHEAD/placement_ws_2118"
cd "$HOME/AHEAD/placement_ws_2118"
rosdep install --from-paths src --ignore-src -r -y --rosdistro humble
colcon build --symlink-install
source install/setup.bash
ros2 launch pac_planning placement_planner.launch.py \
  candidate_config:="$PAC_TEAM_ROOT/config/taehyeon/candidates.yaml" \
  planner_config:="$PAC_PLANNER_ROOT/config/team_fd683e56_smoke.yaml" \
  model_path:="$PAC_PLANNER_ROOT/models/team_fd683e56_smoke.json" \
  use_sim_time:=false
```

output은 새/빈 경로여야 한다. standalone Bullet에 ROS /clock이 없는 이 단계는 use_sim_time=false다.
후에 실제 ROS simulator /clock을 연결하면 노드들이 같은 시간원을 사용하도록 맞춘다.
정상 확인 목표: dependency 설치 → 5개 패키지 build → planner node/service 등록 → 실제 요청 응답.
이 작업공간에는 로봇/State Manager가 없다. planning service가 뜬 것만으로 로봇이 움직이는 것이 아니다.

그 다음 팀원과 로봇 backend를 연결한다: 팔레트·박스·컨베이어 planning scene 반영, TF/박스 중심→TCP,
선택 후보의 IK/접근/충돌 검사, Home→Pre-grasp→Grasp→Lift→Pre-place→Place→Release→Retreat 실행,
그립 attach/detach 및 시뮬레이터 물체 상태 확인, 성공 확인 후 새 state_version을 가진 실제 snapshot commit.
실패했으면 가상 배치를 실제 배치로 commit하지 않는다. 이후 같은 pipeline으로 5~10박스 연속 시험한다.

## 검증 상태

| 구분 | 이번 결과 |
|---|---|
| Python 관련 회귀 | 21 passed |
| NumPy 정책→동한 모델 snapshot | 2건 PASS |
| SB3 metadata/정규화 | 정적 검사 PASS; zip 추론 NOT_RUN |
| package.xml / workspace 구조 | XML 검사 PASS; 5개 이름 중복 없음 |
| rosdep / colcon / ROS service | NOT_RUN |
| MoveIt / controller / 실제 robot pick-place | NOT_RUN |
| 새로운 물리 실험 / 학습 / 전체 장기 성능 평가 | NOT_RUN |

## 패치 적용

이번 ZIP의 patches/after_remote_786004.patch 한 개는 최신 동한 원격 7860043에서 시작하며
1905/2004 미반영 변경과 이번 최소 보완을 포함한다. 예전 146797e 누적 패치와 함께 적용하지 않는다.
저장소 최상위(pac-mission1-shared 폴더의 부모)에서 git apply --check 후 적용한다.
과거 후속 패치를 이미 로컬에 적용했다면 이 누적 패치를 중복 적용하지 말고 현재 diff와 files/를 대조한다.
GitHub 쓰기/병합은 시도하지 않았다.

## 출처

- [동한 업로드](https://github.com/yang8988/pac-mission1-shared/commit/7860043228c81aec517c5a1966d475d36e12f68a)
- [팀 rosdep 수정](https://github.com/yang8988/pac-mission1-shared/commit/197ae202ed2dc3a960028ba55f4fce18077b23ad)
- [팀 SB3 게시](https://github.com/yang8988/pac-mission1-shared/commit/e75e2744510e9e3c85af58fc8d5ac2c99c19df59)
- [팀 최신 평가](https://github.com/yang8988/pac-mission1-shared/blob/e75e2744510e9e3c85af58fc8d5ac2c99c19df59/docs/taehyeon/highlevel.md)
- [SB3 sidecar](https://github.com/yang8988/pac-mission1-shared/blob/e75e2744510e9e3c85af58fc8d5ac2c99c19df59/ros2_ws/src/pac_highlevel/models/highlevel_sb3.contract.json)
- [Humble 공식 ament_python 예제](https://github.com/ros2/examples/blob/humble/rclpy/topics/minimal_publisher/package.xml)
