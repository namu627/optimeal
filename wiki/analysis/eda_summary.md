---
type: analysis
tags: [eda, ratio, 이상치, 가설검정]
source: docs/decisions_summary_v5_7.md, tasks/todo.md
updated: 2026-04-06
---

# EDA 요약

## ratio 이상치 기준 (2026-03-20 확정, 변경 금지)

```
ratio < 0.2 또는 ratio > 3.0 → 이상치 제거
```

- 도메인 기준 채택 (IQR 기준 미채택 — 재현성 문제)
- 필터링 후 **950건 잔존**
- 이상치는 분석에서만 제외, DB 레코드는 보존

## 데이터 규모

| 항목 | 수치 |
|------|------|
| 전체 레시피 쌍 | 171쌍 확정 |
| 이상치 제거 후 관측 수 | 950건 |
| MixedLM 사용 관측 수 | 607건 |

## H0₃ 가설 검정 (2026-03-20)

**조리방법 대분류 간 b 차이 one-way ANOVA**

| 지표 | 값 |
|------|----|
| F 통계량 | 0.45 |
| p-value | **0.655** |
| 결론 | 기각 불가 — 그룹 간 b 차이 유의하지 않음 |

논문 해석: 3대분류 간 스케일링 지수의 차이가 통계적으로 유의하지 않으나, 영양사 피드백 기반 세부 보정 필요성 존재.

## 비가열 검정력 분석 (2026-03-20)

- 비가열 n=207 기준
- f²=0.15에서 검정력: **0.9994**
- α=0.05, 1-β=0.80 목표 충족

## 실행 스크립트

```bash
python module_2/tests/EDAbeforeMixedLM/eda/eda_main.py
```

→ ratio 이상치: [[decisions/adr_001_ratio]]
→ MixedLM 수치: [[analysis/mixedlm_b_results]]
