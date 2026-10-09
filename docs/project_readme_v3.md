**흐름도 5번 Low-level Placement Planner의 ③~⑥**.

새 학습·검증 파이프라인: [동한 모델 학습 v2 가이드](docs/model_training_v2_ko.md).
2026-10-08 팀 후보 검사기·PPO·물리 작업셀 연결 변경과 실행 순서는
[팀 통합 기록](docs/team_integration_20261008.md)을 먼저 확인하세요.
ROS 서비스/launch 코드는 추가됐지만 실제 Humble 실행 검증은 아직입니다.
후보의 특징을 계산하고, AI로 Top-K를 고른 뒤, 같은 미래 시나리오에서 비교하여 순위 목록을 반환
ROS 2가 설치되지 않아도 알고리즘 개발·학습·테스트가 가능

| 흐름도 | 구현 | 설명 |
|---|---|---|
| 5-③ Feature | `pac_planning/features.py` | OPAL 기반 15항목을 SI 단위로 적용하고 정규화·잔여재고·버퍼·의존성·CoG 등으로 45차원 확장 |
| 5-④ AI → Top-K | `pac_planning/model.py` | LambdaRank 순위 헤드 + 별도 미래 성과 회귀 헤드 |
| 5-⑤ Future Rollout | `pac_planning/scenarios.py`, `rollout.py` | 7종 시나리오·공통 난수·Greedy 가상 적재·평균/최악/하위 CVaR/막힘 |
| 5-⑥ 최종 점수 | `pac_planning/scoring.py`, `planner.py` | 안전 포화 + 공간 + 미래 − 하방 위험 − 시간, 후보 목록 반환 |

## 처음 실행

Ubuntu 22.04 / Python 3.10 기준입니다. 저장소 루트에서 실행하세요.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m pac_planning.demo --model models/dual_head_ranker.json --fixed-work
```

`runs/demo/result.json`, `decisions.jsonl`, `preview.svg`가 생성됩니다.
`--fixed-work`를 빼면 1초 **소프트 예산**을 적용합니다. 안전 검사나 외부 콜백이 오래 걸리면 초과할 수 있습니다.
학습 모델 없이 실행하려면 `--model` 옵션을 빼세요. 휴리스틱 Top-K + 실제 롤아웃으로 동작합니다.

![실행 예시](reports/demo_preview.svg)

## 먼저 볼 문서

1. [공통 개발 기준 v0.2 원문](docs/common_development_standard.md)
2. [입출력·함수 연결 예제와 추가 계약](docs/integration.md)
3. [GitHub 협업 방법 / GitHub Desktop 순서](CONTRIBUTING.md)
4. [동한 담당 알고리즘 설명·점수식](docs/low_level_planner.md)
5. [기존 개발·회의·미션·논문 반영표](docs/requirements_traceability.md)
6. [검증 결과와 미완료 연결점](reports/VALIDATION.md)

## 담당 범위의 경계

5-① 후보 생성 / 5-② Hard Mask는 `generate_candidates`, `validate_constraints` 콜백으로 연결
`reference_backend.py`는 단독 테스트용 **완전 지지·단일 하부 박스** 기준선
태현님의 EMS/Extreme Point/LBCP 구현을 대체하거나 이미 구현한 것은 아님

4번 High-level의 버퍼·팔레트 마감·재적재 선택, 6번의 IK/충돌/가반하중 검사,
7번 실행·사후 측정, 8번 실제 State Manager는 각 담당 모듈에서 연결합니다.
출력은 항상 `requires_robot_validation=True`; 이 패키지는 로봇을 움직이거나 ACTUAL 상태를 갱신하지 않음

## 재학습과 비교

```bash
OPENBLAS_NUM_THREADS=1 python -m pac_planning.experiment --output runs/retrain \
  --train-groups 12 --validation-groups 4 --holdout-groups 4 \
  --epochs 80 --aggregate-rounds 1 --seed 20261007
```

teacher: 모든 유효 후보를 평가하고, 방문한 상태를 다시 teacher로 라벨링해 데이터를 누적합니다.
학습/검증/최종 시험은 **박스 구성 그룹**을 분리합니다. PPO 학습은 이 파트의 구현 범위에 포함되지 않습니다.
지금 모델은 작은 합성 데이터로 학습한 통합용 출발점입니다. 데이터 규모·기하 검사기·목표 분포가 바뀌면 다시 평가해야 합니다.

## 저장소 배치

`ros2_ws/src/pac_common`은 공통 dataclass 단일 원본,
`ros2_ws/src/pac_planning`은 이 담당 파트입니다.
설정은 `config/default.yaml`, JSON 입력은 `test_data/`, 테스트는 `tests/`를 사용합니다.
의존성 선언은 루트 `pyproject.toml` 한 곳을 사용합니다.
ROS 패키지의 `setup.py`는 ament 설치용 메타데이터입니다.

ROS 환경이 준비되면 `source /opt/ros/humble/setup.bash` 후 `ros2_ws`에서 `colcon build`할 수 있는 패키지 구조입니다.
실제 ROS 빌드·MoveIt2 연결은 별도 검증이 필요합니다.
