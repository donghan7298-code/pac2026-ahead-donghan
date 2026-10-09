# 동한 5-③~⑥ v2 적용 안내 — 2026-10-09

생성기 → 실제 팀 RuntimeCore 상태의 EMS teacher → 라벨 저장 → 학습 →
최신 robot checker를 포함한 네 방식 비교까지 연결한 제공본이다.
현재 원격 동한 브랜치 `7860043...`에는 아래 수정이 아직 없다.
원격에 push하거나 main에 merge하지 않았다.

**학습 구조·검증 1/2는 실행했지만, 이번 후보 모델은 DBLF보다 팔레트를
더 사용했다.** 대회 기본 모델을 자동 교체하지 않는다. `MODEL_RESULTS_KO.md`에
실제 360개 episode 결과와 안전 이상이 있다. ROS2 Humble·MoveIt·Gazebo 로봇
실행은 이번 검증에 포함되지 않았다.

## ZIP에서 사용할 부분

| 폴더/파일 | 용도 |
|---|---|
| `upload_to_donghan_branch/` | 현재 원격 동한 브랜치에서 바뀐 44개 파일. 브라우저 업로드용 |
| `full_donghan_branch/` | 동한 브랜치 전체 소스. 기존 root/nested 구조를 유지한 로컬 사용용 |
| `patches/donghan_after_remote_786004.patch` | 현재 GitHub `7860043...` 기준 누적 수정 |
| `patches/donghan_after_0536.patch` | 이전 05:36 후속 제공본을 이미 적용한 로컬 소스용 증분 수정 |
| `patches/team_runtime_after_81d0333.patch` | 팀장의 `81d0333...`에 적용하는 8개 runtime 파일 수정 |
| `team_runtime_files/` | 위 팀장 패치와 동일한 8개 완성 파일 |
| `experiments/development/` | 36/864 데이터·원본 라벨·array·checkpoint·모델·216+144 평가 |
| `experiments/fresh/` | 추가 12/288 독립 생성 데이터 |
| `MODEL_GUIDE_KO.md` | 설치·확장 학습·재개·검증 명령과 코드 설명 |
| `MODEL_RESULTS_KO.md` | 실제 수치·DBLF 비교·미검증 범위·다음 개선 순서 |
| `patch_roundtrip.json` | 깨끗한 기준 소스에 패치 적용 후 제공 파일과 byte 일치 결과 |
| `logs/` | 코드 검사·학습·runtime 원본 로그 |
| `verification.json`, `SHA256SUMS.txt` | 실행 범위 및 파일 무결성 기록 |

`full_donghan_branch`는 동한 담당 코드 전체다. 팀장 runtime/생성기/시뮬레이터는
별도 checkout을 사용한다. 전체 프로젝트를 실제 로봇까지 완성했다는 의미가
아니다. 실험 증거 폴더는 GitHub 웹으로 모두 올리지 않는다.

## 브라우저에서 내 브랜치에 올리기

1. ZIP을 압축 해제한다.
2. GitHub에서 `yang8988/pac-mission1-shared`의
   **`feature/donghan-placement-planner`**를 선택한다.
3. 저장소 맨 위 화면에서 `Add file` → `Upload files`를 연다.
4. `upload_to_donghan_branch` **안에 있는** `README.md`, `README_KO.md`,
   `TRAINING_GUIDE_KO.md`, `verification.json`과 `pac-mission1-shared` 폴더를
   함께 드래그한다. `upload_to_donghan_branch` 폴더 자체를 올리지 않는다.
5. 업로드 목록에 `README.md`와
   `pac-mission1-shared/scripts/donghan/model_pipeline.py`가 보이는지 확인한다.
   경로 앞에 `files/`, `upload_to_donghan_branch/`, `full_donghan_branch/`가
   붙으면 취소하고 폴더 안의 내용만 다시 선택한다.
6. commit 메시지 예: `Scale Donghan training and add paired runtime validation`.
   commit 대상은 **내 feature 브랜치**로 선택한다.
7. 업로드 후 root README의 ‘이번 실제 검증 결과’를 열어 360개 episode 보고서가
   보이는지 확인한다. 팀장 패치 8개는 내 브랜치에 섞어 올리지 않는다.

원격 기준에서 삭제하는 파일은 없으며, 변경된 44개만 올리면 된다.
ZIP 파일 자체를 GitHub에 업로드하는 방식은 실행 소스 갱신이 아니다.

## Ubuntu 터미널에서 패치 적용

기존 작업 파일이 없는 새 checkout을 권장한다. 적용 전에 `git status`로
자기 수정 사항을 확인한다. 아래는 압축 해제한 폴더를
`~/Downloads/PAC2026_Donghan_Model_v2_20261009`라고 가정한 명령이다.
다운로드 위치가 다르면 `AHEAD_V2_BUNDLE`만 바꾼다.

```bash
AHEAD_V2_BUNDLE=~/Downloads/PAC2026_Donghan_Model_v2_20261009
cd ~/AHEAD/pac-donghan
git branch --show-current
git rev-parse HEAD
git status --short
git apply --check "$AHEAD_V2_BUNDLE/patches/donghan_after_remote_786004.patch"
git apply "$AHEAD_V2_BUNDLE/patches/donghan_after_remote_786004.patch"
```

이 누적 패치는 GitHub `7860043228c81aec517c5a1966d475d36e12f68a`의
확인된 파일 상태로 검증했다. 이전 제공본을 이미 로컬에 적용했다면
`donghan_after_0536.patch`로 두 apply 명령의 파일명만 바꾼다.
**동한 패치 두 개를 모두 적용하지 않는다.** `--check`가 실패하면
강제 적용/파일 초기화를 하지 말고 기준 소스가 맞는지 확인한다.

팀 runtime은 별도 checkout의 `81d0333...` 기준으로 다음을 적용한다.
팀원 파일의 대규모 교체는 없으며, 수정 범위는 `team_runtime_files/` 8개다.

```bash
cd ~/AHEAD/pac-team
git rev-parse HEAD
git apply --check "$AHEAD_V2_BUNDLE/patches/team_runtime_after_81d0333.patch"
git apply "$AHEAD_V2_BUNDLE/patches/team_runtime_after_81d0333.patch"
```

clone·환경 설치 명령은 `MODEL_GUIDE_KO.md`에 있다. 코드 수정은 현재
동한의 pac_common/pac_planning과 팀장의 candidates/highlevel/robot_check/runtime을
함께 불러온다. 시뮬레이터의 중복 package를 이 Python 경로 앞에 넣지 않는다.

## 바로 실행하기

동한 프로젝트 root는 `~/AHEAD/pac-donghan/pac-mission1-shared`다.
처음에는 36/864 설정으로 새 실험을 만들고 이후 14,400 설정으로 확대한다.
큰 설정은 이 제공본에서 실행하지 않았다.

```bash
cd ~/AHEAD/pac-donghan/pac-mission1-shared
sudo apt update
sudo apt install -y python3-pip python3-venv
python3 -m venv .venv-model
source .venv-model/bin/activate
python3 -m pip install numpy pyyaml pytest

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
python3 scripts/donghan/model_pipeline.py all \
  --team-root ~/AHEAD/pac-team \
  --generator-root ~/AHEAD/pac-generator/pac-mission1-shared/tools/ahead_dataset_generator \
  --generator-ref cc4c075daa3dd4c7f80510042732763fc9502933 \
  --config config/model_development.yaml \
  --output experiments/development_v2 --workers 3
```

정상적으로 생성기 로그 → 수집 query/후보 수 → epoch validation 로그 →
`evaluated ...`가 나타나고 output에 `selected_model.json`,
`planner_config.yaml`, `runtime_test/report.json`이 남는다.

제공 `experiments/`는 원래 source hash와 실행 기록을 그대로 보존한 증거다.
최종 수정으로 pack 중복 판정 helper만 바뀌었고 원본 array/metadata 재생성은
byte가 같았다. 기존 manifest를 최신 코드 것으로 바꾸지 않는다. 새 코드로
수집·평가를 다시 시작하려면 위처럼 **새 output**을 사용한다. 체크포인트
재개/warm start/새 독립 데이터의 세부 명령은 가이드를 따른다.

## ROS 연결 시 확인할 부분

팀장 최신 로봇 받침대: world `(1.35, 0.15, 0.50)`m. 팔레트 center는
`(1.35, -1.00)`m, deck은 0.15m다. 따라서 base_from_pallet_center는
`(0, 1.15, 0.35, 0)`이며 이전 pose와 혼용하지 않는다.

팀 runtime 패치는 모델과 같은 planner 설정을 읽는 `ranker_config`를 추가했다.
이번 모델 h2/s3에 기본 h3/s7을 적용하면 model fallback이 발생한다.
launch 예시는 가이드에 있지만 ROS2에서 실행해 확인한 명령은 아니다.
현재 시간 기반 JointTrajectory 성공 처리와 place 위치 spawn 경로는 남아 있다.
MoveIt 실행 결과, PlanningScene의 attach/detach, 실제 release 이후 상태 갱신을
확인해야 실제 pick/place 통합 완료로 판단할 수 있다.

측정오차 환경의 L4와 사후 OVERLOADED가 발견됐으므로 안전성이 검증됐다고
말하지 않는다. 모델을 키우는 작업과 geometry/하중/실제 로봇 실행 검증을
함께 진행해야 한다.
