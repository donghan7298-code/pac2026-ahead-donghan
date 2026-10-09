# 진행 현황 — 2026-10-09 기준

이 문서는 ChatGPT Work에서 만든 AHEAD v3 결과물을 Claude Code 환경으로 옮긴 뒤,
**실제로 다시 확인한 것**과 **확인하지 못한 것**을 구분해 기록한 것입니다.
ChatGPT Work 쪽 보고서의 수치를 여기서 방금 실행한 결과처럼 쓰지 않았습니다.

검증 환경: Windows 10 + WSL2 Ubuntu 22.04.5, Python 3.10.12, ROS 2 Humble(apt), pytest 6.2.5.

## 1. 실제 검증됨 (이 환경에서 직접 실행)

| 항목 | 결과 |
|---|---|
| 소스 상태 | 원격 `feature/donghan-placement-planner` @ `1d83406` 위에 v3 누적 패치를 `pac-mission1-shared/` 범위로 적용 (변경 67개) |
| Python 문법 | 70개 파일 모두 파싱 성공 |
| 단위 테스트 | **95 passed**, 물리 테스트(`test_team_physics.py`)만 제외 (아래 참고) |
| 팀 패치 의존성 | 팀 패치 전 `81d0333` 원본에서도 같은 결과. 패치는 Python 테스트에 불필요 |
| ROS 빌드 | `pac_common`, `pac_planning`, `pac_planning_interfaces`, `pac_execution` 빌드 성공 |
| 학습 파이프라인 import | `scripts/donghan/model_pipeline.py`가 쓰는 14개 모듈 모두 import 성공 |
| 중복 패키지 | 저장소 루트 전체 탐색 시 중복 발생, `--base-paths ros2_ws/src` 지정 시 5개 패키지만 탐색됨 |
| 무결성 | 바이너리/비밀값/캐시 없음. 파일당 최대 약 2 MB |

물리 테스트 제외 이유: `pybullet`과 팀의 `pac_simulation`이 없습니다.
ROS를 `source`한 상태에서 pytest 6은 이 모듈의 skip 하나로 수집을 중단하므로 `--ignore`로 제외했습니다.

### 학습·평가 검증 1·2 (`config/model_development.yaml`)

- 1차 실행: 생성 36 시나리오 / 864 박스 → 수집 → 학습 → 평가가 **exit 0으로 약 1시간 만에 종료**되었습니다.
- **결과 파일은 보존하지 못했습니다.** 출력을 WSL `/tmp`에만 두었는데, WSL이 유휴로 종료되며 사라졌습니다.
  따라서 이 실행의 점수는 근거로 쓰지 않습니다.
- 2차 실행은 Windows 디스크에 출력하도록 다시 진행 중입니다. 완료 후 결과를 이 문서에 추가합니다.
- 수집 단계 로그에서 측정 오차 점검이 `XY_MARGIN_NOT_COVERED`로 나왔습니다. uncertain 박스는 6.025 mm가 필요한데 설정은 4 mm입니다. (v2 문서에 적힌 알려진 간극과 같습니다.)

## 2. 코드만 있고 미검증

| 항목 | 상태 |
|---|---|
| `pac_gazebo_grasp` (C++) | Gazebo Fortress(`ignition-gazebo6`) 개발 패키지가 없어 빌드 실패. 코드는 실행해 본 적 없음 |
| 14개 패키지 workspace | `stage_demo_workspace.py`가 AHEAD 압축본에서 Hyundai submodule(`hdr_description` 등)을 찾지 못해 구성 실패 |
| `hdr50_22_moveit_config` | 출처 저장소를 확인하지 못함 (launch 파일에서만 언급) |
| MoveIt 실행, Gazebo pick & place | 실행한 적 없음 |
| `ros_gz_*`, `ign_ros2_control` | 이 환경에 설치되어 있지 않음 (apt 후보는 존재함, 설치하지 않음) |
| 팀 runtime 패치 (`archive/patches_v3/team_from_81d0333_v3.patch`) | 임시 복사본에 `git apply --check`만 통과. 팀 저장소에는 적용하지 않음 |
| 물리 패치 (`physics_zero_load_optional.patch`) | 같은 방식으로 check만 통과 |

## 3. 앞으로 구현 / 알려진 문제

- **14,400 박스 학습 자료가 없음.** v3 문서가 말한 dataset / training_arrays_models / training_shards 01~04 ZIP이 확보되지 않았습니다.
  누락된 파일 1,112개의 목록: [archive/provenance/missing_v3_files.txt](../archive/provenance/missing_v3_files.txt).
  이 때문에 14,400 학습 재개와 held-out 324 episode 평가는 불가능합니다.
- 학습 모델 계약(허용오차 2 mm, TCP offset 0.22 m)과 시연 설정(3 mm, 0.1525 m)이 다릅니다.
  첫 로봇 실행은 모델 없이(`model_path` 비움) weighted scoring / look-ahead로 시작해야 합니다.
- 팀 마스크 여유 4 mm와 uncertain 박스 요구 6.025 mm의 간극은 학습으로 해결되지 않습니다.
- `BUFFER_CURRENT`, NG, repack, pallet change를 실행할 물리 actuator가 없습니다 (v3 문서 기준: 동한 원본 8개 시나리오는 7번째에서 정지. 여기서 재현하지는 않았습니다).
- fixed joint attach는 진공/골판지 변형 모델이 아닌 시작 구현입니다.
- payload는 설정 50 kg, 과거 대화에는 50 N 표현이 있어 실제 합의된 사양과 대조가 필요합니다.
- v2 문서(`archive/v2_delivery/`)의 기준 SHA와 테스트 수는 현재 상태와 다릅니다. 문서는 수정하지 않고 보존했습니다.

## 4. 다음 단계 (아래 명령은 **아직 실행하지 않은 계획**입니다)

1. v3 학습 재개용 ZIP 6개를 확보해 `archive/provenance/missing_v3_files.txt` 기준으로 검증.
2. WSL에 Gazebo Fortress와 ROS 연동 패키지 설치 (설치 전 패키지가 Fortress용인지 확인):
   `libignition-gazebo6-dev`, `libignition-plugin-dev`, `libignition-transport11-dev`,
   `ros-humble-ros-gz-sim`, `ros-humble-ros-gz-bridge`, `ros-humble-ign-ros2-control`.
3. AHEAD 저장소를 git으로 받아 Hyundai submodule 초기화 (`fix/hyundai-submodule-init`의 `scripts/fetch_hyundai_refs.sh` 확인 후).
4. 14개 패키지 staging과 빌드:
   ```bash
   python3 scripts/donghan/stage_demo_workspace.py --team-root <team> --ahead-root <ahead> --output ~/demo_ws
   cd ~/demo_ws && colcon build --symlink-install
   python3 scripts/donghan/robot_preflight.py --workspace ~/demo_ws --output reports/robot_preflight_local.json
   ```
5. preview(`execute_enabled:=false`) → 원본 1개 → 원본 6개 순으로 실제 pick & place 확인.
6. 시연 계약(3 mm / 0.1525 m)으로 재수집·재학습 후 paired held-out 비교를 통과한 모델만 연결.
