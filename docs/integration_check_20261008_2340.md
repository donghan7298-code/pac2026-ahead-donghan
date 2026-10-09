# 태현 runtime·6단계와 동한 5-③~⑥ 연동 점검 — 2026-10-08

## 변경 기준

- 동한: `7860043228c81aec517c5a1966d475d36e12f68a` 그대로다.
- 태현: `e75e2744510e9e3c85af58fc8d5ac2c99c19df59` → `e8674da49d78b096016ef46b978524542445ada2`.
- 생성기 `cc4c075d`, shared 물리 `7f4ae376`, shared main `1eb10252`, 별도 ahead main `fa3115a9`, fix `0c8fb80f`는 그대로다.

태현 브랜치에는 11개 커밋, 54개 파일 변경으로 `pac_robot_check`(6단계)와
`pac_runtime`(1~8 loop, 상태 관리자, ROS JSON bridge), PyBullet 재생 결과와 SB3 학습 보조 코드가 추가됐다.
최신 커밋 시각은 2026-10-08 22:59:37 KST다.

## 연동 결론

새 runtime의 기본 정책은 Rule이고 PPO의 게시 계약 `slots=4`, `value_provider=proxy`,
`placer=dblf`, `candidate_config=candidates.yaml`은 바뀌지 않았다. 새 SB3 학습 코드는
rule-relative reward, Rule BC, validation checkpoint 선택을 추가했지만 새 모델은 이번 변경에 없다.
학습된 PPO를 robot-aware 또는 동한 placer 환경에서 사용하려면 기존 DBLF 계약을 이름만 바꾸지 말고
별도 평가/재학습해야 한다. 지금은 Rule을 유지한다.

팀 runtime의 기존 `donghan_ranker`는 `PlacementPlanner`를 직접 만들어 `backend.context`를 썼다.
이 경로는 `CandidateBackend.context_with_ems(...)`와 `backend_contract` 검사를 우회하므로
`EMS_SUPPLIED` 및 학습 모델/backend 일치가 보장되지 않았다. 동한 쪽에 `TeamRuntimeRanker`를 추가해
`plan_with_backend` 한 경로만 사용하도록 했다. 특징 입력에서 거부된 후보를 DBLF fallback으로 되살리지 않는다.

팀 runtime은 오래된 명령 결과를 집계만 하고 실제 상태에 계속 적용할 수 있었다.
새 관측 등으로 `state_version`이 바뀌었다면 `STALE_RESULT`로 거부하는 최소 upstream 패치를 별도로 제공한다.
ROS bridge가 결과 메시지와 명령 version만 비교하는 것으로는 현재 State Manager version을 보장할 수 없다.

## 인터페이스 점검

| 항목 | 결과 |
|---|---|
| 패키지 중복 | 동한 `pac_common/pac_planning/interfaces` + 태현 `candidates/highlevel/robot_check/runtime`, 총 7개 이름 중복 없음 |
| 좌표·단위 | 후보는 `pallet` frame 최소 모서리, m/kg/N/rad. runtime command는 최소 모서리와 박스 중심을 모두 출력 |
| 팔레트 | runtime은 주문/시나리오 규격을 사용하고 robot check도 state 규격을 받음. 별도 ahead의 1.10×1.10과 workcell 1.20×1.00 불일치는 그대로라 실제 scene 하나로 확정 필요 |
| EMS | `TeamRuntimeRanker` 단일 박스 chain에서 모든 평가 `EMS_SUPPLIED` 확인 |
| 버퍼/version | 4칸 계약 유지. command/state version 일치 확인. stale 실행 결과 거부 패치와 회귀 검사 추가 |
| 0 N | runtime `place_no_load`가 capacity override 0 N을 전달. 실제 재성 simulator에 0 N 패치가 적용됐는지는 별도 |
| 6단계 | HDR50-22 해석 IK, 하강·캡슐 충돌, payload, 예상 시간. MoveIt 메시 충돌/실제 trajectory는 아님 |
| 상태 commit | Python runtime은 실측 pose와 version을 갱신. 실제 센서·controller 성공 결과와 연결된 것은 아님 |

HDR50-22 정격 50 kg에서 gripper 질량 가정 15 kg을 빼면 현재 검사상 박스 한계는 35 kg이다.
생성기 최대 30 kg은 통과 범위지만 그리퍼 질량은 실측 전 가정이다.

## 동한 수정 파일

- `pac_planning/team_bridge.py`: 순서 목록을 반환하는 `TeamRuntimeRanker`, 실제 EMS·모델 fingerprint 경로 재사용.
- `pac_planning/__init__.py`: runtime에서 public import 가능하도록 export.
- `scripts/donghan/stage_planner_workspace.py`: `--include-runtime`으로 7개 패키지만 stage.
- `tests/test_team_bridge.py`: EMS, 상태 불변, 로봇 검증 필요 플래그 회귀 검사.
- 이 문서와 `reports/runtime_integration_20261008_2340.json`.

별도 upstream 검토 패치는 태현 `e8674da4` 기준으로 `pac_runtime/placer.py`,
`pac_runtime/core.py`, `pac_runtime/package.xml`, `test_th_runtime.py` 네 파일만 수정한다.
팀 영역을 자동 병합하지 않았다.

## 실행 및 결과

```bash
export PAC_PLANNER_ROOT=/경로/donghan/pac-mission1-shared
export PAC_TEAM_ROOT=/경로/taehyeon
cd "$PAC_PLANNER_ROOT"
export PYTHONPATH="$PAC_PLANNER_ROOT/ros2_ws/src/pac_common:$PAC_PLANNER_ROOT/ros2_ws/src/pac_planning:$PAC_TEAM_ROOT/ros2_ws/src/pac_candidates:$PAC_TEAM_ROOT/ros2_ws/src/pac_highlevel:$PAC_TEAM_ROOT/ros2_ws/src/pac_robot_check:$PAC_TEAM_ROOT/ros2_ws/src/pac_runtime"
python3 -m pytest tests/test_team_training.py tests/test_team_bridge.py tests/test_highlevel_handoff.py -q
```

결과: 22 passed, 0.71 s. 태현 runtime/robot 관련 검사와 stale-result 회귀 검사는
최종 패치 기준 25 passed, 7.37 s. Python 3.12.14에서 실행했다.

단일 박스 Python chain은 `PLAN → PLACE_CURRENT`, command/state version=1,
동한 ranked 후보 4개, 모든 특징 `EMS_SUPPLIED`, stage-6 `q_place/q_approach` 산출을 확인했다.
실행 전이라 팔레트 실제 상태는 commit하지 않았다.

7개 패키지 workspace 생성:

```bash
python3 scripts/donghan/stage_planner_workspace.py \
  --team-root "$PAC_TEAM_ROOT" --include-runtime \
  --output "$HOME/AHEAD/runtime_ws"
```

XML 정적 검사에서 7개 package 이름은 유일했고 잘못된 `buildtool_depend=ament_python`은 없었다.
Python compile 검사도 통과했다. 그러나 이 환경에는 ROS2 Humble이 없어 `rosdep`, `colcon`,
launch, topic/service/action을 실행하지 않았다.

## 아직 검증되지 않은 부분

- 새 SB3 코드의 PyTorch 학습·추론과 새 정책 성능. 게시 모델은 이전 모델이다.
- `rule-baseline`은 episode 총합에서는 Rule 차이를 만들지만 gamma<1의 discounted objective에서는
  마지막 step에 뺀 baseline이 `gamma^(T-1)`로 할인된다. 현재 테스트는 gamma=1만 확인하므로 새 학습 전 검토가 필요하다.
- MoveIt2 planning scene, mesh/self collision, controller trajectory, attach/detach, 실제 TCP/TF.
- 팀 `pac_runtime` ROS node는 현재 `RuntimeCore`를 `ranker=None`으로 생성하므로,
  `pac_planning`을 함께 빌드해도 ROS 실행 경로는 아직 DBLF다. 이번 검증은 Python 주입 경로와
  offline runtime CLI까지이며, ROS node에는 `ranker=donghan` 파라미터 또는 planning service 연결 후
  ROS 2 Humble에서 다시 검증해야 한다.
- 실제 로봇·카메라·저울 및 여러 박스 연속 pick/place.
- 태현 보고의 47/47 PyBullet 안정은 게시 보고서 결과이며 이번 로컬 재실행이 아니다.
- 불확실 박스의 XY 필요 여유 6.025 mm 대비 현재 4 mm 문제는 별개로 미해결이다.

## 출처

- 태현 최신: https://github.com/yang8988/pac-mission1-shared/commit/e8674da49d78b096016ef46b978524542445ada2
- 변경 비교: https://github.com/yang8988/pac-mission1-shared/compare/e75e2744510e9e3c85af58fc8d5ac2c99c19df59...e8674da49d78b096016ef46b978524542445ada2
- runtime: https://github.com/yang8988/pac-mission1-shared/blob/e8674da49d78b096016ef46b978524542445ada2/docs/taehyeon/runtime.md
- robot check: https://github.com/yang8988/pac-mission1-shared/blob/e8674da49d78b096016ef46b978524542445ada2/docs/taehyeon/robot_check.md
