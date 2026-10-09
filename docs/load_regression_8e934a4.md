# 2026-10-08 하중 검사 변경과 동한 파트 연동 검증

## 최종 검증 기준: e7ed1f9

작업 중 태현 브랜치가 다시 갱신돼 최종적으로
`e7ed1f93fa617d9b61006db036d0637f9252a35b`(2026-10-08 17:36:35 KST)로 고정했다.
아래 8e934a4 기록은 중간 검증 이력이다.

- 추가 비교: https://github.com/yang8988/pac-mission1-shared/compare/8e934a43eaed64cedd41a128243c70ecc9bf8e7d...e7ed1f93fa617d9b61006db036d0637f9252a35b
- 5-①: balance 탐색 중단 기준을 정확한 마스크로 확인하고, 허용 yaw의 같은 footprint를 매칭한다.
- 4번: 현재/버퍼 박스 모두 배치 불가이고 충전율 30% 이상이면 버퍼 강제 투입 대신 마감한다.
- `features.order_list_known=false`이면 잔여 수량을 4번과 5번 모두에 숨긴다.
- value_provider 생성과 설정 일치, 첫 결정 전 NG 보상 반영, repack 반복 제한,
  PPO log 확률 하한, gym reset 오류 처리가 수정됐다.
- `docs/taehyeon/PROGRESS.md`는 기존 PPO가 수정 전 환경에서 학습됐으며
  재학습이 팀원 요청으로 중단됐다고 명시한다. 이번 작업에서 재학습을 시작하지 않았다.
- 팀 문서의 planner 평균 689ms / 소프트 예산 초과 6/24는 팀 측 측정 보고다.
  여기서 다시 측정한 수치가 아니며, 1초의 엄격한 실시간 보장을 뜻하지 않는다.

동한 쪽 추가 보완:

- `tests/test_team_bridge.py`: 0°, 180°, −90°, 270° 허용 yaw로 실제 EMS → 평가 경로 검증.
- `tests/test_team_order_visibility.py`: 실제 high-level에서 주문 수량 공개/비공개 설정이
  planner snapshot과 미래 시나리오까지 전달되는지 확인. 숨겨진 수량을 재구성하지 않는다.
- 기존 테스트 포함 **57 passed (7.37초)**. 실제 물리 계산 검사 3개가 포함된다.
- 최신 태현 테스트 **160 passed, 2 skipped (36.21초)**. gymnasium/torch 관련 검사만 미실행.
- 최신 5박스 Rule → TeamPlacer 재실행도 **5/5 가상 배치**, safety issues 0.
- 계산식, PPO proxy+DBLF 계약, 팀원 모듈은 수정하지 않았다.

## 확인한 변경

태현 브랜치의 `c0f32d611b9bb5721fc2e71f1fcab1ed1b17ddc0` 이후
`8e934a43eaed64cedd41a128243c70ecc9bf8e7d` 1개 커밋을 확인했다.
커밋 시각은 2026-10-08 17:16:51 KST다.

- 비교: https://github.com/yang8988/pac-mission1-shared/compare/c0f32d611b9bb5721fc2e71f1fcab1ed1b17ddc0...8e934a43eaed64cedd41a128243c70ecc9bf8e7d
- `pac_candidates/{backend,geometry,hard_mask,loads,pallet_model}.py`와
  `tests/taehyeon/test_th_hard_mask.py` 총 6개 파일이 변경됐다.
- 각 지지 박스의 실제 하중 지지 영역(LBCP)으로 접촉을 잘라 면적·중심을 계산한다.
- 아래 박스의 합력이 지지 영역을 벗어나는 경우를 검사한다.
- share 방식에서는 이미 분담률을 곱한 하중을 비교하므로, 작은 분담률도 검사한다.
- 일직선 지지점의 하중 분배, 용량 초과 판단, metrics 복사도 수정됐다.

다른 shared 4개 브랜치와 별도 ahead 2개 브랜치는 이전 확인 SHA와 동일했다.
전체 SHA는 배포 ZIP의 `verification/source_commits.json`에 기록했다.

## 동한 담당 범위에 한 최소 변경

`tests/test_team_load_regression.py`에 실제 pac_candidates를 호출하는 검사 2개를 추가했다.
5-③~⑥은 이미 전달받은 validator 결과를 사용하므로 계산식·가중치·PPO 계약은 변경하지 않았다.

1. 현재 후보: 0.5kg 지지 박스가 작은 접촉 비율로 약 3.4kg을 부담하는 사례가
   `HEAVY_ON_LIGHT:light`로 탈락하고 점수를 받지 않는지 확인한다.
2. 미래 rollout: 동일한 불가능 배치가 미래 적재량으로 계산되지 않는지 확인한다.
   이 테스트는 미래 후보를 한 개로 제한하지만 validity 판정은 실제 팀 backend를 사용한다.

검증 순서와 실제 결과:

- 첫 테스트 작성 시 reason 문자열에 박스 ID 접미사가 포함된 것을 확인해 기대값을 고쳤다.
- 정확한 upstream diff를 역적용한 c0f32d6 후보 모듈: 새 검사 **2개 실패**.
  현재 후보는 순위에 남았고, 미래 rollout도 적재 1개로 계산했다.
- 8e934a4 후보 모듈 + 동한 패치: 새 검사 포함 **52 passed (6.10초)**.
- 태현 테스트: **152 passed, 2 skipped (32.00초)**.
  첫 실행은 PAC_COMMON_SRC 누락으로 수집에 실패했고 명시 설정 후 실행했다.
  gymnasium/torch가 없어 선택적 2개는 실행하지 않았다.
- 생성기 S0001 앞 5개 → high-level Rule → TeamPlacer: **5/5 가상 배치**, NG 0,
  snapshot safety issues 0. 단일 낮은 층 사례라 복잡한 다층 적재 전체를 보증하지 않는다.
  표시된 40초는 high-level 비용 가정이며 로봇 실측 시간이 아니다.
- 52개에는 실제 PyBullet solid/slatted 단일 박스 안정화 검사와 0N 검사가 포함된다.
  단일 박스를 생성한 접촉 시험이며 로봇 pick & place 실행은 아니다.
- Ubuntu 24.04 / Python 3.10.22에서 실행했다. ROS2 Humble은 설치되어 있지 않다.

## 연동 경계 재검토

| 항목 | 이번 변경 영향 / 유지할 조치 |
|---|---|
| pac_common/pac_planning 중복 | 변경 없음. planner 작업공간에는 동한 공통·계획 패키지만 연결한다. |
| 단위·좌표·팔레트 | 변경 없음. m/kg/N/rad와 pallet 하단 모서리 기준 유지. Gazebo 1.2×1.0m와 Bullet 기본 1.1×1.1m 불일치 해결 필요. |
| EMS/evidence | 타입·호출 계약 유지. 미게시 TeamPlacer가 실제 EMS를 전달하고 새 validator evidence를 사용함을 재검증했다. |
| 버퍼/state_version | 4슬롯·stale 후보 검사가 기존 52개 검사에 포함된다. 실제 실행 완료 시 commit과 중복 실행 차단은 별도 작업이다. |
| PPO | proxy+DBLF 메타데이터는 그대로다. 하중 마스크·분배 변경으로 유효 행동/관측/전이가 달라질 수 있어 기존 정책 성능 재평가가 필요하다. 이번에 PPO 모델 실행·재학습하지 않았다. |
| MoveIt | 변경 없음. 실제 backend, TCP/TF 변환, planning scene, controller 실행 연결이 여전히 남아 있다. |

## 재현 명령

`PAC_PLANNER_ROOT`는 패치를 적용한 동한 저장소의 `pac-mission1-shared` 하위 디렉터리,
`PAC_TEAM_ROOT`는 태현 checkout 루트로 설정한다. 기존 태현 작업 브랜치를 덮어쓰지 않도록
별도 checkout에서 최종 e7ed1f9로 고정한다. 동한 기본 패치 적용법은 ZIP의 APPLY_GUIDE_KO.md를 따른다.

```bash
git -C "$PAC_TEAM_ROOT" switch --detach e7ed1f93fa617d9b61006db036d0637f9252a35b
export PAC_COMMON_SRC="$PAC_PLANNER_ROOT/ros2_ws/src/pac_common"
export PAC_PLANNING_SRC="$PAC_PLANNER_ROOT/ros2_ws/src/pac_planning"
export PYTHONPATH="$PAC_COMMON_SRC:$PAC_PLANNING_SRC:$PAC_TEAM_ROOT/ros2_ws/src/pac_candidates:$PAC_TEAM_ROOT/ros2_ws/src/pac_highlevel"
cd "$PAC_PLANNER_ROOT"
python -m pytest tests/test_team_load_regression.py -q
python -m pytest -q -rs
cd "$PAC_TEAM_ROOT"
python -m pytest tests/taehyeon -q -rs
```

정상 시 새 회귀 테스트는 `2 passed`다. 물리 패키지를 연결하지 않은 전체 실행에서는
선택적 물리 테스트가 skip될 수 있으므로 `57 passed`를 무조건 기대하지 않는다.
실제 물리 검증 설정은 `team_integration_20261008.md`의 0N 검토 패치와 설치 절차를 따른다.

## 바로 다음 검증

1. Ubuntu 22.04/Humble에서 기존 미게시 ROS service 패치를 빌드하고 실제 요청/응답 확인.
2. 팀 작업셀 팔레트 크기·적재면·허용높이 설정 확정 및 planning scene 연결.
3. MoveIt 단일 박스 pick/place 성공 후 실제 관측으로 상태 갱신, 5~10박스 반복.
4. 새 5-② 조건에서 기존 proxy+DBLF PPO 성능 재평가. TeamPlacer 전환은 별도 평가/학습.

GitHub 쓰기 403은 재시도하지 않았다. 팀원 파일을 재작성하거나 main에 병합하지 않았다.
