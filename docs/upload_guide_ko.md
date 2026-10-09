# 이번 파일을 팀 GitHub에 올리는 방법

대상: https://github.com/yang8988/pac-mission1-shared

코드·모델·테스트·문서는 완성됐지만 자동 업로드의 최초 README 생성 요청이
GitHub에서 `403 Resource not accessible by integration`으로 거절됐습니다.
연결된 계정의 저장소 push 권한과 ChatGPT GitHub 앱의 쓰기 권한은 서로 다를 수 있습니다.
따라서 이번 결과에는 생성된 원격 브랜치·커밋·PR이 없습니다.

## GitHub Desktop으로 바로 업로드

1. ZIP을 풀면 `pac-mission1-shared` 폴더가 나옵니다.
2. GitHub Desktop에서 팀 저장소를 Clone합니다. ZIP 안 폴더를 새로운 별도 저장소로 만들지 않습니다.
3. 아직 저장소가 비어 있다면, Clone한 폴더에 간단한 `README.md` 한 개를 만들고
   `chore: initialize team repository`로 main에 최초 Commit → Publish/Push합니다.
4. `Branch` → `New Branch`에서 `feature/donghan-placement-planner`를 만듭니다.
5. ZIP 안 `pac-mission1-shared`의 **내용물**을 Clone한 저장소 루트로 복사합니다.
   `README.md`, `pyproject.toml`, `ros2_ws`, `tests`가 루트 바로 아래여야 합니다.
   `.github`, `.gitignore`도 포함합니다. `.git` 폴더를 건드리지 않습니다.
6. `python -m pip install -e '.[dev]'`, `python -m pytest -q`로 확인합니다.
7. 변경 목록을 확인하고 `feat: add low-level placement planner stages 5-3 to 5-6`으로 Commit합니다.
8. `Publish branch` → `Preview Pull Request` → base `main` → `Create Pull Request` 순서로 올립니다.
9. [PR 본문 초안](pr_body_ko.md)을 복사해서 사용합니다.
10. 공통 계약의 좌표 원점·PlanningContext·ConstraintEvidence를 다른 팀원 1명 이상 확인한 뒤 병합합니다.

작업 중 팀원이 이미 파일을 올렸다면 최신 main을 Pull하고 기존 파일을 먼저 대조하세요.
공통 타입·설정·README를 다른 사람 변경 위에 무조건 덮어쓰지 않습니다.

## 자동 업로드를 다시 사용할 경우

연결된 GitHub 앱이 이 저장소에 파일 내용을 쓸 수 있도록 연결/설치 권한을 확인해야 합니다.
팀 저장소 소유자가 `yang8988`이므로 필요하면 소유자에게 해당 앱의 저장소 접근 설정을 확인받으세요.
단순히 개인 계정의 collaborator 권한이 있다는 사실만으로 앱의 쓰기 권한이 보장되지는 않습니다.
정확한 GitHub 설치 권한의 변경은 소유자/관리자가 확인해야 합니다.

## 통합 후 일상 작업

이후에는 [CONTRIBUTING.md](../CONTRIBUTING.md)의 기능 브랜치와 PR 절차를 사용하면 됩니다.
자동 검사는 `.github/workflows/checks.yml`에 준비돼 있으며, GitHub에 올라간 뒤 실행 결과를 확인하세요.
