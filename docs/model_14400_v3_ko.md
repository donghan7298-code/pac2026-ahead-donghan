# 원본 14,400 박스 확장 학습 v3
확인일 2026-10-09. 이전 v2 학습 모델을 기본 모델로 승격하지 않은 결정을 유지한다.

## 이번에 달라진 부분

6개 generator family × family당 30개 scenario × 80개 원본 박스,
총 180개 독립 scenario / 14,400개 source box를 실제로 생성했다.
source_dataset의 manifest, ground_truth, catalog, split, SHA256을 제공한다.
기존 864개 개발 실험과 새 14,400개 실험은 구분한다.

| 항목 | v3 first-pass 설정 |
|---|---|
| 실제 teacher | 최신 팀 RuntimeCore + EMS/LBCP backend + 동한 h3/s7 |
| inventory split | train 126 / validation 27 / test 27 |
| collection seed | 7, 한 scenario에 한 번 |
| corruption | mismatch 2%, missing 2%; 실제 teacher runtime에서 적용 |
| worker | 6, NumPy/OpenBLAS thread는 1 |
| 모델 | 3개 초기화 seed 7/17/29, hidden 128 |
| 학습 상한 | seed당 200 epoch, patience 30, query minibatch 16 |
| 선택 | validation only |
| 비교 | DBLF / heuristic rollout / learned ranking / learned rollout |
| held-out 조건 | test 27 × 4방법 × clean/nominal/noisy3mm, seed 101 |
| PPO | 학습하지 않음; 기존 proxy + DBLF observation, buffer 4 유지 |

현재 완료 수와 학습 시작 여부는 reports/integration_v3_20261009.json 또는
ZIP의 experiments/training_checkpoint/progress_snapshot.json을 확인한다.
수집된 candidate label 개수는 로봇 테스트 횟수가 아니며,
source box 수를 모델 학습 완료 수로 표기하지 않는다.

## 최적화와 실제 확인

ros2_ws/src/pac_planning/pac_planning/rollout.py의 greedy future search는
정렬된 후보에서 첫 hard-valid 후보를 찾으면 멈춘다. 모든 후보의 identity/version
검사는 유지한다. 팀 backend에는 pose-only future generation 경로를 추가하고
현재 박스의 EMS evidence 생성은 그대로 유지했다.

S0001의 처음 8개 / 164개 후보 라벨 pilot에서 이전 계산과 query/label parity를
비교했다. 합산 teacher 시간은 약 4.45초, 이전 계산 약 7.16초였다.
전체 pipeline wall time이나 모든 14,400 박스에서의 1.61배 속도를 주장하지 않는다.
7번째 박스 하나에서는 기존 경로가 더 빨랐다.
결과를 바꾼 기능이 아니라 중복 계산을 줄인 경로다.

## 설치와 새 실행

WSL Ubuntu 22.04에서 DONGHAN_ROOT는 내 checkout 내부 pac-mission1-shared 폴더,
TEAM_ROOT는 팀 checkout root다. ZIP의 팀 runtime/candidate 9개 파일 패치가 필요하다.

```bash
cd ~/AHEAD/pac-donghan/pac-mission1-shared
sudo apt update
sudo apt install -y python3-pip python3-venv
python3 -m venv .venv-model
source .venv-model/bin/activate
python3 -m pip install numpy pyyaml pytest pybullet networkx pillow

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python3 scripts/donghan/model_pipeline.py all \
  --team-root ~/AHEAD/pac-team \
  --team-ref 81d0333ba6550d9ee6f02661d8b8d9e79beaca6c \
  --generator-ref cc4c075daa3dd4c7f80510042732763fc9502933 \
  --dataset ~/Downloads/PAC2026_Donghan_v3_20261009/experiments/source_dataset \
  --config config/model_14400_firstpass.yaml \
  --output experiments/original14400_v3 --resume
```

새 output에서 먼저 collected 1/180 ... 로그가 보인다.
수집이 모두 끝나면 packed train/val/test array가 만들어지고 epoch 로그가 시작된다.
그 뒤 selected_model.json과 runtime_test/report.json이 생성된다.
이번 제공 시점에 어느 단계까지 실행했는지는 progress_snapshot에 기록한다.

## 중간 체크포인트 이어서 실행

아래는 ZIP을 내려받아 완성된 shard를 새 경로에서 이어 사용하는 절차다.
같은 output을 사용 중인 다른 worker가 없을 때만 실행한다.
원본 shard bytes/모델/array fingerprint를 바꾸지 않고 status의 저장 경로만 조정한다.

```bash
cd ~/AHEAD/pac-donghan/pac-mission1-shared
AHEAD_V3_BUNDLE=~/Downloads/PAC2026_Donghan_v3_20261009
AHEAD_RUN="$AHEAD_V3_BUNDLE/experiments/training_checkpoint"

python3 scripts/donghan/relocate_training_run.py --run-dir "$AHEAD_RUN"
python3 scripts/donghan/relocate_training_run.py --run-dir "$AHEAD_RUN" --apply

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python3 scripts/donghan/model_pipeline.py all \
  --team-root ~/AHEAD/pac-team \
  --team-ref 81d0333ba6550d9ee6f02661d8b8d9e79beaca6c \
  --generator-ref cc4c075daa3dd4c7f80510042732763fc9502933 \
  --dataset "$AHEAD_V3_BUNDLE/experiments/source_dataset" \
  --config config/model_14400_firstpass.yaml \
  --output "$AHEAD_RUN" --resume
```

같은 run을 재개하려면 collection.json에 기록된 planner/runtime/backend와 설정이 같아야 한다.
source나 config가 달라졌다는 오류를 지우기 위해 manifest/hash를 덮어쓰지 않는다.
부모·현재 train inventory가 평가 inventory에 들어가면 validation/test leakage로 거절한다.

더 많은 행동 다양성을 추가할 때는 model_benchmark.yaml을 새 output에 실행한다.
3개 collection seed + 일부 DBLF behavior의 큰 설정이다. 현재 first-pass와 섞지 않는다.

## 시연용 모델은 별도 계약으로 학습

model_pipeline.py에 --candidate-config / --robot-config를 추가했다.
시연의 3 mm candidate reserve / 0.1525 m TCP offset을 명시하여 새 run을 만들 수 있다.
기존 2 mm / 0.22 m first-pass output으로 재개하려고 하면 계약 변경으로 거절된다.
해당 시연 계약의 추가 학습은 아직 실행하지 않았다.

실제 runtime held-out 결과가 좋아야 learned model을 시연에 사용한다.
candidate geometry validity는 학습 모델과 분리되어 있고, model score가 이를 덮어쓰지 못한다.
DBLF보다 팔레트 수·하중 안전·실행 가능성이 나쁘면 더 큰 모델이어도 기본 모델을 유지한다.

ROS build와 실제 pick/place는 docs/original_boxes_demo_v3_ko.md의 순서로 검증한다.
