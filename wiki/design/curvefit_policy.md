---
type: design
tags: [curve_fit, 보조분석, MixedLM, 정책]
source: docs/decisions_summary_v5_7.md, .claude/rules/statistics.md
updated: 2026-04-06
---

# curve_fit 실행 정책

## 핵심 원칙

**curve_fit은 보조·비교용. MixedLM이 항상 최우선.**

## 실행 조건

| 조건 | 처리 |
|------|------|
| 셀당 N ≥ 10 | curve_fit 실행 허용 |
| 셀당 N < 10 | curve_fit 실행 금지, MixedLM 결과만 사용 |

## b값 불일치 처리

```
|b_MixedLM - b_curvefit| > 0.1 → MixedLM 값 채택 (ADR-002)
```

- 차이 발생 셀 수 및 비율 → 분석 리포트에 보고 의무
- 이 규칙은 CLAUDE.md 핵심 제약사항에도 명시됨

## 모델 수식

```python
from scipy.optimize import curve_fit

def ratio_model(N, a, b):
    return a * np.power(N, b - 1)

popt, pcov = curve_fit(ratio_model, N_arr, ratio_arr)
# popt = [a, b]
```

## estimation_method 기록

- curve_fit 결과 적재 시: `estimation_method = 'curve_fit'`
- `sample_size`: 각 셀 실제 N 기록 필수

## 실제 결과 (2026-03)

- 비수렴 셀 별도 기록: `curve_fit_comparison_table.xlsx`
- 모든 주요 셀에서 MixedLM 채택

→ MixedLM 결과: [[analysis/mixedlm_b_results]]
→ 전략 근거: [[decisions/adr_002_mixedlm]]
