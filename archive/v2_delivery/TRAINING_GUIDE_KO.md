# 생성기 기반 동한 모델 학습 연결 — 2026-10-08

## 연결한 버전과 범위

- 팀장 브랜치: claude/pensive-pasteur-dwbu3g, fd683e56e67af8e2de743b80456f9fd2177a3eef
- 공유 생성기: feature/jaesung-dataset-generator, cc4c075daa3dd4c7f80510042732763fc9502933
- 동한 원격 기준: 146797e337d9b17c521b3ddb1b8560453cddb1bf

앞선 회의용 보완을 포함한 동한 로컬 코드에서 작업했다. 원격 기준 누적 패치와 이번 학습 연결만의 패치를 함께 제공한다. main과 팀원 코드는 수정하지 않았다.

기존 experiment.py의 ReferenceBackend 실험은 유지하고 다음 실제 학습 경로를 추가했다.

1. 공유 생성기를 subprocess로 실행하거나 생성된 출력 폴더를 읽는다.
2. 팀장 world_from_spec의 측정 오차·불확실성·손상 감지·팔레트 순환을 그대로 사용한다.
3. Rule이 행동을 결정하고 실제 CandidateBackend가 생성·검사한 후보를 teacher가 평가한다.
4. 실제 EMS 특징 45개, 안전 우선 순위, 미래 평균/CVaR/막힘/실패를 학습 정답으로 저장한다.
5. 기존 NumPy DualHeadRanker를 새로 학습하고 validation으로 epoch를 선택한다.
6. 별도 test 시나리오에서 휴리스틱·학습 순위·학습+rollout을 비교한다.

teacher 정답은 유한 후보와 유한 미래 시나리오의 가상 평가다. 물리적 최적 정답이나 실제 로봇 성공 라벨이 아니다. 후보를 먼저 잘라 정답을 만들지 않는다. 실제 도착 순서는 가상 세계만 읽고 planner에는 현재 상태와 잔여 SKU 수만 전달한다. 같은 snapshot 옵션을 다시 평가해도 수집 query를 중복 추가하지 않는다.

## 수정 파일

| 파일 | 목적 |
|---|---|
| scripts/donghan/train_team_model.py | 생성·수집·학습·holdout 평가 실행 |
| pac_planning/team_training.py | 구성 단위 분리, 실제 EMS teacher 라벨, 평가 |
| pac_planning/team_bridge.py | 모델 선택 입력과 backend/source/config 계약 검사 |
| pac_planning/planning_service.py | 모델을 한 번 읽어 계획 요청에 전달하는 선택 입력 |
| launch/placement_planner.launch.py | model_path 인자; 빈 값이면 기존 휴리스틱 |
| tests/test_team_training.py | 누출·EMS 라벨·추론·service 입력·계약 거부 검사 |

생성기 split을 우선 유지한다. 같은 SKU/형상 수량 구성에서 순서만 바뀐 시나리오가 서로 다른 split에 있으면 inventory group 전체를 함께 묶어 로컬 학습 split만 다시 나눈다. effective_splits.json에 기록하며 생성기 파일은 수정하지 않는다.

새 모델은 별도 파일로 저장한다. 기존 reference 모델이나 PPO를 덮어쓰지 않는다. 현재는 새 모델 재학습이며 기존 가중치/optimizer checkpoint에서 이어가는 resume 기능은 아니다. PPO의 proxy+DBLF 계약을 임의 수정하거나 PPO를 재학습하지 않았다.

## 실제 실행 결과

Ubuntu24.04, Python3.10.22, NumPy2.2.6에서 실행했다. ROS2/MoveIt 실행은 하지 않았다.

- 패밀리당 2개 × 시나리오당 8박스: 6패밀리/12시나리오/96박스.
- train 8시나리오: 64 query, 후보 1,724개.
- val 2시나리오: 16 query, 후보 331개.
- test 2시나리오: 16 query, 후보 364개.
- 서로 다른 inventory group이며 generator split을 재분할할 필요가 없었다.
- horizon=2, scenario_count=3, Top-K=4, seed=20261008.
- 60 epoch 중 validation 기준 41번째 모델 선택.
- test teacher-best Top-K recall: 모델/휴리스틱 모두 1.0.
- test Top-1 relevance regret: 모델 0.002717, 휴리스틱 0.0. 순위 성능 우위는 확인되지 않았다.
- test 미래 mean MSE: 모델 0.000611, 휴리스틱 zero-future 기준 0.002910.
- 별도 test 두 시나리오에서 세 모드 모두 각각 8/8 가상 배치, NG 0, 기하 safety issues 0.
- 학습 순위 전용은 이번 실행에서 휴리스틱+rollout보다 결정 시간이 짧았다. 반복 측정/통계 검증이나 실제 로봇 시간 측정은 아니다.

작은 시험용 데이터이므로 높은 적재·긴 작업 전체를 대표하지 않는다. 새 모델은 실험용이다. 학습 지평도 운영 기본 3/7보다 짧다. 기본 모델로 자동 채택하지 않았다.

검증: 동한 전체 64 passed. service 모델 입력을 추가한 뒤 관련 17개도 통과. 팀장 최신 175 passed, 2 skipped(gymnasium/torch 없음). 동한 전체 검사에는 기존 Bullet 안정화 검사가 포함되지만 이번 학습/평가 자체는 기하 가상 세계다.

생성기로 학습이 불가능했던 것이 아니라 입력을 teacher 라벨로 바꾸는 경로가 빠져 있었다. 이번에 연결하여 실제 학습과 모델 추론까지 확인했다. 적재 성능 향상은 더 큰 평가가 필요하다.

## 재현 명령

변수는 실제 checkout 위치로 지정한다. PAC_PLANNER_ROOT는 동한 checkout 안에서 ros2_ws/scripts/config가 보이는 폴더, PAC_TEAM_ROOT는 config/taehyeon이 있는 팀장 루트, PAC_GENERATOR_ROOT는 공유 생성기의 tools/ahead_dataset_generator 폴더다.

```bash
export PAC_PLANNER_ROOT=/실제경로/donghan_checkout/pac-mission1-shared
export PAC_TEAM_ROOT=/실제경로/team_checkout
export PAC_GENERATOR_ROOT=/실제경로/generator_checkout/pac-mission1-shared/tools/ahead_dataset_generator
cd "$PAC_PLANNER_ROOT"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]' shapely networkx

python scripts/donghan/train_team_model.py \
  --team-root "$PAC_TEAM_ROOT" --generator-root "$PAC_GENERATOR_ROOT" \
  --team-ref fd683e56e67af8e2de743b80456f9fd2177a3eef \
  --generator-ref cc4c075daa3dd4c7f80510042732763fc9502933 \
  --sample-per-family 2 --boxes-per-scenario 8 \
  --horizon 2 --scenario-count 3 --epochs 60 --seed 20261008 \
  --output runs/team_training_smoke
```

의존성: Python>=3.10, NumPy, PyYAML, Shapely, NetworkX. torch는 필요 없다. ref 인자는 기록용이며 자동 checkout하지 않는다. 의존 checkout을 실제로 해당 ref에 맞춰야 한다.

정상 출력: Generated 12 scenarios / 96 boxes → split별 query → test 결과 → 모델/보고서 경로. output은 새/빈 폴더여야 한다. 기존 데이터는 --generator-root 대신 --dataset /생성된폴더를 지정한다.

출력 파일:
- team_dual_head_ranker.json: 새 실험용 모델
- planner_config.yaml: 모델 사용 시 함께 적용할 설정
- teacher_train/val/test.jsonl.gz: 후보 특징과 teacher 정답
- effective_splits.json: 실제 분리 결과
- training_history.json: validation에 의한 epoch 선택
- training_report.json: 버전·파일 hash·지표·가상 적재 결과
- source_dataset: 자동 생성한 원천 데이터

## 데이터 확대

아래 큰 조건은 아직 실행하지 않았다. 먼저 24박스로 수집 시간과 후보 수를 확인하고 80박스로 늘린다. teacher는 모든 후보를 rollout하므로 느리며 현재 단일 프로세스로 수집한다.

```bash
python scripts/donghan/train_team_model.py \
  --team-root "$PAC_TEAM_ROOT" --generator-root "$PAC_GENERATOR_ROOT" \
  --team-ref fd683e56e67af8e2de743b80456f9fd2177a3eef \
  --generator-ref cc4c075daa3dd4c7f80510042732763fc9502933 \
  --sample-per-family 10 --boxes-per-scenario 80 \
  --horizon 3 --scenario-count 7 --epochs 100 --seed 20261008 \
  --output runs/team_training_extended
```

학습/validation에서만 조정한다. 같은 test 결과를 보고 반복 조정하면 test도 오염된다. 강도·불확실성·버퍼·높은 적재·무효 후보를 포함한 별도 평가도 필요하다. 기존 checkpoint resume는 지원하지 않으므로 데이터 확대 후에도 새 모델 재학습이다.

## 제공 모델 위치

ZIP의 training_result는 실행 당시 생성기 데이터와 teacher 라벨·보고서 전체다. 코드에도 models/team_fd683e56_smoke.json과 config/team_fd683e56_smoke.yaml, reports/team_generator_training_20261008.json을 추가했다. 제공 모델은 실험용이며 기존 기본 모델을 교체하지 않았다. 아래 예제는 직접 학습한 runs/team_training_smoke 결과 경로다. 제공 모델을 쓸 때에는 위 models/config 경로로 바꾼다.

## 계획에 모델 사용

Python에서 명시적으로 선택한다.

```python
from pac_planning.config import load_config
from pac_planning.team_bridge import TeamPlacer

placer = TeamPlacer(
    load_config("runs/team_training_smoke/planner_config.yaml"),
    model_path="runs/team_training_smoke/team_dual_head_ranker.json",
    mode="ahead", use_time_budget=False,
)
# 팀 world_from_spec(..., placer=placer)에 넣어 가상 평가한다.
```

ranking은 학습 순위만, ahead는 학습 Top-K 후 실제 기하 rollout을 사용한다. 둘 다 안전 검사와 안전 우선 순위를 유지한다. backend 소스/설정이 기록과 다르면 MODEL_BACKEND_MISMATCH로 거부한다. 기본 horizon=3/scenario_count=7에 이 모델을 넣지 말고 함께 제공한 2/3 설정을 사용한다. 모델의 존재가 로봇 도달성이나 물리 안정성을 보장하지 않는다.

ROS2 제안 명령(미실행):

```bash
ros2 launch pac_planning placement_planner.launch.py \
  candidate_config:="$PAC_TEAM_ROOT/config/taehyeon/candidates.yaml" \
  planner_config:="$PAC_PLANNER_ROOT/runs/team_training_smoke/planner_config.yaml" \
  model_path:="$PAC_PLANNER_ROOT/runs/team_training_smoke/team_dual_head_ranker.json" \
  use_sim_time:=false
```

Humble에서 build/source 후 실행한다. model_path를 비우면 기존 휴리스틱이다. 메시지 생성·launch·controller·MoveIt·물리 pick/place는 미검증이다. 서비스의 timeout은 soft budget이며 엄격한 실시간 제어기로 사용하지 않는다. PPO에 연결할 때도 기존 DBLF 정책을 새 placer에 자동 호환된다고 판단하면 안 된다.

## 테스트

```bash
export PYTHONPATH="$PAC_TEAM_ROOT/ros2_ws/src/pac_candidates:$PAC_TEAM_ROOT/ros2_ws/src/pac_highlevel"
cd "$PAC_PLANNER_ROOT"
python -m pytest tests/test_team_training.py tests/test_team_bridge.py -q
```

정상 기대값은 17 passed다. 전체/물리 검사 환경은 team_integration_20261008.md를 따른다. 새 모델 채택 전 큰 holdout과 Humble 실행을 각각 확인한다.
