---
type: analysis
tags: [baseline, mape, cv, 성능비교]
source: tasks/todo.md, docs/PRD_v1_5_20260314.md
updated: 2026-04-06
---

# Baseline MAPE 및 교차 검증

## 5-Fold GroupKFold CV 결과 (2026-03-27)

| 지표 | 값 |
|------|----|
| b 표준편차 (폴드 간) | **0.0275** |
| H0₁ p-value | 0.24 (기각 불가) |
| 분할 단위 | `base_recipe_id` |

b 표준편차 0.0275 → 교차 검증 전 걸쳐 안정적

## 3종 비교 목표 (검증 기준)

| 모델 | 목표 MAPE |
|------|----------|
| Baseline (선형, ratio=1) | ~25% |
| Rule-based 단독 | ~10% |
| 하이브리드 (Rule-based + ML) | ~7% |

선형 대비 **20% 이상 개선** 필수 (p < 0.05)

## 현황

- Baseline 선형 모델 코드 완성 ✅
  - 위치: `module_2/otherstest/Baseline 선형모델_20260323_박미연/baseline_model_v0.02.py`
- Rule-based 엔진 MAE/MAPE 측정: 4/14~4/19 예정 (D-5)
  - → [[status/module2_progress]]

## 검증 방법론

- paired t-test: 선형 vs 멱함수
- paired t-test: Rule-based vs 하이브리드
- 결과 `scaling_analysis` 테이블에 INSERT

→ CV 설계: [[decisions/adr_002_mixedlm]]
