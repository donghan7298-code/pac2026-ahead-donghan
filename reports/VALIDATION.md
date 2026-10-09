# 검증 결과

기준일: 2026-10-07 (한국시간). 제공 코드와 새로 학습한 모델로 실행했습니다.

## 자동 검사

- pytest **36개 통과**. 공통 모델/입출력, JSON·ROS adapter 경계, 상태 불변성, stale 후보,
  지지·하중·경계, 불확실 박스 거절, 크기 보정, tracking 실패, 로봇 후보 failover,
  공통 시나리오, 부분 라운드 폐기, 두 학습 출력, 데이터 누출, 모델 조건 불일치를 검사했습니다.
- 실제 실행 환경은 Python 3.12.14, NumPy 2.3.5, pytest 8.4.2, PyYAML 6.0.3입니다.
- Python 3.10 문법 검사를 통과했습니다. Python 3.10/3.12 CI workflow를 포함합니다.
- ROS 2 Humble / colcon / MoveIt2 / 실제 로봇은 이 실행 환경에서 검증하지 않았습니다.
- JSON fixture → 공통 dataclass → planner → 공통 후보 → 모의 로봇 검증의 계약 테스트입니다.
  ROS adapter는 주입한 메시지 객체로 확인했으며 실제 ROS 통신 테스트가 아닙니다.

## 학습

- Seed: 20261007. train 12개 박스 구성 / validation 4개 / holdout 4개 그룹.
- 초기 teacher 라벨 + 학생 방문 상태 teacher 재라벨링 1회.
- 학습 95개 결정, 1073개 후보.
- holdout 16개 결정, 180개 후보에서 teacher 최상 후보의 Top-4 포함률 **87.5%**.
- 모델 선택에는 validation만 사용했습니다. holdout으로 epoch나 가중치를 고르지 않았습니다.
- 조건: horizon 3, 7개 시나리오, CVaR alpha 0.2, 완전 지지/단일 하부 박스 참조 검사기.
- 순위값과 미래 값은 다른 출력입니다. 회귀값은 확정 실측이 아니라 학습 예측입니다.

## 같은 입력의 전체 에피소드 비교

각 방식은 동일한 팔레트/박스 구성/투입순서 4개로 평가했습니다.
한 번 막히면 해당 팔레트 에피소드를 종료하고, 버퍼나 재적재를 몰래 사용하지 않았습니다.
비교는 고정 작업량 기준이며 1초 예산에서 자동 축소한 결과가 아닙니다.

| 방식 | 평균 적재부피율 | 평균 적재 개수 | 막힌 에피소드 | 평균 판단시간 | P95 판단시간 |
|---|---:|---:|---:|---:|---:|
| Greedy | 31.18% | 6.75 | 75% | 22.2 ms | 58.9 ms |
| Current-only (다단계 미래 없음) | 35.29% | 7.25 | 75% | 19.8 ms | 50.5 ms |
| Full Rollout Teacher | 44.41% | 8.25 | 50% | 517.2 ms | 1526.8 ms |
| AI Ranking 단독 | 44.41% | 8.25 | 50% | 21.5 ms | 57.0 ms |
| AI + Rollout AHEAD | 44.41% | 8.25 | 50% | 175.2 ms | 319.8 ms |

이 작은 표본에서는 AHEAD가 Full Teacher와 같은 평균 적재 결과를 더 적은 계산시간으로 얻었습니다.
**에피소드 4개, 단순 참조 기하, 합성 데이터 결과**이므로 실제 물류 성능·통계적 우월성·현장 안전을 입증하지 않습니다.
Current-only도 현재 상태에서의 잔여 SKU 배치 가능 특징은 사용합니다. 여기서 제외한 것은 다단계 롤아웃입니다.
이 표본에서는 AI Ranking 단독도 같은 결과였습니다. 따라서 이 4개 에피소드만으로
AI 단독 대비 rollout의 추가 이득을 입증했다고 해석하지 않습니다.

## 가중치와 시각화

- validation teacher 후보에서 초기값 및 항별 ±20% 11가지 민감도/Pareto 비교를 수행했습니다.
- 후보 단위 분석이며 전체 에피소드 최적 가중치를 확정한 실험은 아닙니다. 기본 YAML은 초기값을 유지합니다.
- `demo_preview.svg`는 ACTUAL snapshot과 선택된 PLANNED 후보의 2D top view입니다.
- `demo_result.json`에는 후보별 점수 기여도, 미래 결과 출처, 탈락 사유, soft budget 등이 있습니다.

## 재현 및 원시 결과

README의 `pac_planning.experiment` 명령으로 데이터·모델·결과를 다시 만들 수 있습니다.
`training_report.json`, `benchmark.json`, `weight_sensitivity.json`에 원시 수치·시드·설정을 보관했습니다.
하드웨어 부하에 따라 시간 값은 달라집니다. 고정 작업량에서 선택과 점수는 재현할 수 있습니다.
코드·모델·설정의 SHA-256은 `source_manifest.json`에 기록했습니다.

## 아직 확인하지 않은 것

실제 EMS/Extreme Point 구현의 탐색 범위, LBCP 다중 지지 및 강건 불확실성 처리,
ROS node/action/message 전체 경로, 카메라 실측, IK·충돌·가반하중/EOAT/Load CoM,
그리퍼 접근/하강/후퇴, 물리 엔진 전도 시험과 실제 실행·사후 보정은 후속 통합입니다.
시간 예산은 협력형 soft limit입니다. 모든 필수 검사를 1초 내 보장하는 hard deadline이 아닙니다.
