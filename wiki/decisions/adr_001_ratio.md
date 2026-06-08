---
type: decision
tags: [adr, ratio, 수식, 핵심]
source: docs/decisions_summary_v5_7.md
updated: 2026-04-06
---

# ADR-001 — ratio 종속변수 정의 및 멱함수 수식

## 결정 (2026-02-22)

**종속변수를 스케일링 비율(ratio)로 정의한다.**

```
ratio = 실제 대규모 투입량 / (base_amount × N)
역변환: Y = a × base_amount × N^b
Baseline: ratio = 1  (b=1, 선형)
```

- `Y`: 대규모 투입량 (g)
- `base_amount`: 소규모 1인분 투입량 (g)
- `N`: 목표 인원수
- `a`: 멱함수 계수 (보정 상수)
- `b`: 멱함수 지수 (스케일링 지수)

## MixedLM 설계식

```
log(ratio) = log(a) + (b-1)×log(N) + β₁×Category + u_method
고정효과: log(N), Category (재료 카테고리 5종)
랜덤효과: u_method ~ N(0, σ²)
```

## b 해석

| b 값 | 의미 | 해당 재료 |
|------|------|----------|
| b < 1 | 대량 시 덜 필요 | 양념류, 수분류 |
| b ≈ 1 | 선형 | 주재료 |
| b > 1 | 대량 시 더 필요 | 흡수형 재료 |

**확정 수치**: b = 0.6163 (→ [[analysis/mixedlm_b_results]])

## 기각안

`Y = a × (base×N)^b` — b가 "인원수 스케일링 지수"로 해석 불가, MixedLM 설계식과 충돌 → **기각**

## 주의사항

- 엔진 출력 시 역변환 필수: `Y = ratio × base × N`
- ratio 이상치 기준: **[0.2, 3.0]** (2026-03-20 확정, 변경 금지)
- → 상세: [[analysis/eda_summary]]
