# 적용 안내 (최종 검증: 태현 e7ed1f9, 2026-10-08)

대상은 shared의 feature/donghan-placement-planner이며, 원격 기준은
fe1e135c826cad8b134c637342e4ca5373ccdc73입니다. GitHub 쓰기는 기존 403 제한으로
재시도하지 않았으며, 아래 내용은 모두 원격 미반영 패치입니다.

## 적용 파일 선택

- 이전 TeamPlacer/scene_bridge/ROS service 패치를 적용하지 않았다면 `donghan_integration.patch`.
- 이전 17개 파일 패치를 이미 로컬에 적용했다면 `donghan_followup_e7ed1f9.patch`.
- 두 패치를 함께 적용하지 마세요. check 실패 시 강제로 덮어쓰지 말고 현재 변경과 비교하세요.
- `files/`는 누적 변경 파일을 저장소 루트 기준 경로로 보관한 참고용 사본입니다.

GitHub Desktop에서 동한 브랜치를 선택하고 변경 사항을 보관한 후 저장소 루트 터미널에서:

```bash
git status --short
git branch --show-current
git apply --check /압축해제경로/donghan_integration.patch
git apply /압축해제경로/donghan_integration.patch
```

이전 패치가 적용된 로컬에는 두 apply 명령의 파일명을 donghan_followup_e7ed1f9.patch로 바꾸세요.
검토·테스트한 후 GitHub Desktop에서 Commit, Push origin을 수행할 수 있습니다.
팀원 브랜치나 main을 자동 병합하는 작업은 포함하지 않습니다.

## 이번 보완

하중 검사 → 후보 점수/미래 rollout 연결 회귀 검사, 180°/음수 yaw 처리,
주문 수량 비공개 → 미래 시나리오 연결 검사를 추가했습니다.
동한 계산식과 PPO의 proxy+DBLF 계약은 그대로입니다.

- 동한 전체 검사: 57 passed (실제 PyBullet 검사 3개 포함).
- 태현 검사: 160 passed, 2 skipped (gymnasium/torch 없음).
- 5박스 Rule+TeamPlacer: 5/5 가상 배치, 기하 safety issues 0.
- ROS2 Humble/MoveIt/로봇 pick & place: 미실행.
- 0N 패치는 팀원 검토용이며 물리 저장소에 자동 적용되지 않았습니다.
- 기존 PPO 모델은 변경 전 환경에서 학습된 것이므로 새 환경에서 재평가해야 합니다.

## 실행과 남은 작업

`files/pac-mission1-shared/docs/team_integration_20261008.md`의 설치/빌드/launch 절차와
`files/pac-mission1-shared/docs/load_regression_8e934a4.md`의 최신 e7ed1f9 검증 기록을 보세요.
검사에는 태현 checkout의 pac_candidates와 pac_highlevel이 필요합니다.
물리 테스트는 별도 PyBullet 의존성과 검토용 0N 패치가 있어야 실행됩니다.

다음 단계는 Ubuntu22.04/Humble에서 service를 실제로 빌드·호출하는 것입니다.
그 뒤 팔레트 규격/좌표를 확정하고 MoveIt 단일 박스 실행 및 실제 상태 commit을 연결합니다.
