# 연동 변경 점검 — 2026-10-08

## 변경 기준

직전 팀장 fd683e56 → 최신 1782d631c86781d4dc938ec3acfb56a1a504e121.
최신 문서 커밋 시각: 2026-10-08 18:47:59 KST.
변경은 docs/taehyeon의 문서/검증 보고서 9개뿐이다. 실행 코드, candidate config,
PPO 정책 파일은 바뀌지 않았다. 나머지 shared 4브랜치와 ahead 2브랜치 HEAD는 그대로다.

현재 동한 원격은 146797e이고 이전 학습/추가 패치는 아직 원격 미반영이다.
기존 TeamPlacer/scene_bridge/service와 0N 검토 패치 파일은 이미 게시된 상태다.
0N 패치가 실제 물리 코드에 적용됐다는 뜻은 아니다.

## 변경 내용과 영향

1. PPO 재학습 상태가 중단 → 진행 중으로 갱신됐다. 게시 모델은 여전히 리뷰 이전 환경의
   정책이라고 명시돼 있다. proxy+DBLF 계약을 유지하며 새 모델 게시/평가를 기다린다.
2. 제공 동한 실험 모델의 backend fingerprint와 실제 최신 코드/설정은 일치한다.
   문서 변경 때문에 모델을 재학습하거나 출처 커밋을 바꿀 필요는 없다.
3. 팀 측 planner 보고서: 평균 765 ms, p95 1073 ms, 최대 1300 ms, soft budget 초과 7/23.
   이 수치를 이번 동한 로컬 패치/모델의 재측정 결과로 취급하지 않는다.
4. 팀 측 Bullet 보고서: solid/slatted 각각 valid 82/82 안정. 여기서는 새 물리 실행을 하지 않았다.
5. 80박스×12시나리오 보고서의 T11 그룹에 실제 크기 XY 돌출 1건이 있다.
   같은 보고서의 다른 안전 지표가 0이라고 실제 크기 경계까지 0건이라고 말하면 안 된다.

## 독립적으로 재현한 반례

팀장 설정에서 불확실 박스의 size tolerance는 2 mm×2=4 mm다.
가상 관측은 sigma=4 mm, clip=3 sigma로 최대 12 mm의 크기 오차를 허용한다.
실제 폭 400 mm가 388 mm로 관측되면 중심 기준 실제 가장자리는 관측보다 6 mm 바깥이다.
현재 CandidateBackend의 유효 후보 x=4 mm를 실제 폭으로 환산하면 x=-2 mm이다.
mask 통과와 실제 크기 경계 위반을 Python으로 확인했다. 로봇/물리 실행은 하지 않았다.
보고서의 정확한 원래 사례는 JSON 장면이 없어 확인하지 못했다.

## 동한 범위의 최소 보완

- team_training.py: 공개 관측 오차 상한과 후보 XY 여유의 정합성 진단.
- train_team_model.py: 진단을 실행 로그와 training_provenance에 기록.
- test_team_training.py: 실제 backend 반례와 진단의 회귀 검사 2개 추가.
- 회의 자료와 학습 안내: PPO 진행 상태, 모델 호환, 경계 미해결 범위 갱신.

진단은 자동으로 팀 설정을 변경하지 않는다. 반올림 오차를 포함해 불확실 박스의
필요 XY 여유가 6.025 mm인데 4 mm만 공급하므로 XY_MARGIN_NOT_COVERED를 기록한다.
경계 문제 자체가 해결된 것은 아니다. 관측 오차와 후보 δ 설정의 공통 계약을 맞추고,
설정 변경 후 실제 크기/지지/하중을 함께 평가해야 한다. 모델 backend 계약도 변경되므로
기존 모델 메타데이터만 바꾸지 말고 재평가/재학습해야 한다.

## 검증과 실행

기존 연결 검사 19 passed. 진단 추가 후 아래 21개 검사 통과(1.37초).

```bash
export PYTHONPATH="$PAC_TEAM_ROOT/ros2_ws/src/pac_candidates:$PAC_TEAM_ROOT/ros2_ws/src/pac_highlevel"
cd "$PAC_PLANNER_ROOT"
python -m pytest tests/test_team_training.py tests/test_team_bridge.py tests/test_highlevel_handoff.py -q
```

학습 실행 명령은 generator_model_training_20261008.md를 따른다. 새 학습 보고서에는
measurement_geometry_assessment가 추가된다. 이번 점검에서 재학습은 하지 않았다.
이전 전체 64개/팀장 175개 검사를 이번에 다시 실행한 것으로 보고하지 않는다.

## 유지된 계약과 미검증

pac_common/pac_planning 중복, 팔레트 크기/목재 높이/단위/TF, 실제 EMS 공급,
버퍼 capacity와 state_version, PPO 계약, MoveIt 실행 관련 코드는 변경되지 않았다.
따라서 기존 중복 제거/단일 설정/성공 후 commit/실제 MoveIt backend 작업은 그대로 남아 있다.
ROS2 Humble 빌드·service 실행·MoveIt·물리 pick/place는 미검증이다.
원격 쓰기와 main 병합은 시도하지 않았다.

## 출처

- [변경 비교](https://github.com/yang8988/pac-mission1-shared/compare/fd683e56e67af8e2de743b80456f9fd2177a3eef...1782d631c86781d4dc938ec3acfb56a1a504e121)
- [팀 진행 기록](https://github.com/yang8988/pac-mission1-shared/blob/1782d631c86781d4dc938ec3acfb56a1a504e121/docs/taehyeon/PROGRESS.md)
- [80박스 보고서](https://github.com/yang8988/pac-mission1-shared/blob/1782d631c86781d4dc938ec3acfb56a1a504e121/docs/taehyeon/reports/full_pallet_summary.md)
- [팀 검증 기록](https://github.com/yang8988/pac-mission1-shared/blob/1782d631c86781d4dc938ec3acfb56a1a504e121/docs/taehyeon/VALIDATION.md)
