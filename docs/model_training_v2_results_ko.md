# 동한 5-③~⑥ 확장 학습·검증 결과 — 2026-10-09

학습·재개·새 데이터 검증을 실제로 실행할 수 있는 v2를 개발했고 360개
Python runtime episode 비교를 완료했다. **이번 후보 모델은 DBLF를 이기지
못했다.** 기존 기본값을 유지하며 `candidate_runtime_v2.json`은 실험 후보로
제공한다. 코드 개발 완료와 모델 성능 목표 달성은 다른 상태다.

## 실제 확인한 범위

| 구분 | 결과 |
|---|---|
| 동한 원격 기준 | `7860043228c81aec517c5a1966d475d36e12f68a` |
| 팀장 runtime 기준 | `81d0333ba6550d9ee6f02661d8b8d9e79beaca6c` |
| 생성기 기준 | `cc4c075daa3dd4c7f80510042732763fc9502933` |
| 원본 생성·수집 | 36 시나리오 / 864 박스 / 36 episode shard |
| 학습 | train 883 query / 12,011 후보 라벨 |
| 모델 선택 | val 195 query / 3,008 후보 라벨만 사용 |
| 미사용 test | 222 query / 3,328 후보 라벨 |
| 추가 독립 데이터 | 새 seed로 12 inventory / 288 원본 박스 |
| runtime 비교 | 기존 test 216 + 새 데이터 144 = 360 episode |
| 코드 검사 | 동한 74 passed / 1 skipped, 팀 연동 202 passed / 4 skipped |
| ROS2 Humble / MoveIt / Gazebo 로봇 | 실행하지 않음 |

모든 라벨 18,347개를 학습에 사용한 것이 아니다. train/val/test는
inventory 단위로 25/5/6개로 나뉘며, 새 12개도 기존 train/val/test와
inventory가 겹치지 않는다. 각 방식이 같은 stream을 받는 90개 조건을
4개 방식으로 반복했다. 독립 inventory는 18개이며 seed 반복을 별도의
독립 실험군으로 부풀리지 않았다.

학습 모델 초기화 seed 7/17을 비교했다. seed 7은 33 epoch 실행 후 best 13,
seed 17은 30 epoch 실행 후 best 10이 선택됐다. validation objective로
seed 17의 10 epoch를 고정한 다음 test/fresh를 평가했다.
hidden 64, 기존 45 feature, rank + 4 future-output head를 유지했다.
모델 SHA-256은 `54b446300f28184b634de12859ac66ad9dd27f5a2513e1810febf0349608d48f`다.

## 검증 1 — 최신 팀 runtime의 동일 입력 비교

기존 held-out inventory 6개 × seed 101/102/103 × clean/nominal/noisy3mm ×
4개 방식이다. 팀장의 최신 로봇 받침대와 일치하는
`robot_check_gazebo.yaml`을 사용했다. low-level 비교를 분리하기 위해
상위 policy는 rule, stage-4 value-provider는 proxy+DBLF, buffer는 4칸으로
유지했다. PPO를 재학습하거나 그 계약을 바꾸지 않았다.

### Nominal

| 방식 | episode | 평균 적재 박스 | 평균 팔레트 | 평균 충전율 | L4 합계 | 사후 이상 합계 |
|---|---:|---:|---:|---:|---:|---:|
| DBLF 기준 | 18 | 22.389 | 2.389 | 16.64% | 0 | 0 |
| 휴리스틱 + rollout | 18 | 22.389 | 2.500 | 15.69% | 0 | 0 |
| 학습 순위/미래 head | 18 | 22.389 | 2.611 | 14.87% | 0 | 0 |
| 학습 shortlist + rollout | 18 | 22.389 | 2.611 | 15.04% | 0 | 1 |

### Clean — 실행오차를 제거한 비교

| 방식 | episode | 평균 적재 박스 | 평균 팔레트 | 평균 충전율 | L4 합계 | 사후 이상 합계 |
|---|---:|---:|---:|---:|---:|---:|
| DBLF 기준 | 18 | 22.333 | 2.500 | 15.80% | 0 | 0 |
| 휴리스틱 + rollout | 18 | 22.333 | 2.333 | 16.61% | 0 | 0 |
| 학습 순위/미래 head | 18 | 22.333 | 2.500 | 15.41% | 0 | 0 |
| 학습 shortlist + rollout | 18 | 22.333 | 2.556 | 15.11% | 0 | 0 |

### Noisy3mm — 실행 위치 오차 진단

| 방식 | episode | 평균 적재 박스 | 평균 팔레트 | 평균 충전율 | L4 합계 | 사후 이상 합계 |
|---|---:|---:|---:|---:|---:|---:|
| DBLF 기준 | 18 | 22.389 | 2.444 | 16.23% | 15 | 0 |
| 휴리스틱 + rollout | 18 | 22.389 | 2.611 | 14.84% | 25 | 1 |
| 학습 순위/미래 head | 18 | 22.389 | 2.556 | 15.31% | 29 | 0 |
| 학습 shortlist + rollout | 18 | 22.389 | 2.556 | 15.43% | 24 | 0 |

nominal에서 학습 방식은 DBLF보다 평균 약 0.222개 더 많은 팔레트를 썼다.
clean에서 휴리스틱 rollout의 팔레트 수가 줄었지만 6개 inventory이고,
cluster CI가 0을 포함하여 우위 확정 근거로 사용하지 않았다.
noisy3mm에서는 DBLF도 L4가 발생하고, 세 ranker 방식의 L4가 더 많았다.
팔레트 수 외에 배치오차/하중/robot 접근 실패를 함께 개선해야 한다.

## 검증 2 — 학습에 쓰지 않은 새 생성 데이터

새 generator seed 20261109, 12개 inventory × seed 201/202/203 × nominal ×
4개 방식이다. 모델을 고른 후 생성했고 train/val 입력이나 모델 선택에
사용하지 않았다.

| 방식 | episode | 평균 적재 박스 | 평균 팔레트 | 평균 충전율 | L4 합계 | 사후 이상 합계 |
|---|---:|---:|---:|---:|---:|---:|
| DBLF 기준 | 36 | 22.889 | 2.194 | 18.61% | 0 | 4 |
| 휴리스틱 + rollout | 36 | 22.889 | 2.417 | 17.20% | 0 | 5 |
| 학습 순위/미래 head | 36 | 22.889 | 2.583 | 16.11% | 0 | 4 |
| 학습 shortlist + rollout | 36 | 22.889 | 2.583 | 16.01% | 0 | 5 |

| 새 데이터의 DBLF 대비 차이 | 평균 추가 팔레트 | inventory cluster 95% CI | 개선/동일/악화 inventory |
|---|---:|---|---|
| 휴리스틱 + rollout | +0.222 | [+0.028, +0.444] | 1/6/5 |
| 학습 순위/미래 head | +0.389 | [+0.194, +0.583] | 0/5/7 |
| 학습 shortlist + rollout | +0.389 | [+0.139, +0.639] | 1/4/7 |

차이는 새 방식 − DBLF다. 같은 처리 부피에서 양수는 팔레트를 더 사용했다는
뜻이다. 비교 지표 `pallet_equivalent = 팔레트 수 − 적재 부피 / 팔레트 용량`을
사용했으며, 이번에는 방식별 처리 박스와 부피가 같아 실제 팔레트 수 차이와
같다. 팔레트 수를 줄이기 위해 박스를 누락시키는 결과를 통과시키지 않는다.

네 방식의 L4는 모두 0이지만, true layout의 별도 해석적 검사에서
`OVERLOADED`가 DBLF 4건/휴리스틱 5건/학습 순위 4건/학습 rollout 5건
발견됐다. L4 0을 안전성 인증으로 해석할 수 없다. 이 사후 검사는 실제 물리
시뮬레이션이 아니다. 최종 gate는 모든 variant/방식에서 `KEEP_BASELINE`이다.
이는 DBLF가 검증된 안전 모델이라는 뜻도 아니다.

## 모델 진단 — 높은 top-k 수치의 한계

| test teacher-query 지표 | 정적 휴리스틱 | 학습 모델 |
|---|---:|---:|
| teacher 최선 후보가 top-4에 포함 | 98.65% | 99.10% |
| top-1 relevance regret, 낮을수록 좋음 | 0.01372 | 0.02205 |
| future mean MSE | 0.002389 | 0.001061 |
| future CVaR MSE | 0.001680 | 0.001052 |
| future blocking MSE | 0.290799 | 0.031727 |
| future failure MSE | 0.151000 | 0.013258 |

정적 휴리스틱의 future MSE는 future 출력이 없는 zero-head 기준이다.
실제 휴리스틱 rollout과 이 표를 같은 실험으로 보지 않는다.
후보가 4개 이하인 query 37개를 제외해도 학습 top-4 recall은 185개 query에서
98.92%였지만 top-1 regret는 0.02106으로 휴리스틱 0.01106보다 나빴다.
teacher 재현율이 높다는 이유만으로 실제 팔레타이징 성능을 주장하지 않는다.
최종 검증에서 robot checker 통과율도 낮아져, teacher의 목표와 로봇 실행성/
최종 packing 효율 사이를 더 잘 맞춰야 한다는 **추론**을 얻었다.

## 속도와 남은 우선순위

새 데이터 S0002/seed 201의 학습 순위 episode를 재실행해 stream과
최종 배치/결정 hash가 같음을 확인했다. profile에서 모델 predict는
58회 합계 0.0268초, `compute_features`는 1,046회 누적 66.14초였다.
잔여 SKU/버퍼의 적재 가능성을 묻는 `has_placement`가 8,076회 후보를
다시 만들었다. model 추론보다 feature용 geometry 탐색 반복이 큰 병목이다.
다른 worker가 함께 실행되는 동안 측정했으며 단독 지연 benchmark는 아니다.

runtime report의 `time_s`는 가정한 동작 비용 누적이고 실제 로봇 실행시간이
아니다. `planner_p95_sec`는 CPU 실행시간으로 별도 기록한다. DBLF의 0은
ranker callback 측정 대상이 없다는 뜻이며 DBLF 처리가 즉시 끝난다는 뜻이
아니다. 깊은 rollout에 time budget을 주지 않은 비교이므로 실제 ROS 실행에서
요구하는 planning deadline을 아직 충족한다고 판단할 수 없다.

1. 먼저 3mm 오차의 L4와 hidden capacity 과부하를 재현한다. uncertain 박스의
   선언된 필요 XY 여유 6.025mm에 비해 현 mask는 4mm다. 팀 후보 생성/측정
   담당자와 수정 범위를 합의하고 학습모델 대신 geometry 조건부터 검증한다.
2. 같은 state/candidate/remaining inventory의 feature probe를 정확히 캐시하거나
   동일 hard mask를 쓰는 backend existence query를 연결한다. cache를 썼다는
   이유로 하중/지지 검사를 생략하지 않는다. 현재 제공본에는 이 최적화를
   구현했다고 주장하지 않는다.
3. 더 많은 train inventory/후반 적층/DBLF와 teacher 행동 상태를 수집한다.
   `model_benchmark.yaml`은 180 시나리오/14,400 원본 박스/3 pass 설정이다.
   43,200은 반복 노출 수이며 고유 박스 수가 아니다. 큰 preset은 아직 미실행이다.
4. train/validation에서 teacher 목표·top-1 ranking·robot 실행성·packing을
   비교해 모델을 고른다. 이번 test/fresh를 보고 가중치를 조정한 뒤 같은
   test 수치를 새로운 일반화 증거처럼 쓰지 않는다. 재선택 후 새 holdout으로
   다시 검증한다.
5. ROS2에서 topic/state_version/planning config/TF를 확인하고 MoveIt의 실제
   planning/execution 결과로 상태를 갱신한다. 현재 시간 기반 성공 처리·place
   위치 spawn을 실제 흡착 pick/place로 바꾸는 작업은 별도 로봇 담당 범위다.

## 변경 파일과 적용

코드 역할·명령·정상 출력은 [실행 가이드](model_training_v2_ko.md)를 따른다.
주요 변경은 `training_data.py`, `training.py`, `model_pipeline.py`,
`team_training.py`, `evaluation.py`와 CLI/config다. mmap 기반 라벨 저장,
query minibatch Adam, 체크포인트/RNG 재개, validation-only 모델 선택,
source/dataset/EMS 계약 검사, 완전한 후보 순서와 robot checker 연결,
paired cluster 비교 및 배포 gate를 추가했다.

팀 runtime 패치는 8개 파일의 `ranker_config`, 후보 연결, stale result,
새 observation 이후 계획과 provenance 기록에 한정했다. remote에 push하거나
main에 merge하지 않았다. ROS launch에는 `ranker_model_path`와 같은 실험의
`planner_config.yaml`을 함께 넘겨야 한다. 이번 모델은 h2/s3인데 기본 h3/s7을
쓰면 계약 불일치로 model fallback이 난다.

이번 실행 후 packing의 중복 판정을 snapshot ID에서 query 전체 내용으로
바꿨다. 서로 다른 Monte Carlo 정답 표본을 남기기 위한 수정이다. 최종 코드로
원본 shard를 다시 pack해 train/val/test array와 metadata의 모든 byte가
같음을 확인했다. teacher replay도 같은 query/라벨 hash였다. 원래 수집/평가
manifest의 source hash는 보존했고, 실행 당시와 최종 코드의 차이는 report에
명시했다. 최종 코드로 새 실험을 수집할 때는 새 output을 사용한다.

Python 3.12.14 / NumPy 2.3.5 / PyYAML 6.0.3 / Linux에서
코드·학습·runtime을 실행했다. 목표 Ubuntu 22.04/Python 3.10/ROS2 Humble에서
아직 실행하지 않았다. 동한 skip 1개는 pac_simulation 부재이고 팀 skip 4개는
gymnasium/torch 부재다. 단위 테스트 수, runtime episode 수, 물리/로봇 검증
결과를 서로 합산해 하나의 통과 수치로 표현하지 않는다.

원본 모델/checkpoint/array/episode/layout/로그는 ZIP의 `experiments/`와
`logs/`에 있다. JSON 결과는 `reports/model_training_v2_20261009.json`이다.
