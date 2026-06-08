---
type: decision
tags: [ADR-006, NN, ResNet, 폐기, MAPE]
decision_date: 2026-05-19
status: ✅ 승인
supersedes: [FR-05 Rule-based, FR-06 ML 분류기, FR-07 ML 예측기]
source: docs/decisions_summary_v5_7.md §ADR-006
---

# ADR-006 — Rule-based 엔진 폐기, 1D ResNet 채택

## 결정 한 줄

**Rule-based 룩업 엔진과 FR-06/FR-07 ML 모듈을 폐기. 모듈 2 스케일링은 1D ResNet 단일 모델로 통합.**

## 전환 이유 (3가지)

1. **MixedLM 통계 b가 MAPE 기준으로 실패** — n=342 기준 선형 32.68% vs MixedLM 34.31% (1.63%p 더 나쁨)
2. **영양사 피드백 b의 MAPE 비교가 구조적으로 불가** — `Y_final = Y_engine + Δ_g` 종속 구조
3. **Rule-based 일반화 한계** — N의 연속 일반화 부재, 셀별 단일 b 조회

## 학술적 역할 분리

| 단계 | 책임 |
|------|------|
| 통계 (MixedLM) | 비선형 관계 *존재* 증명 (LRT p=0.032, ΔAIC=-67.13) |
| 영양사 피드백 | 현장 합의 b 표준 (50건 98% 승인, b≈0.74) |
| 1D ResNet | b 표준의 임의 레시피 *일반화* 적용 |

## 학습 데이터

- `Y = base × N^b_feedback` — **측정 기반 파생 데이터** (합성 아님)
- N 확장: {10, 20, 50, 100, 150, 200, 300} 7종 → 205 레시피 × 7 = 1435 샘플

## 아키텍처

- Input (B, 3, 30): log(base) + category_id + valid_mask
- Conv1d(3→32) → ResBlock(32→32) → ResBlock(32→64) → Flatten + log(N) → FC(128) → FC(30)
- Output (B, 30): log(scaled_amount), 마스킹된 유효 슬롯에서만 MSELoss

## 유지/폐기

| 항목 | 상태 |
|------|------|
| `lookup.py` | ✅ 유지 (NN 학습 데이터 생성 참조) |
| `fallback.py` | ⚠ 유지(비활성, 비교 기준 보존) |
| `feedback_update.py` | ✅ 유지 |
| `scaling_coefficients.csv` | ✅ 동결 (331행) |
| Rule-based D-3/D-4/D-5 | ❌ 폐기 |
| FR-06 / FR-07 | ❌ 폐기 |

## 관련 페이지

- 상세 ADR: `docs/decisions_summary_v5_7.md` §ADR-006
- 변화 흐름 narrative: `docs/for_reports/module2_transition_narrative.md`
- 설계 상세: [[../design/nn_resnet_design]]
- MAPE FAIL 분석: [[../analysis/feedback_b_validation]]
- 진행 현황: [[../status/module2_progress]]
