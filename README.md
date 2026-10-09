# PAC 2026 AHEAD — 동한 Low-level Placement Planner (v3 작업 스냅샷)

PAC 2026 AHEAD 프로젝트에서 동한이 맡은 **Low-level Placement Planner 5-③~⑥**
(특징 계산 → AI Top-K → 미래 롤아웃 → 최종 점수)과, 이를 ROS2 Humble + 로봇
시뮬레이터의 연속 pick & place로 연결하기 위한 실행 코드의 작업 저장소입니다.

최종 목표는 현장에서 실제 박스를 인식하고, 적재 위치를 평가하고, 로봇팔이
pick & place를 수행하는 시연입니다. **아직 그 단계에 도달하지 않았습니다.**
현재 상태는 아래 세 가지로 구분해서 읽어 주세요. 자세한 근거는
[docs/PROGRESS_STATUS_20261009.md](docs/PROGRESS_STATUS_20261009.md)에 있습니다.

| 상태 | 내용 |
|---|---|
| 실제 검증됨 | Python 단위 테스트 95 passed (Ubuntu 22.04 / Python 3.10), ROS 패키지 4개 빌드, 학습·평가 파이프라인 import |
| 코드만 있고 미검증 | `pac_gazebo_grasp`(C++ Gazebo 플러그인), 14개 패키지 workspace, MoveIt 실행, Gazebo pick & place |
| 앞으로 구현 | 물리 버퍼/NG/팔레트 교체 actuator, 시연 계약에 맞춘 모델 재학습, 현장 인식 연동 |

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `ros2_ws/src/` | ROS2 패키지: `pac_common`, `pac_planning`, `pac_planning_interfaces`, `pac_execution`, `pac_gazebo_grasp` |
| `scripts/donghan/` | 학습·수집·평가 파이프라인(`model_pipeline.py`), 로봇 시연 준비·검사 스크립트 |
| `config/` | 학습/평가 설정, 원본 박스 시연 설정(`demo_original6/8/10`) |
| `tests/` | 단위·계약 테스트 |
| `docs/` | 개발 문서와 통합 점검 기록 (`project_readme_v3.md`는 이전 README) |
| `models/`, `reports/` | 기존 모델과 검증 보고서 |
| `archive/` | **보존용** 자료. v2 실험 데이터, v3 증거, 패치, 팀 참조 코드, ZIP 출처 기록 ([archive/README.md](archive/README.md)) |

팀원 담당인 후보 생성(5-①), Hard Mask(5-②), Stage 4 PPO, 로봇 체커, 물리 시뮬레이터는
이 저장소에 없습니다. 별도 checkout이 필요합니다.

| 필요한 외부 저장소 | 기준 커밋 |
|---|---|
| 팀 runtime (`yang8988/pac-mission1-shared`, `claude/pensive-pasteur-dwbu3g`) | `81d0333ba6550d9ee6f02661d8b8d9e79beaca6c` |
| 데이터 생성기 (같은 저장소, `feature/jaesung-dataset-generator`) | `cc4c075daa3dd4c7f80510042732763fc9502933` |
| AHEAD 시뮬레이터 (`dlwotjd1289-cloud/pac2026-ahead`, Hyundai submodule 포함) | 확인 필요 (`main` `fa3115a`, `fix/hyundai-submodule-init` `0c8fb80`) |

## 테스트 실행

팀 runtime 체크아웃(`ros2_ws/src/pac_candidates` 등)을 경로에 추가해야 팀 연동 테스트까지 실행됩니다.
아래는 실제로 실행해 본 방식입니다(Ubuntu 22.04, Python 3.10.12, 시스템 pytest 6.2.5).

```bash
TEAM=/path/to/pac-team/ros2_ws/src
source /opt/ros/humble/setup.bash
PYTHONPATH=$PYTHONPATH:ros2_ws/src/pac_common:ros2_ws/src/pac_planning:ros2_ws/src/pac_execution:\
$TEAM/pac_candidates:$TEAM/pac_highlevel:$TEAM/pac_runtime:$TEAM/pac_robot_check \
python3 -m pytest -p no:cacheprovider -q tests --ignore=tests/test_team_physics.py
```

- `tests/test_team_physics.py`는 `pybullet`과 팀 물리 시뮬레이터(`pac_simulation`)가 있어야 하므로 위에서 제외했습니다.
- 시스템 pytest 6은 `pyproject.toml`의 `pythonpath` 옵션을 무시합니다. 위처럼 `PYTHONPATH`를 직접 지정하거나, pytest 7 이상을 설치해 사용하세요.
- 이 환경에서 ROS를 `source`한 상태로 `tests` 전체를 지정하면 pytest 6이 모듈 수준 skip 하나 때문에 수집을 중단하는 현상이 있어, 물리 테스트를 `--ignore`로 제외하는 방식을 썼습니다.

## ROS2 패키지 빌드 (일부만 확인)

저장소 전체를 colcon으로 탐색하면 `archive/` 아래의 복사본과 중복될 수 있으므로,
**반드시 `--base-paths`로 `ros2_ws/src`를 명시**하세요.

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros2_ws/src --packages-select pac_common pac_planning pac_planning_interfaces pac_execution
```

위 4개는 실제로 빌드됐습니다. `pac_gazebo_grasp`는 Gazebo Fortress(`ignition-gazebo6`)
개발 패키지가 있어야 하며, 이를 설치하지 않은 환경에서는 빌드에 실패합니다.
14개 패키지 workspace(`scripts/donghan/stage_demo_workspace.py`)는 AHEAD 저장소의
Hyundai submodule이 필요해서 아직 구성하지 못했습니다.

## 출처와 라이선스

- 원본 코드의 출처와 변경 범위는 [NOTICE.md](NOTICE.md), [docs/requirements_traceability.md](docs/requirements_traceability.md)를 참고하세요.
- `archive/` 안에는 팀원 저장소에서 가져온 파일(팀 runtime 패치, 물리 시뮬레이터 참조)이 있습니다. 모두 공개 저장소 기준 커밋이 `archive/` 문서에 적혀 있습니다.
- **이 저장소와 원본 저장소들 모두 라이선스 파일이 없습니다.** 별도의 오픈소스 라이선스를 임의로 부여하지 않았으며, 정책은 팀이 정합니다.
