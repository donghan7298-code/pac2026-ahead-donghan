# 근거와 요구사항 반영

우선순위는 **이번에 받은 흐름도·담당 범위와 공통 v0.2 → 10/4 회의 정리 → 미션 1 지침 → 이전 코드·알고리즘 메모**입니다.
공통 문서 원문은 보존하고, 미정 필드를 보충한 제안은 별도 integration 문서에 두었습니다.

## 기존 개발에서 이어받은 점

기존 저장소: [donghan7298-code/pac2026-pattern-evaluation](https://github.com/donghan7298-code/pac2026-pattern-evaluation)

확인한 기준 commit: `dd5c3cafbcb7c19703f67678e98ed5e36ed66d6d`.
`scoring.py`, `rollout.py`, `model.py`, `planner.py`, `contracts.py`, `teacher.py`, `train.py`,
`pacdata/packing.py`와 설계·통합 문서를 읽고 개편했습니다.

| 이전 | 이번 구현 |
|---|---|
| 실제 기존 평가기는 m, raw dict 기반 | m 유지 + 공통 dataclass로 변경 |
| ALGORITHM.md의 일부 오래된 mm·PPO 중심 초안 | 단위/역할은 최신 공통 계약과 흐름도를 우선 |
| 공간 품질, 취약한 안전 여유와 포화 보상 | 유지하고 검사기 evidence로 분리 |
| 115차원 MLP + listwise 학습 | OPAL 기반 45차원 + LambdaRank/회귀 이중 출력으로 재학습 |
| 미래 6유형 | 스트레스 유형 포함 7유형 |
| 후보를 차례로 전부 평가하다 시간 중단 | 시나리오 라운드 단위로 후보 간 동일 표본 수 보장 |
| ceil 개수 하위 꼬리 근사 | 경험분포 하위 alpha 질량을 분수 표본까지 반영 |
| 버퍼·NG·팔레트 마감·로봇 분기 포함 planner | 담당 5-③~⑥에 집중, High-level/로봇/State Manager 경계 명시 |
| 기존 학습 가중치·기존 결과 숫자 | 새 코드로 재생성, 옛 수치를 새 성능으로 사용하지 않음 |

## 회의·미션·흐름도 대응

| 근거 | 적용 | 검증 또는 연결점 |
|---|---|---|
| 회의 2-5 Teacher 전체 후보, Runtime Top-K | `teacher` / `ahead` 모드 | 테스트에서 전체 유효 후보 라벨링 확인 |
| 회의 2-6 Decision Budget | K/표본/H 감소, 공통 완료 라운드 | 타임아웃에도 검사기 호출 유지 |
| 회의 4-1 Safety First → 포화 | evidence 최소 안전 여유 + 포화 | 불가능 후보 점수 미계산 |
| 회의 4-2 공간·미래·하방 위험 분리 | 조밀도/연속공간/단편화/미래 probe, mean−CVaR | score_detail로 항별 기여 표시 |
| 회의 4-3 Sweep·Sensitivity·Pareto·holdout | ±20% 11개 후보 단위 비교 + 별도 시험 그룹 | reports/weight_sensitivity.json |
| 회의 5 정상 Buffer와 Inspection/NG 분리 | BUFFERED 박스의 적합도만 계산 | 이동/회수/NG 분기는 4번·앞단에서 결정 |
| 회의 7 EXPECTED_UNSEEN | 남은 수량 그대로 사용, 임의 Missing 금지 | preview/현재 재고 이중 감소 테스트 |
| 흐름도 5-③ OPAL/의존성/CoG·하중 변화 | 45차원 특징과 출처 표시 | EMS 정보가 없으면 proxy 표시 |
| 흐름도 5-④ LambdaRank + Future Value | 학습·추론 이중 출력 | 모델 스키마/비정상 출력 검사 |
| 흐름도 5-⑤ 공통 난수·7유형 | 동일 시나리오 모든 Top-K 적용 | 시드 재현/부분 라운드 폐기 테스트 |
| 미션 ① 규격·무게·수량·방향 | typed snapshot/catalog, 허용 yaw | NaN/inf/음수/상태 모순 거절 |
| 미션 ② 순차 결정·잔여공간·재계획 | 박스마다 snapshot 재입력 | 크기 수정 + 새 version 재계획 테스트 |
| 미션 ③ 공간·시간·안정성 | 현재 점수 + 미래 성과 + 시간 대용 | 실제 시간과 로봇 추정시간 분리 |
| 미션 ④ 이상 박스 | 앞단 상태 결과와 capacity override 연결 | 무조건 정상 처리하지 않음; 불확실 상태는 참조기 거절 |
| 미션 ⑤ 시각화·비교·로봇 | 현재/계획 2D SVG + 5모드 비교 | 실제 IK·충돌은 6번 담당 연결 필요 |
| 필수: 관통·돌출·무거운 상부 하중 | 외부 hard callback; reference는 보수적 검사 | 경계/지지/하중 회귀 테스트 |
| 필수: 가반하중·접근 자세 | `requires_robot_validation=True` | 현재 팀 선택은 HDR50-22; 실제 EOAT·가반하중·접근 검증은 6단계에서 수행 |

## 첨부 논문 묶음 사용 방법

첨부 ZIP은 **논문 18편**으로 이루어져 있습니다. 코드 저장소는 들어 있지 않았습니다.
전체 파일 목록과 SHA-256은 [paper_inventory.json](paper_inventory.json)에 기록합니다. 논문 PDF 원문은 이 공개 저장소에 재배포하지 않습니다.

| 논문/묶음 | 이번 담당 부분에서의 사용 |
|---|---|
| Operationally Guided Placement-Aware Learning… (OPAL) | Table 5의 15특징과 operational feature 구성을 읽어 SI 단위/추가 특징으로 적용. 논문 PPO/xLSTM 전체 재현은 아님 |
| From RankNet to LambdaRank to LambdaMART | pairwise 순위 gradient에 NDCG swap 가중치를 적용. LightGBM/LambdaMART 라이브러리 사용으로 표현하지 않음 |
| A Reduction of Imitation Learning… / Reinforcement and Imitation Learning via Interactive No-Regret Learning | 학생 방문 상태의 교사 라벨 누적. 정확한 논문 알고리즘 재현/이론적 보장은 아님 |
| Optimization of Conditional Value-at-Risk | 성과 하위 꼬리 CVaR와 평균 성과를 분리한 위험 항 |
| Robot Packing With Known Items and Nondeterministic Arrival Order | 종류·수량은 알고 도착 순서는 모르는 문제 구분. 모든 순서 성공 인증 대신 제한된 표본 탐색 |
| Extreme point-based heuristics / PCT / Transformer 3D BPP | 후보 생성·후보 집합 순위화 배경. 실제 EP/EMS 생성은 5-① 담당 |
| Learning Practically Feasible Policies / Physics-Aware Robotic Palletization / Iterative Action Masking | 학습과 별개의 필수 제약 검사 경계. 로봇·물성 검증을 점수로 대체하지 않음 |
| Online 3D Bin Packing with Fast Stability Validation and Stable Rearrangement Planning | LBCP 결과를 evidence로 받을 연결점. LBCP·다중 지지·재배치 원 논문 구현은 5-②/High-level 연결 과제 |
| Buffer 및 High-level PPO 관련 4편 | 4번 행동 선택·버퍼 운용 담당의 확장 근거. 이 저수준 모듈에서 PPO 구현을 주장하지 않음 |
| Three-Dimensional Bin Packing and Mixed-Case Palletization | 공간/안정성/현장 제약을 별도 평가하는 전체 설계 배경 |

## 아직 팀 통합이 필요한 사항

2026-10-08 업데이트: 실제 EMS와 LBCP hard mask는 team_bridge에서 연결해 Python으로 검증했다.
HighLevelDecider의 현재/버퍼 박스 인계도 추가 패치에서 검증했다. ROS 계획 service 코드는
원격 146797e에 올라갔지만 Humble에서 build/호출은 하지 않았다.
카메라 상태 갱신, MoveIt2 IK/접근·하강·후퇴 및 충돌/EOAT/Load CoM 검증,
실제 버퍼 이동·팔레트 교체·부분 재배치 실행과 배치 후 heightmap 보정은 후속 통합이다.
이 작업으로 전체 로봇 시스템이 완성됐다고 표시하지 않습니다.
