## 변경 이유

팀의 흐름도와 공통 개발 기준 v0.2에 맞춰 동한 담당인 Low-level Placement Planner 5-③~⑥을 독립 모듈로 통합합니다.
기존 dict 기반 평가기를 공통 dataclass로 옮겨 상태 소유권과 모듈 입출력을 통일했습니다.

## 변경 내용

- 45차원 후보 특징, LambdaRank 순위 + 미래 성과 회귀 이중 출력 모델
- Teacher 전체 유효 후보 평가, 학생 방문 상태의 교사 라벨 누적 학습
- 7종 재고 기반 미래 시나리오, 공통 난수, Greedy rollout, mean/worst/lower-tail CVaR/막힘
- 안전 점수 포화와 공간/미래/위험/시간 점수, 설명 가능한 후보 순위
- 시간 부족 시 계산량 축소, 공통 완료 라운드만 비교, 모델/현재 점수 폴백
- ROS와 독립된 계산 코어, 공통 JSON/ROS adapter, 모델·보고서·2D 시각화
- 팀 협업 문서, Python 3.10/3.12 CI workflow

## 리뷰할 공통 계약

기존 v0.2 원문은 보존했습니다. 추가 제안은 `docs/integration.md`에 있습니다.

1. target_pose를 회전 후 AABB의 최소 모서리로 정의하고 robot center 변환을 명시
2. 기존 공통 필드를 바꾸지 않고 PlanningContext로 SKU/허용하중/EMS/버퍼 전달
3. Hard Validator 성공 결과의 details에 typed ConstraintEvidence 제공

## 검증

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m pac_planning.demo --model models/dual_head_ranker.json --fixed-work
```

- 로컬 Python 3.12 / pytest 8.4.2: 36개 통과
- Python 3.10 문법 검사 통과; 실제 3.10 실행은 CI 결과 확인 필요
- 별도 박스 구성 holdout 4개, 16개 결정/180개 후보: Teacher best Top-4 recall 87.5%
- 4개 전체 에피소드 비교 원시 수치는 reports/benchmark.json, 해석은 reports/VALIDATION.md

## 연결점과 제한

reference_backend는 단독 실행용 완전 지지/단일 하부 박스 기준선입니다.
팀원의 EMS/EP 및 LBCP/불확실성 hard mask로 교체한 뒤 다시 학습·평가해야 합니다.
로봇 IK·충돌·가반하중·실제 ROS/실행/State Manager는 담당 모듈과 후속 통합합니다.
planner는 실제 상태를 바꾸지 않으며 모든 반환 후보에 로봇 검증이 필요합니다.
전체 공정 완성이나 소규모 합성 실험의 현장 성능 보장을 주장하지 않습니다.
