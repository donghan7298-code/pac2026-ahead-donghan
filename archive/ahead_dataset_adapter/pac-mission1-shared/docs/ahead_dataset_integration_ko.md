# AHEAD Dataset Generator 연결 및 기존 박스 생성기 교체

## 적용 범위

동한 담당 5-③~⑥의 학습·평가 입력을 팀원의 AHEAD 생성기로 교체한다.
`python -m pac_planning.experiment --ahead-dataset ...`를 사용하면 기존
`make_episode()`와 SKU 고정 무게 기반 `observe_next()`를 호출하지 않는다.
외부 생성기는 별도 프로세스로 실행하고 JSON/JSONL 경계에서 연결한다.
같은 이름의 `pac_common` 두 개를 Python 경로에 함께 넣지 않는다.

현재 연결은 **오프라인 시뮬레이션 전용**이다. 실제 State Manager와 센서의
측정 완료 판정은 해당 파트가 담당한다. 새 생성기는 학습된 순위/미래가치 모델이
아니므로, 데이터 생성 이후 teacher 평가와 모델 학습이 여전히 필요하다.

## 확인한 원본

- 생성기: https://github.com/dlwotjd1289-cloud/ahead-dataset-generator
- 검증 커밋: `766d1ee179e7efe3b8b75b0813c1918dca26bc90`, generator 1.2.0.
- 플래너: https://github.com/yang8988/pac-mission1-shared/tree/feature/donghan-placement-planner
- 비교 기준 커밋: `1cd65ba67dbd7e1d1b6365d2c89376448e535980`.
- 이 커밋에서는 프로젝트가 저장소 안의 `pac-mission1-shared/` 폴더에 있다.
  아래 명령은 **pyproject.toml이 있는 폴더**에서 실행한다.

## 호환성 차이와 처리

| 항목 | 생성기 | 연결본 처리 |
|---|---|---|
| 기본 공통 dataclass | BoxState, SystemState 등 v0.2 필드 | 플래너의 검증·불변성 있는 pac_common으로 복원 |
| JSON 구조 | schema_version=1, initial_system_state | 별도 AHEAD 로더 사용. 기존 demo의 scene_from_file에 그대로 넣지 않음 |
| 초기 박스 | tracked_boxes가 비어 있음 | simulation_observations에서 현재 박스 1개씩 반영 |
| 박스 상태 | DETECTED | identity·confidence=1·규격·단위 검사 후 시뮬레이션 안에서만 READY_FOR_PICK |
| 팔레트 size.z | 실제 받침대 두께 0.15m | max_height_m에서 플래너용 적재 공간 높이로 변환한 복사본 사용 |
| SKU 무게 | 같은 SKU도 개별 박스마다 다름 | 관측 박스의 실제 무게 그대로 보존 |
| 미관측 박스 무게 | 알려지지 않음 | 공개 설정의 최대 무게를 임시 상한으로 사용 |
| PlanningContext | 생성기에 없음 | 공개 SKU 카탈로그·별도 하중 정책으로 구성 |
| 평가용 정답 | ground_truth, analysis | 플래너 입력 경로에서는 읽지 않음 |
| 학습/평가 분할 | splits.json | train/val/test 유지, 중복 scenario_id 거부 |

`config/ahead_adapter.simulation.yaml`은 실행 가능한 **시뮬레이션 가정**이다.
150kg 팔레트 허용하중은 생성기가 제공한 값이나 실제 장비 사양이 아니다.
생성기의 `max_load_kg`가 null일 때만 이 명시적 설정을 사용하며, 둘 다 없으면 중단한다.
박스 상부 허용하중은 정보가 없으므로 0N으로 두어 상부 적재를 허용하지 않는다.
적재 성능을 제대로 비교하려면 팀에서 정한 박스별/SKU별 강도로 채워야 한다.

높이 해석은 `above_pallet`이면 적재 면 위 1.5m, `floor`이면 팔레트 두께를
제외한 1.35m이다. 현재 플래너 문서의 팔레트 상면 z=0 정의에 맞춰
`above_pallet`을 선택했다. 생성기 담당자와 이 기준을 확인한 후 확정한다.
원본 파일과 팀 공통 dataclass의 필드 자체는 수정하지 않는다.

생성기의 stress 모드는 SKU별 통상 무게 범위를 넘어 30kg까지 만들 수 있다.
따라서 현재 미관측 미래 박스는 전체 stress 범위를 포함한 보수적 상한을 쓴다.
기본 설정에서는 모든 SKU의 미래 가상 무게가 30kg이다. 이는 확률 분포를
그대로 재현한 예측이 아니다. 관측된 현재 박스의 무게는 실제 생성값을 사용한다.
미래 무게 분포를 정교하게 표본추출하는 확장은 별도 알고리즘 개선 항목이다.

## 빠른 실행: 새 데이터 생성 후 플래너 확인

Ubuntu 22.04 / Python 3.10 대상 명령이다. 이미 생성기 저장소가 있으면 clone은 생략한다.

```bash
# pyproject.toml이 있는 플래너 폴더에서 실행
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
git clone https://github.com/dlwotjd1289-cloud/ahead-dataset-generator ../ahead-dataset-generator
git -C ../ahead-dataset-generator checkout 766d1ee179e7efe3b8b75b0813c1918dca26bc90
python -m pip install -r ../ahead-dataset-generator/requirements.txt

# 별도 프로세스가 생성기의 pac_common을 사용한다.
python ../ahead-dataset-generator/scripts/generate_dataset.py \
  --config ../ahead-dataset-generator/config/default.yaml \
  --output runs/ahead_dataset --mode sample --sample-per-family 2
cp ../ahead-dataset-generator/config/default.yaml runs/ahead_dataset/generator_config.yaml

# 플래너 프로세스는 플래너의 pac_common을 사용한다.
python -m pac_planning.ahead_dataset \
  --dataset runs/ahead_dataset \
  --generator-config runs/ahead_dataset/generator_config.yaml \
  --adapter-config config/ahead_adapter.simulation.yaml \
  --scenario S0001 --max-steps 3 \
  --output runs/ahead_dataset_smoke.json
```

생성 당시 설정을 복사해 같이 보관한다. 다른 config로 생성된 데이터를 섞지 않는다.
`test_data/*.json`만 있으면 아직 처리할 현재 박스가 없으므로, 생성된
`simulation_observations/`, `manifest.json`, `file_hashes.json`, `splits.json`도 필요하다.
파일 해시를 검사하므로 입력을 수정할 때는 생성기를 다시 실행한다.

## 학습·평가용 기존 생성기 교체

```bash
python -m pac_planning.experiment \
  --ahead-dataset runs/ahead_dataset \
  --generator-config runs/ahead_dataset/generator_config.yaml \
  --adapter-config config/ahead_adapter.simulation.yaml \
  --train-groups 12 --validation-groups 4 --holdout-groups 4 \
  --epochs 80 --aggregate-rounds 1 --max-steps 4 \
  --output runs/ahead_training
```

각 group 수는 해당 split에서 사용할 시나리오 개수의 상한이다.
`--max-steps`는 학습용 방문 상태 수를 제한한다. 벤치마크는 기본적으로
선택된 평가 에피소드를 끝까지 진행하거나 배치가 막히면 중단한다.
빠른 연결 확인에만 `--benchmark-max-steps 2`를 붙일 수 있다.
이 경우 출력의 `truncated_for_smoke=true`이고 전체 적재 성능으로 해석하면 안 된다.

기존 `models/dual_head_ranker.json`은 이전 데이터로 학습했으므로 이 연결에서
자동으로 OOD fallback 처리한다. 새로 학습한 모델에는 데이터·연결 설정의
계약을 저장한다. 같은 계약을 가진 모델만 이 경로에서 사용한다.
이 계약 검사는 입력 설정의 일치 확인이며 모델 정확도 보증은 아니다.

```bash
python -m pac_planning.ahead_dataset \
  --dataset runs/ahead_dataset \
  --generator-config runs/ahead_dataset/generator_config.yaml \
  --adapter-config config/ahead_adapter.simulation.yaml \
  --model runs/ahead_training/dual_head_ranker.json \
  --scenario S0001 --max-steps 3
```

학습·validation·holdout은 생성기가 정한 scenario 단위 분할을 유지한다.
데이터 증강으로 같은 원본 시나리오를 늘릴 경우에는 그 원본 그룹 단위로도
분할해야 한다. 현재 원본은 각각 별도 seed로 생성된 시나리오다.

## 유지하는 코드와 실행 의미

- `experiment.make_episode`: 이전 실험 재현용으로 보존. `--ahead-dataset` 경로에서는 호출 안 함.
- `scenarios.sample_scenarios`: 5-⑤의 미래 가정 7종 생성 기능이므로 유지.
- 5-③ 특징 45개, 5-④ 순위/미래가치 학습, 5-⑤ rollout, 5-⑥ 점수화: 기존 처리 사용.
- 현재 관측된 박스만 tracked_boxes에 추가하고 unseen 재고를 한 번 차감.
- 실제 미래 JSONL은 실험 드라이버만 순차적으로 읽고, 플래너에는 현재 상태만 전달.
- 입력의 conveyor pose는 그대로 보존. 후보의 pallet 모서리 좌표는 기존 정의를 따름.
- 초기 부분 적재 상태나 오차 있는 CCTV 관측은 이번 v1 연결에서 지원하지 않음.
- EMS/LBCP/로봇 검사는 기존과 같이 팀원 모듈 연결이 필요. 이 검증은 reference backend 사용.

## 브랜치에 추가할 때

압축에는 변경·추가 파일만 담았다. 현재 동한 브랜치의 저장소 루트 아래
`pac-mission1-shared/` 경로에 맞춰 복사한다. 기존 `experiment.py`만 교체하고
나머지는 새 파일이다. 기존 공통 타입·모델 파일을 삭제하거나 덮어쓰지 않는다.
현재 작업에 추가 수정이 있었다면 `experiment.py`의 차이를 먼저 비교한다.

권장 커밋: `feat: connect AHEAD dataset to placement planner training`

```bash
python -m pytest -q
```

검증 범위와 실제 결과는 `reports/AHEAD_COMPATIBILITY.md`를 참고한다.
