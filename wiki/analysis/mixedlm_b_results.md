---
type: analysis
tags: [mixedlm, 통계, b값, 핵심수치, aic_linearity]
source: docs/decisions_summary_v5_7.md, tasks/todo.md
updated: 2026-05-13
---

# MixedLM 분석 결과

## 핵심 수치 (2026-03-20 확정)

| 지표 | 값 |
|------|----|
| **b (스케일링 지수)** | **0.6163** |
| SE(b) | 0.1814 |
| 95% CI | [0.261, 0.972] |
| AIC | 1262.87 (REML) |
| 사용 데이터 | n=607 (이상치 제거 후) |

## OLS vs MixedLM 비교 (2026-03-26)

| 모델 | AIC |
|------|-----|
| OLS (고정효과 only) | 1330.00 |
| MixedLM (랜덤효과 포함) | **1262.87** |
| **ΔAIC** | **-67.13** |

ΔAIC = -67.13 → 랜덤효과 도입 정당성 확보

## 그룹 전략 결과

- SEPARATE: 18셀 (셀당 n≥10 충족)
- MERGE: 13셀 (3대분류로 병합 유지)
- 그룹 수 k < 5 → 논문 방법론에 분산 추정 한계 명시 의무

## 교차 검증 결과 (5-Fold GroupKFold)

- b 표준편차: **0.0275** (안정적)
- H0₁ p=0.24 → 기각 불가 (구조적 한계)
- → 상세: [[analysis/baseline_mape]]

## AIC 비선형성 직접 검증 (2026-05-13 추가)

**비교**: 모델 A (b=1 고정, 선형) vs 모델 B (b 추정, 비선형), ML 적합

| 모델 | AIC | 로그우도 |
|------|-----|---------|
| A (b=1 고정, 선형) | 1265.47 | — |
| B (b 추정, 비선형) | 1262.87 | — |
| **ΔAIC = B - A** | **-2.60** | — |

| 항목 | 값 |
|------|-----|
| LRT 통계량 | 4.60 |
| 자유도 | 1 |
| **p-value** | **0.0319** |
| **판정** | **비선형성 통계적으로 유의 (p < 0.05)** |
| b (ML 추정) | 0.6109 |

> ΔAIC = -2.60 (|ΔAIC| > 2 기준 충족), LRT p=0.032 < 0.05
> → log_N 항(비선형성)이 데이터 적합도를 통계적으로 유의하게 개선함

**기존 AIC 비교와의 구분**:
- 기존 OLS vs MixedLM (ΔAIC=-67.13): 랜덤효과 도입 정당성 검증
- 본 분석 b=1 vs b 추정 (ΔAIC=-2.60, p=0.032): **비선형성 자체 검증**

스크립트: `module_2/otherstest/aic_linearity_test.py`
결과 파일: `module_2/otherstest/aic_linearity_test_result.md`

## 채택 근거

- [[decisions/adr_002_mixedlm]] — MixedLM 최우선 원칙
- |b_MixedLM - b_curvefit| > 0.1이어도 MixedLM 값 채택
- 수렴 방식: REML (실패 시 ML 전환)
