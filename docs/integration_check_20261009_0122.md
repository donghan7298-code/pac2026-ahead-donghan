# 동한 runtime 보고서 연동 점검 — 2026-10-09

## 변경 기준

- 태현: `5a367904a02b7d21b8c1aba3c6dcd49ac4642086` → `83db160e9e6277fec8011c3005b6167b11b92784`.
- 새 파일: `docs/taehyeon/reports/runtime_test_donghan.json`.
- 동한 `7860043`, 생성기 `cc4c075d`, shared 물리 `7f4ae376`, shared main `1eb10252`,
  별도 ahead main `fa3115a9`와 fix `0c8fb80f`는 그대로다.

새 커밋은 test 9개 시나리오에서 Rule high-level, 동한 순위, 해석형 robot check를 연결한
Python runtime 보고서다. ROS2, MoveIt, 실제 controller 실행 보고서는 아니다.

## 게시 결과 비교

동일한 9개 시나리오의 기존 DBLF `full`과 새 동한 보고서를 비교했다.

| 지표 | DBLF | 게시 동한 경로 | 변화 |
|---|---:|---:|---:|
| 적재/episode | 74.11 | 74.11 | 동일 |
| 팔레트/episode | 5.22 | 5.56 | +6.38% (악화) |
| 실제 채움률 | 0.2337 | 0.2203 | -5.76% (악화) |
| 가상 시간/episode | 1164.5 s | 1103.6 s | -5.23% (개선) |
| L2 / L4 | 14 / 0 | 25 / 1 | 안전 편차 증가 |
| robot rejected | 1893 | 1134 | -40.10% |
| collision reject | 1883 | 1099 | -41.64% |
| repack / aborted | 10 / 4 | 14 / 12 | aborted 증가 |

한 번의 seed와 9개 시나리오 결과이므로 통계적 우월성을 뜻하지 않는다. 새 경로는 적재 수는
같고 예상 시간과 robot reject는 줄었지만 공간 효율과 L2/L4가 나빠졌다. 특히 S0018에서
`PROTRUSION` L4가 1건 발생했다.

## EMS provenance 문제

이 보고서가 사용한 원격 `pac_runtime.donghan_ranker`는 `PlacementPlanner`를 직접 만들고
`backend.context`를 넘긴다. 따라서 `context_with_ems()`와 모델/backend fingerprint 검사를
우회하며, planner가 거부한 후보를 DBLF 순서로 다시 뒤에 붙인다.

같은 단일 박스 fixture로 확인한 결과:

- 게시 구버전 경로: `FOOTPRINT_COLUMN_PROXY`
- 수정 `TeamRuntimeRanker`: `EMS_SUPPLIED`, `ems_verified=true`

따라서 게시 수치는 구버전 adapter의 유용한 기준선이지만, 수정된 동한 5-③~⑥ 연동의 최종
성능으로 확정하면 안 된다.

## 최소 보완

- `TeamRuntimeRanker.provenance()`: 호출 수, 후보 평가 수, geometry source, model status,
  robot-validation 요구 수, backend contract SHA-256을 집계한다.
- 팀 runtime 검토 패치의 `run_runtime.py`: 각 episode와 summary에 ranker provenance를 남긴다.
- 기존 stale-result 거부, `TeamRuntimeRanker` 사용, `pac_planning` 의존성 패치는 유지한다.

배치 실행 후 다음 조건을 확인해야 한다.

```bash
python tools/runtime/scripts/run_runtime.py \
  --dataset tools/highlevel/output/dataset80 \
  --split test --variants full --ranker donghan \
  --report docs/taehyeon/reports/runtime_test_donghan_ems.json
```

정상 보고서 조건:

- `ranker == "donghan"`
- `summary.full.ranker_provenance.ems_verified == true`
- `geometry_sources`의 유일한 key가 `EMS_SUPPLIED`
- `backend_contract_sha256`가 하나
- `robot_validation_required_calls == calls`

## 검증과 미검증

- 동한 관련 Python: 22 passed, 0.70 s.
- 태현 runtime/robot + 검토 패치: 25 passed, 7.00 s.
- `run_runtime.py` Python compile: PASS.
- 게시 JSON 수치는 읽고 비교했지만 데이터셋이 원격에 없어 수정 adapter로 9개 시나리오를
  다시 실행하지 못했다.
- ROS2 Humble, colcon, MoveIt, 실제 trajectory, PyBullet 재실행은 하지 않았다.
- PPO의 `slots=4`, `value_provider=proxy`, `placer=dblf` 계약과 Rule 기본값은 변경하지 않았다.

## 출처

- 새 보고서 커밋: https://github.com/yang8988/pac-mission1-shared/commit/83db160e9e6277fec8011c3005b6167b11b92784
- 동한 보고서: https://github.com/yang8988/pac-mission1-shared/blob/83db160e9e6277fec8011c3005b6167b11b92784/docs/taehyeon/reports/runtime_test_donghan.json
- DBLF 비교 보고서: https://github.com/yang8988/pac-mission1-shared/blob/83db160e9e6277fec8011c3005b6167b11b92784/docs/taehyeon/reports/runtime_test.json
