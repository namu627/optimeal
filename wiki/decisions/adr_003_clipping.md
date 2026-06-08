---
type: decision
tags: [adr, 피드백, 클리핑, 파라미터업데이트]
source: docs/decisions_summary_v5_7.md
updated: 2026-04-06
---

# ADR-003 — 파라미터 업데이트 수식 및 클리핑 전략

## 결정 (2026-02-23)

**단순 이동 평균 업데이트 + 2단계 독립 클리핑 채택**

## 업데이트 수식

```python
b_new = (b_old × n_old + b_feedback) / (n_old + 1)  # 단순 이동 평균
```

EMA(지수이동평균) 기각 이유: α 선택 정당화 어려움, 피드백 5회 고정 소규모에 부적합

## b_feedback 역산 수식

```python
ratio_adj = (실제 투입량 + Δ_g) / (base_amount × N)
b_feedback = log(ratio_adj / a_old) / log(N) + 1
```

## 2단계 독립 클리핑

| 단계 | 대상 | 방법 |
|------|------|------|
| **1단계 (입력)** | `Δ_g` | IQR 기반 이상치 제거 |
| **2단계 (출력)** | `b_new` | 도메인 지식 기반 범위 클리핑 |

멘토링 제안(`b_feedback` 자체에 ε 클리핑) 기각 이유:
1. 정상 피드백도 차단 가능
2. 업스트림 `Δ_g` 오류 미탐지
3. 이중 방어 부재

## 클리핑 임계값 ε

`ε = 2 × se_b` — DB의 `scaling_coefficient.se_b` 컬럼에서 읽을 것. **코드 하드코딩 금지.**

→ se_b 적재 현황: [[status/open_issues]]

## 감사 추적 컬럼 (nutritionist_feedback 테이블)

| 컬럼 | 역할 |
|------|------|
| `delta_g_original` | 1단계 클리핑 전 원본 Δ_g |
| `delta_g_clipped` | 클리핑 후 실제 적용 Δ_g |
| `b_new_raw` | 2단계 클리핑 전 업데이트 결과 b |
| `is_clipped` | 클리핑 발생 여부 플래그 |

**이 컬럼들은 학술 재현성 요건으로 삭제 금지.**

## 구현 현황

`module_2/src/engine/feedback_update.py` — ADR-003 수식, 2단계 클리핑, 48 tests passed ✅
