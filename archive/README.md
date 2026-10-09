# archive — 보존용 자료

이 폴더의 파일은 **실행 코드가 아니라 기록**입니다. 원본을 고치지 않고 그대로 복사했습니다.
ChatGPT Work가 만든 ZIP 7개의 내용 중 프로젝트 본체에 없는 것과, 이전 v2 전달물을 모았습니다.
ZIP 7개의 SHA-256과 파일 수 검증 기록은 [provenance/](provenance/)에 있습니다.

| 폴더 | 내용 | 출처 ZIP |
|---|---|---|
| `v2_experiments/` | v2 실험 전체: 36 시나리오 / 864 박스 원본 데이터, fresh 12 / 288, 라벨 shard, packed 배열, 체크포인트, 선택 모델, 평가 보고서 | `PAC2026_Donghan_Model_v2_20261009.zip` |
| `v2_delivery/` | v2 문서·패치·로그 (이전 저장소 루트에 있던 것). **현재 기준과 다른 옛 안내가 포함되어 있음** | 위와 같음 |
| `v3_evidence/` | v3 테스트 기록(XML/로그), 패치 왕복, 로봇 preflight, teacher 속도, 체크포인트 재개 확인 | `PAC2026_Donghan_v3_20261009_code.zip` |
| `v3_physics_runs/` | 실제 Bullet 측정 루프 실행 결과 (solid / slatted pallet) | 위와 같음 |
| `v3_delivery_docs/` | v3 제공 안내, 14,400 학습 가이드, 로봇 시연 가이드, 테스트 설명 | 위와 같음 |
| `patches_v3/` | v3 누적/증분 패치, 팀 runtime 패치, 물리 0 N 패치 | 위와 같음 |
| `team_runtime_files_v3/`, `v2_delivery/team_runtime_files/` | 팀 runtime 파일(패치 적용 후 전체본) | 위와 같음 |
| `physics_reference_jaesung_7f4ae37/` | 팀 물리 시뮬레이터 참조 복사본 (`yang8988/pac-mission1-shared`, `feature/jaesung-physics-simulator` @ `7f4ae37`, 2개 파일은 로컬 수정) | 위와 같음 |
| `team_integration_1008/` | 10/08 팀 통합 패치와 replay 검증 기록 | `PAC2026_Donghan_Team_Integration_20261008.zip` |
| `generator_training_1008/` | 10/08 생성기 연동 학습 결과(v1 작은 실험) | `PAC2026_Generator_Model_Training_20261008.zip` |
| `ahead_dataset_adapter/` | AHEAD 데이터셋 어댑터 초안(설정, 문서, 테스트, 픽스처) | `PAC2026_AHEAD_Dataset_Adapter.zip` |
| `ci_reference_not_active/` | 이전 GitHub Actions 설정. 이 저장소에서는 **활성화하지 않음** (팀 패키지가 없고 액션 버전 확인 필요) | `PAC2026_Donghan_Planner_5_3_to_5_6.zip` |

## 알아둘 점

- **누락**: v3 학습 재개용 ZIP 6개(dataset, training_arrays_models, training_shards 01~04)는 확보되지 않았습니다.
  해당 파일 1,112개의 목록은 `provenance/missing_v3_files.txt`입니다.
- 프로젝트 본체와 이름이 같은 구버전 파일(예: v1/v2 시점의 `model_pipeline.py`)은 중복 보존하지 않았습니다.
  현재 본체의 파일이 v3 최신본입니다.
- `v2_experiments/development/selected_model.json`의 SHA-256은
  `54b446300f28184b634de12859ac66ad9dd27f5a2513e1810febf0349608d48f`로 v2 문서가 적은 값과 일치합니다.
  이 모델은 DBLF 기준선을 이기지 못해 기본 모델로 승격되지 않았습니다.
- 이 폴더의 `ci_reference_not_active/`를 `.github/`로 옮기기 전에는 CI가 동작하지 않습니다.
- 라이선스: 원본 저장소들에 라이선스 파일이 없고, 여기서도 임의로 부여하지 않았습니다.
