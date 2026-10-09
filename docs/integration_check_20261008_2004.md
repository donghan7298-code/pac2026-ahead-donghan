# PPO 재학습 파일과 저수준 planner 연결 점검 — 2026-10-08

## 변경 기준

팀장 브랜치 1782d631 → 098251f9fd7114a6653c40ba55a2c5a35b9001c8.
실제 재학습 게시 커밋 c6440576: 2026-10-08 19:13:07 KST.
최신 문서 커밋 098251f9: 19:13:20 KST. 문서/보고서 5개와 PPO 모델 1개가 변경됐다.
양쪽 저장소의 모든 7개 브랜치 HEAD를 읽었고, 나머지 6개는 직전 점검과 같다.

동한 원격: 146797e337d9b17c521b3ddb1b8560453cddb1bf.
생성기: cc4c075daa3dd4c7f80510042732763fc9502933.
shared 물리: 7f4ae37621ea20527c09a707390db9b0f1317aa3.
shared main: 1eb10252ffcbd47b1b9f24990aa6e9e5f523a0a4.
별도 ahead main: fa3115a903117eeb57009defd12a201d5290345d.
별도 ahead fix/hyundai-submodule-init: 0c8fb80f5674e6202489209a743f8b82ce915262.

## 새 정책과 보고서의 의미

새 NumPy PPO 파일은 obs_dim=77, actions=6, slots=4, value_provider=proxy,
placer=dblf, candidate_config=candidates.yaml을 유지한다.
모델 trained_on에는 train 시나리오 42개, steps=60000, imitation_episodes=120이 기록됐다.
팀 문서는 test 9 시나리오×3회(27 에피소드)에서 Rule 4.09, PPO 4.25 팔레트 상당량으로
보고하며 Rule을 기본값으로 유지한다. 이 수치는 팀 보고서이며 이번 로컬 재평가가 아니다.

팀의 추가 동한 planner 평가(test 4 시나리오)는 Rule 3.97, PPO 4.14 팔레트 상당량이다.
보고서에 사용 모델이 dual_head_ranker.json이라고만 기록되어 있어, 동한의 새 생성기 기반
team_fd683e56_smoke.json 평가 결과와 동일시하지 않는다. 또한 time_s는 세계의 가정된
작업 시간이며 실제 로봇 실측이나 planner 계산 시간(sec_per_ep)과 구분한다.

## 연동 영향 검토

| 항목 | 이번 변경의 영향 |
|---|---|
| pac_common/pac_planning 중복 | 코드 변경 없음. 검증 스크립트는 동한 checkout의 두 패키지를 우선 import |
| 단위·좌표·팔레트 규격 | 코드/설정 변경 없음. 기존 TF/TCP와 팔레트 단일 설정 작업 유지 |
| EMS evidence | CandidateBackend 코드/설정 변경 없음. 새 정책 전달 결과 모두 EMS_SUPPLIED |
| 버퍼·state_version | 새 파일은 버퍼 4칸 계약. BUFFERED 박스와 snapshot 버전 유지, 오래된 snapshot 거부 |
| PPO 계약 | proxy+DBLF 유지. donghan 공급자로 설정을 바꾸면 모델 로드 거부 |
| 저수준 모델 | 현재 실제 후보 코드/설정 fingerprint와 기존 실험 모델이 일치하여 명시 로드 성공 |
| 실제 MoveIt | 코드 변경 없음. 로봇 검증·실행·성공 후 상태 commit 미구현/미검증 범위 유지 |

새 정책의 메타데이터를 고치거나 학습 입력을 변경하지 않았다. planner로 최종 위치를
바꾸는 것은 DBLF 학습 환경과 완전히 같다는 뜻이 아니다. 이번 검사는 snapshot 전달만
검증한다. 모든 안전 조건이 실제 크기까지 보장된다는 뜻도 아니며, 직전 점검에서 재현한
불확실 박스의 XY 안전 여유 부족은 그대로 미해결이다.

## 동한 범위의 최소 보완

- scripts/donghan/check_policy_handoff.py: 게시된 실제 정책과 선택한 저수준 모델을 읽어
  현재/버퍼 박스 → HighLevelDecider → 실제 EMS backend → 저수준 ranking을 재현한다.
  후보의 box_id, state_version, EMS, 원본 상태 유지, stale snapshot/공급자 불일치 거부를 검사한다.
- docs/meeting_handoff_20261008.md: 재학습 진행 중이라는 오래된 설명을 게시 완료로 갱신하고,
  실제 generator teacher adapter가 이미 연결된 상태와 원격 미반영 상태를 구분했다.
- reports/highlevel_handoff_20261008.json: 실제 새 정책 파일 hash와 연결 결과를 기록한다.
- 이 문서: 팀 보고서, 로컬 검증, 실행 미검증 범위를 구분한다.

## 실행 명령

이전 generator 학습 패치를 적용한 동한 checkout과 팀장 checkout이 필요하다.
팀장 checkout은 098251f9 커밋의 코드/설정/모델을 사용한다. 같은 원격 저장소의 다른
브랜치이므로 각각 별도 디렉터리 또는 worktree로 준비한다.

```bash
cd "$PAC_PLANNER_ROOT"
python3 scripts/donghan/check_policy_handoff.py \
  --team-root "$PAC_TEAM_ROOT" \
  --team-ref 098251f9fd7114a6653c40ba55a2c5a35b9001c8 \
  --model models/team_fd683e56_smoke.json \
  --planner-config config/team_fd683e56_smoke.yaml \
  --output reports/highlevel_handoff_local.json
```

정상 결과: status=PASS, PLACE_CURRENT와 RETRIEVE_BUFFER(0), 각 ranked_count=4,
EMS_SUPPLIED, PROVIDED, state_unchanged=true. 버퍼 2칸의 기본 fixture를 이번 검사에서
명시적으로 정책의 4칸에 맞췄다. 실제 운영의 capacity가 다르면 모델 계약을 우회하지 말고
공통 설정/정책을 맞춘다. 다른 정책 파일의 의사결정은 달라질 수 있으며 이 스크립트는
위 fixture에서 저수준 전달에 도달해야 통과한다.

관련 회귀 검사는 다음과 같다.

```bash
export PYTHONPATH="$PAC_PLANNER_ROOT/ros2_ws/src/pac_common:$PAC_PLANNER_ROOT/ros2_ws/src/pac_planning:$PAC_TEAM_ROOT/ros2_ws/src/pac_candidates:$PAC_TEAM_ROOT/ros2_ws/src/pac_highlevel"
python3 -m pytest tests/test_team_training.py tests/test_team_bridge.py tests/test_highlevel_handoff.py -q
```

이번 결과: 21 passed, 0.39초. 새 정책 snapshot 검사 2건 모두 PASS, stale snapshot 2건과
value_provider 변경 1건 거부 확인. Python 3.12.14 / NumPy 2.3.5에서 실행했다.
이전 Python 3.10 가상환경 실행 파일이 없어 이번에는 현재 Python으로 확인했으며,
pytest는 기존 순수 Python 패키지로 실행했다. Humble의 Python 3.10에서 다시 확인해야 한다.
전체 테스트나 80박스 성능 평가는 다시 실행하지 않았다. 새 학습도 실행하지 않았다.

## 패치 적용과 미검증

after_1905.patch는 이전 1905 패치까지 적용한 checkout용이다.
from_remote_146797e.patch는 동한 원격 146797e 기준 전체 후속 변경을 포함한다.
하나만 선택하고 팀원 파일에 덮어쓰지 않는다. 저장소 최상위(pac-mission1-shared 폴더의
부모)에서 git apply --check 후 적용한다. 같은 경로에 이미 수정사항이 있다면 먼저 diff를 검토한다.

ROS2 Humble 빌드·노드·service, MoveIt, 실제 controller, 새 물리 실험, pick/place,
여러 박스 PPO+새 저수준 모델의 운영 성능은 미검증이다. 원격 쓰기/병합은 시도하지 않았다.

## 출처

- [변경 비교](https://github.com/yang8988/pac-mission1-shared/compare/1782d631c86781d4dc938ec3acfb56a1a504e121...098251f9fd7114a6653c40ba55a2c5a35b9001c8)
- [재학습 게시 커밋](https://github.com/yang8988/pac-mission1-shared/commit/c6440576167a875c581d4b33f4e77b9bcdb721e2)
- [정책 파일](https://github.com/yang8988/pac-mission1-shared/blob/098251f9fd7114a6653c40ba55a2c5a35b9001c8/ros2_ws/src/pac_highlevel/models/highlevel_ppo.json)
- [팀 평가 설명](https://github.com/yang8988/pac-mission1-shared/blob/098251f9fd7114a6653c40ba55a2c5a35b9001c8/docs/taehyeon/highlevel.md)
- [팀 동한 placer 평가](https://github.com/yang8988/pac-mission1-shared/blob/098251f9fd7114a6653c40ba55a2c5a35b9001c8/docs/taehyeon/reports/highlevel_eval_donghan_placer.json)
