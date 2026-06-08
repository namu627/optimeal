# Wiki 로그

> append-only. 최신 항목이 위에 위치.
> 형식: `## [날짜] 타입 | 제목`
> 타입: ingest / query / lint / update

---

## [2026-05-19] update | 1D ResNet 4파일 구현 완료 + Quick CV 검증 + ADR-006 추가

- 신규 파일 4개:
  - `module_2/src/engine/nn_training_data.py` — df_B.csv → 30슬롯 텐서, N {10,20,50,100,150,200,300} 확장, GroupKFold(5)
  - `module_2/src/engine/nn_scaling_engine.py` — ResNet1D + train/eval/save/load/predict
  - `module_2/src/engine/train_nn.py` — 5-Fold CV 진입점 (`--quick` 옵션)
  - `module_2/tests/test_nn_engine.py` — 신규 20 PASS (정규화/텐서/순전파/마스킹/predict 계약/save&load)
- 의존성: torch 2.12.0 + scikit-learn 설치 (MPS 디바이스 인식)
- Quick(10 epoch) 검증: 데이터 1435 샘플 (205 레시피 × 7 N), valid_slots=4249, 5-Fold mean MAPE=94.57% ± 15.17% (워밍업 수치)
- 테스트 상태: 136 passed, 1 known failure (구 b값 기반 — 무시)
- 신규 문서:
  - `docs/for_reports/module2_transition_narrative.md` — 발표용 변화 흐름 narrative
  - `docs/decisions_summary_v5_7.md` §ADR-006 — 1D ResNet 전환 결정 요약
- 다음 작업: `python module_2/src/engine/train_nn.py` 정식 학습 (60 epoch × 5-Fold, ~10분)

## [2026-05-19] update | 1D ResNet 스케일링 엔진 설계 확정 + Rule-based 폐기

- Rule-based 엔진(D-3/D-4/D-5), FR-06, FR-07 전면 폐기 확정
- 1D ResNet으로 전체 대체. PyTorch 의존성 추가 (requirements.txt)
- 설계 확정:
  - 입력 (B, 3, 30): log(amount) + category + mask 채널, 카테고리별 슬롯 고정
  - Y = base × N^b_feedback → 측정 기반 파생 데이터 (합성 아님)
  - N: {10,20,50,100,150,200,300} 7종으로 데이터 확장
  - 2 ResBlock, Flatten → Concat log(N) → FC 128 → FC 30
  - GroupKFold(5, 단위=small_recipe_id)
- 신규 wiki: `design/nn_resnet_design.md`
- 데이터 소스: `module_2/df_B.csv` (607행, ingredient 단위)

## [2026-05-08] update | 3종 MAPE 비교 분석 및 논문 기술 방향 확정

- 비교: 선형(49.13%) vs MixedLM 사전(44.60%, +9.2%) vs 피드백 후(3.65%, in-sample)
- 학술 주장 근거: 선형 대비 MixedLM +9.2% 개선 (주재료 +26.3%)이 out-of-sample에 가까운 유효 비교
- 피드백 후 b는 in-sample → 성능 수치 아닌 "전문가 보정 최종 파라미터"로만 기술
- wiki/analysis/feedback_b_validation.md 전면 업데이트 (논문 결과/한계/결론 초안 포함)

## [2026-05-08] update | 영양사 피드백 5회차 처리 완료 + b값 최종 확정

- 피드백 파일: `피드백_전체_5회차_20260424_완성.xlsx` (50 레시피, R1~R5)
- CSV 업데이트: `scaling_coefficients.csv` 307행 추가 (기존 24행 → 331행)
- 최종 확정 b: dry_heat 양념류 0.7478 / moist_heat 주재료 0.7359 등 (wiki/analysis/feedback_b_validation.md 참조)
- 버그 수정: `lookup.py` — nutritionist_feedback(group_type 기반) priority 오류 수정
- 검증 방법론: 피드백 전후 MAPE 비교 생략 확정 (모집단 불일치). 신 기준: 피드백 Excel 최종 승인량 기준 MAPE (선형 49.13% → Rule-based 3.65%)
- 신규 wiki: `analysis/feedback_b_validation.md` — 논문 기술 방향 포함
- 테스트: 118 passed

## [2026-04-24] update | 영양사 피드백 Excel 템플릿 시스템 구축

- 신규 파일: `module_2/templates/generate_feedback_excel.py` — 스케일링 결과 → 영양사 전달용 Excel
- 신규 파일: `module_2/templates/parse_feedback_excel.py` — 영양사 작성 Excel → ADR-003 파이프라인 → CSV 업데이트
- 신규 파일: `module_2/templates/sample_recipe.json` — 제육볶음 샘플 (dry_heat, 1→100인분)
- 비고: end-to-end 검증 완료 (generate → parse --dry-run). 118 tests passed. openpyxl==3.1.5 추가 설치.

## [2026-04-06] ingest | Wiki 초기 구성

- 소스: `docs/decisions_summary_v5_7.md`, `docs/PRD_v1_5_20260314.md`, `docs/요구사항정의서_기능명세서_v1_4_20260314.md`, `docs/data_flow_documentation_v1_4.md`, `tasks/todo.md`
- 생성 페이지:
  - decisions: adr_001~005 (5개)
  - analysis: mixedlm_b_results, eda_summary, baseline_mape (3개)
  - status: module2_progress, open_issues (2개)
  - design: scaling_engine, fallback_policy, curvefit_policy (3개)
- 비고: 기존 docs/를 raw source로 보존, wiki는 합성 레이어로 신규 생성
