# 생성기 기반 동한 모델 학습 연결

먼저 TRAINING_GUIDE_KO.md를 읽는다.

- pac-mission1-shared/: 동한 프로젝트 전체 코드 스냅샷. 기존 브랜치의 동일 폴더에 해당하며 팀장 코드·생성기 전체는 별도 checkout이 필요하다.
- training_result/: 이번에 실제 생성·수집·학습·평가한 결과와 실험용 모델.
- patches/from_remote_146797e.patch: 현재 동한 게시본 146797e 기준 누적 보완.
- patches/training_only_after_meeting.patch: 앞선 회의용 보완을 이미 적용한 경우의 학습 연결만의 패치.

두 패치는 같은 변경을 일부 포함하므로 둘 다 적용하지 않는다.
현재 원격 게시본이라면 저장소 루트에서 다음 명령을 실행한다.

```bash
git switch feature/donghan-placement-planner
git pull --ff-only
git status --short
git apply --check /압축해제경로/patches/from_remote_146797e.patch
git apply /압축해제경로/patches/from_remote_146797e.patch
```

새 작업/빈 작업공간에서는 pac-mission1-shared 폴더를 사용하고 팀장·생성기 버전은 아래로 맞춘다.
- 팀장: fd683e56e67af8e2de743b80456f9fd2177a3eef
- 생성기: cc4c075daa3dd4c7f80510042732763fc9502933

제공 모델: pac-mission1-shared/models/team_fd683e56_smoke.json
함께 쓸 설정: pac-mission1-shared/config/team_fd683e56_smoke.yaml
실제 기록: pac-mission1-shared/reports/team_generator_training_20261008.json

기존 파일을 무조건 삭제하거나 팀장/main 코드를 덮어쓰지 않는다. 새 모델은 작은 데이터로 검증한 실험용이며 기본 모델은 유지된다. 이번 작업은 로컬 결과와 패치 제공이며 원격에 자동 게시하지 않았다. ROS2/MoveIt/물리 pick&place는 아직 실행하지 않았다.
