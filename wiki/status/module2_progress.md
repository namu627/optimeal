---
type: status
tags: [모듈2, 진행현황, 스프린트]
source: tasks/todo.md
updated: 2026-05-19
---

# 모듈 2 구현 현황

## 현재 스프린트 (5/19~): 정식 학습 대기

> **다음 작업**: `python module_2/src/engine/train_nn.py` — 자세한 진입점은 `tasks/todo.md` 최상단 참조.

| 태스크 | 상태 |
|--------|------|
| nn_training_data.py — 학습 데이터 생성 | ✅ 5/19 |
| nn_scaling_engine.py — 1D ResNet 모델 | ✅ 5/19 |
| train_nn.py — 학습 파이프라인 | ✅ 5/19 |
| test_nn_engine.py — 단위 테스트 (20 PASS) | ✅ 5/19 |
| torch / scikit-learn 설치 | ✅ 5/19 (.venv) |
| Quick(10 epoch) 검증 | ✅ 5/19 (mean MAPE 94.57%, 워밍업) |
| **정식 학습 (60 epoch × 5-Fold)** | 🔲 다음 세션 |
| 학습 결과 분석 + `wiki/analysis/nn_training_results.md` | 🔲 학습 후 |
| requirements.txt torch / sklearn 추가 (담당자 직접) | 🔲 |
| git tag v-stat-complete | 🔲 미완 |
| git tag v-module2-complete | 🔲 NN 학습 통과 후 |

### 설계 핵심 (2026-05-19 확정 / 구현 완료)
- 입력: (B, 3, 30) — log(amount) + category + mask
- 슬롯: 주재료(0~5), 부재료(6~11), 양념류(12~23), 수분류(24~27), 유지류(28~29)
- N: log(N) concat after flatten
- 출력: (B, 30) log(scaled_amount), 마스킹 슬롯 제외
- 아키텍처: Conv1d(3→32) + ResBlock×2(32→64) + Flatten + FC(128) + FC(30)
- 데이터: df_B.csv × 7 N값, Y=base×N^b_feedback
- 데이터 규모: 205 레시피 × 7 N = 1435 샘플, valid_slots=4249

→ 설계 상세: [[design/nn_resnet_design]]
→ 전환 narrative (발표용): [[../../docs/for_reports/module2_transition_narrative]]
→ ADR 요약: [[../decisions/adr_006_resnet]] (예정) 또는 `docs/decisions_summary_v5_7.md` §ADR-006

## 폐기 확정 항목 (ADR-006)

| 항목 | 폐기일 | 사유 |
|------|--------|------|
| Rule-based D-3/D-4/D-5 | 2026-05-19 | 1D ResNet으로 대체. MAPE 정확도 입증 불가 |
| FR-06 ML 분류기 | 2026-05-09 | 라벨 충돌 17건, b차이 < SE(b) |
| FR-07 ML 예측기 | 2026-05-19 | NN이 전체 통합 |

## 완료 항목

| 항목 | 완료일 |
|------|--------|
| 모듈 1 구축 | 2026-02 |
| 소규모 레시피 수집 (~4,000건) | 2026-02 |
| 재료명 정규화 사전 v4.1 | 2026-03-01 |
| 매칭 코드 완성 (171쌍) | 2026-03-20 |
| EDA + ratio∈[0.2,3.0] 확정, 950건 잔존 | 2026-03-20 |
| MixedLM B안 확정 (b=0.6163, AIC=1262.87) | 2026-03-20 |
| OLS vs MixedLM AIC 비교 (ΔAIC=-67.13) | 2026-03-26 |
| 5-Fold GroupKFold CV (b std=0.0275) | 2026-03-27 |
| H0₃ ANOVA (F=0.45, p=0.655) | 2026-03-20 |
| 영양사 피드백 5회차 (50레시피, 98% 승인) | 2026-05-08 |
| scaling_coefficients.csv 확정 (331행) | 2026-05-08 |
| lookup.py 버그 수정 (118 tests) | 2026-05-08 |
| MAPE 비교 방법론 결정 (FAIL → NN 전환) | 2026-05-08 |
| **1D ResNet 4파일 구현 + 20 신규 PASS** | 2026-05-19 |
| ADR-006 1D ResNet 전환 결정 (decisions_summary §ADR-006) | 2026-05-19 |
| 전환 narrative 작성 (`docs/for_reports/module2_transition_narrative.md`) | 2026-05-19 |

→ 통계 수치: [[../analysis/mixedlm_b_results]]
→ 피드백 b값: [[../analysis/feedback_b_validation]]
→ NN 설계: [[../design/nn_resnet_design]]
