# 3인 팀 협업 방법

**하나의 monorepo에서 모듈별 디렉터리를 나누고, 짧은 기능 브랜치 → PR → 테스트·동료 확인 → main 순서로 합칩니다.**
각자의 전체 프로젝트를 다시 업로드하면 공통 상태·설정·의존성이 중복되므로, 담당 폴더의 변경만 올립니다.

## 폴더와 브랜치

동한 외 두 사람의 세부 담당은 여기서 임의 확정하지 않습니다.

| 작업 | 담당 경계 | 권장 위치 | 브랜치 예 |
|---|---|---|---|
| 인식·측정·추적 | 흐름도 1~2 | `ros2_ws/src/pac_perception/` | `feature/perception-tracking` |
| 운영 상태·행동 | 흐름도 3~4, 8 | 담당자가 State Manager 단일 소유 | `feature/state-manager` |
| 후보 생성·필수 제약 | 5-①~② | `pac_planning/candidates/`, `pac_planning/constraints/` | `feature/candidate-hard-mask` |
| 특징·AI·미래·최종 점수 | **동한, 5-③~⑥** | 이번 PR의 `pac_planning` 핵심 파일 | `feature/donghan-placement-planner` |
| 로봇·실행·사후 확인 | 흐름도 6~7 | `ros2_ws/src/pac_robot/` | `feature/robot-validation` |
| 공통 모델·계약 | 한 사람이 수정, 다른 1명 확인 | `pac_common`, `config/default.yaml`, 공통 문서 | `refactor/common-contract` |

## 이번 최초 통합

처음 확인 시 저장소가 비어 있어 PR의 기준이 될 최초 README 커밋이 필요합니다.
실제 구현은 별도 기능 브랜치와 PR에 올립니다. 공통 계약 파일이 포함되므로 팀원 1명 이상이 확인한 뒤 병합합니다.
완성된 이 PR을 먼저 합치고 팀원들이 최신 `main`에서 각자 브랜치를 만들면 공통 자료형을 다시 만들 필요가 없습니다.
긴급히 병렬 진행해야 하면 이 기능 브랜치를 기준으로 작업하고, 선행 PR 병합 뒤 자신의 PR 대상은 `main`으로 맞춥니다.

첫 리뷰에서 확인할 추가 약속은 [integration.md](docs/integration.md)의 세 가지입니다.
좌표 원점, `PlanningContext`, 검사기의 `ConstraintEvidence`입니다. 기존 v0.2 원문은 수정하지 않았습니다.

## GitHub Desktop으로 작업

1. 이 저장소를 Clone하고 `Current Branch`를 `main`으로 선택합니다.
2. `Fetch origin` 후 원격 변경이 있으면 `Pull origin`을 실행합니다.
3. `Branch` → `New Branch`로 기능 브랜치를 만듭니다.
4. 해당 저장소 안에서 담당 파일을 수정합니다. 다른 저장소 폴더 전체를 복사하지 않습니다.
5. 터미널에서 `python -m pytest -q`와 담당 모듈 예제를 실행합니다.
6. `Changes`에서 의도한 파일만 포함됐는지 보고 `feat: ...`, `fix: ...` 등으로 Commit합니다.
7. 첫 업로드는 `Publish branch`, 이후는 `Push origin`입니다.
8. `Preview Pull Request` → base `main` 확인 → `Create Pull Request`를 누릅니다.
9. PR에는 담당 단계, 입출력 변경, 실행 명령, 테스트 결과, 남은 연결점을 씁니다.
10. 팀원 확인과 자동 검사 통과 후 병합하고, 모두 다시 `main`을 Pull합니다.

공식 안내: [브랜치 관리](https://docs.github.com/en/desktop/making-changes-in-a-branch/managing-branches-in-github-desktop),
[Desktop에서 PR 만들기](https://docs.github.com/en/desktop/working-with-your-remote-repository-on-github-or-github-enterprise/creating-an-issue-or-pull-request-from-github-desktop).

## 충돌을 줄이는 규칙

- 코드 소유자는 내부 구현을 자유롭게 바꿔도 됩니다. 입출력 변경은 먼저 공통 계약 PR로 설명합니다.
- 각자의 코드 안에 `BoxState`, `SystemState`, `PlacementCandidate`를 다시 정의하지 않습니다.
- `config/default.yaml`을 각자 덮어쓰지 않고, 공통 키 변경은 영향 모듈을 PR에 기록합니다.
- 현장 경로·카메라 장치명은 `config/local.yaml`; 이 파일은 Git에서 제외됩니다.
- 대형 학습 데이터·실행 로그는 `runs/`에 두고 생성 명령·시드·작은 결과 요약을 커밋합니다.
- 하루 한 번 이상 최신 main과 맞춥니다. 다른 사람 브랜치를 강제 push하거나 충돌 해결 중 상대 파일을 통째로 버리지 않습니다.
- `main`은 동료가 바로 설치·테스트할 수 있는 상태로 유지합니다.

저장소 관리자는 가능하면 main 보호 규칙에 PR 및 동료 1명 승인을 설정하고 CI 결과를 필수로 지정합니다.
이번 작업에서는 관리자 보호 설정이나 리뷰어 지정을 임의로 바꾸지 않습니다.

## 다음 통합 순서

| 순서 | 할 일 | 완료 기준 |
|---|---|---|
| 1 | 공통 타입·추가 계약 리뷰 | 세 모듈이 같은 클래스를 import |
| 2 | 후보 생성 / Hard Mask 교체 | 같은 fixture에서 실제 EMS·LBCP evidence로 planner 실행 |
| 3 | 상태·재고 연결 | 현재/preview/버퍼를 잔여 수량에 중복 계수하지 않음 |
| 4 | 로봇 검증 연결 | stale 검사 후 후보 순서대로 IK·충돌·접근·하강·후퇴 검사 |
| 5 | 실행 결과 연결 | 실제 완료 후에만 State Manager가 PLACED와 새 version 확정 |
| 6 | 동일 시퀀스 비교 | Greedy / Current-only / Teacher / AI / AI+Rollout KPI 기록 |
