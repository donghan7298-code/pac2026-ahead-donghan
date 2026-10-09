# 동한 v3 제공본 적용 안내

2026-10-09 제공본. Python/실제 Bullet 검증을 수행했다. ROS2 Humble·MoveIt·Gazebo
로봇 실행과 새 C++ grasp plugin build는 미검증이다. main에 자동 병합하지 않았다.
ZIP 자체를 GitHub 코드로 올리는 대신 아래 코드 파일/패치를 적용한다.

| 경로 | 용도 |
|---|---|
| full_donghan_branch | 동한 branch source 전체 167개 파일; repo root 구조 보존 |
| upload_to_donghan_branch | 원격 7860043 이후 바뀐 101개 코드/문서 파일 |
| upload_batches/batch_01, batch_02 | 웹 업로드용 60개 이하 분할; 각 폴더의 내용만 업로드 |
| team_runtime_files | 팀장 81d0333 기준 별도 패치 9개 파일 |
| patches | 동한 누적/증분, 팀, shared Bullet 0 N 선택 패치 |
| experiments/source_dataset | 실제 180 scenario / 14,400 원본 박스 |
| experiments/training_checkpoint | 검증된 수집 shard, packed array, 학습 checkpoint |
| experiments/physics | 완료·실패를 구분한 실제 물리 기록 |
| evidence | pytest XML/로그, source heads, 패치 적용·byte 검증 |

현재 ZIP의 학습 상태는 experiments/training_checkpoint/progress_snapshot.json에 고정했다.
완료한 수집은 180/180,
query 26,906개, candidate label 377,767개다.
phase=TRAINED_AWAITING_FULL_EVALUATION; held-out 전체 비교 완료=False.
학습 epoch나 label 수를 로봇 실행 횟수로 세지 않는다. 모델을 기본으로 승격하지 않았다.

## 브라우저로 내 브런치에 올리기

1. feature/donghan-placement-planner의 root를 연다.
   https://github.com/yang8988/pac-mission1-shared/tree/feature/donghan-placement-planner
2. Add file → Upload files를 선택한다.
3. batch_01 **안에 들어 있는 내용**을 드래그한다. batch_01이나 files라는
   바깥 폴더를 통째로 끌어 넣지 않는다. README.md는 root,
   pac-mission1-shared/ros2_ws/...는 그 경로로 보여야 한다.
4. commit 대상을 내 feature branch로 확인하고 기록한다. 같은 방식으로 batch_02를 올린다.
5. full_donghan_branch 전체를 한 번에 웹 업로드하지 않는다. GitHub 공식 안내의
   한 번에 100개 파일 제한 때문에 분할했다.
   https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository

upload_to_donghan_branch와 batch 두 개는 같은 변경 파일의 다른 묶음이다.
둘을 중복 적용하지 않는다. team_runtime_files/physics_reference/experiments는
내 branch 코드 root에 올리지 않는다. 기존 files/ 폴더가 생겼다면 root 경로를
먼저 확인하며, 이 제공본은 원격의 다른 폴더를 자동 삭제하지 않는다.

## Ubuntu에서 패치로 적용

기존 checkout을 지우지 않고 새로운 checkout에 적용한다.
현재 원격이 이 제공본의 기준에서 변경됐다면 git apply --check 결과를 먼저 확인한다.

```bash
AHEAD_V3_BUNDLE=~/Downloads/PAC2026_Donghan_v3_20261009
mkdir -p ~/AHEAD
git clone --branch feature/donghan-placement-planner --single-branch \
  https://github.com/yang8988/pac-mission1-shared.git ~/AHEAD/pac-donghan
cd ~/AHEAD/pac-donghan
git rev-parse HEAD
# 이 누적 패치의 원격 기준: 7860043228c81aec517c5a1966d475d36e12f68a
git switch -c feature/donghan-demo-v3
git apply --check "$AHEAD_V3_BUNDLE/patches/donghan_from_remote786_v3.patch"
git apply "$AHEAD_V3_BUNDLE/patches/donghan_from_remote786_v3.patch"
git diff --check
git add --all
git commit -m "Add original-box measured execution and v3 training"
```

이전 v2 제공본이 이미 정확히 적용된 checkout이면 from_v2_705b5bb 패치의
--check를 사용하고 위 누적 패치와 **둘 중 하나만** 적용한다.
local v3 source snapshot SHA는 913e0d0c318393fbf77d022736504936ef5bdbaf다. 실제 원격 commit SHA가 아니다.
패치는 원격 업로드 작업을 대신 수행하지 않는다.

팀 checkout은 따로 준비한다. 팀장 영역의 patch는 팀장이 검토할 9개 파일만 포함한다.

```bash
git clone --branch claude/pensive-pasteur-dwbu3g --single-branch \
  https://github.com/yang8988/pac-mission1-shared.git ~/AHEAD/pac-team
cd ~/AHEAD/pac-team
git rev-parse HEAD
# 팀 원격 기준: 81d0333ba6550d9ee6f02661d8b8d9e79beaca6c
git switch -c integration/donghan-v3
git apply --check "$AHEAD_V3_BUNDLE/patches/team_from_81d0333_v3.patch"
git apply "$AHEAD_V3_BUNDLE/patches/team_from_81d0333_v3.patch"
```

shared Bullet의 0 N 두 파일 수정은 별도 선택 패치다. 이번 원격에는 아직 반영되지 않았고,
Gazebo repository에 이 패치를 적용하지 않는다. 제공 physics_reference로 standalone
Bullet 재현은 가능하다. 같은 이름의 두 pac_simulation을 colcon workspace에 넣지 않는다.

## 실행 순서

1. MODEL_14400_GUIDE_KO.md: pip/venv 준비, 저장한 path만 relocate, all --resume.
2. ROBOT_DEMO_GUIDE_KO.md: Ubuntu22.04/Humble, pinned Hyundai submodule, 14개 package build.
3. preview → 실제 원본 1개 → 실제 원본 6개 → ordered trace checker 순서로 진행한다.
4. 첫 시연은 동한 weighted score/look-ahead를 사용한다. 현재 학습의 2 mm/0.22 m 계약과
   시연의 3 mm/0.1525 m 계약이 다르므로 제공 학습 모델을 억지로 연결하지 않는다.
5. 원본 8개의 동한 BUFFER_CURRENT와 10개 experimental fixture를 성공한 시연으로 표시하지 않는다.

현재 실제 결과: 동한98 pass/0 skip, 팀202 pass/4 skip.
동한 h3/s7 원본6개와 DBLF 원본8개는 두 deck의 measured Bullet loop를 완료했다.
실제 로봇 동작·camera/scale·physical buffer/pallet replacement는 다음 검증이다.

SHA256SUMS.txt는 ZIP 안의 제공 파일 byte 검증용이다.
