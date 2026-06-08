---
type: decision
tags: [adr, 데이터, 불확정값, amount_type]
source: docs/decisions_summary_v5_7.md
updated: 2026-04-06
---

# ADR-004 — ingredient_amount 불확정값 처리

## 결정 (2026-02-25)

**`amount_type` 컬럼 신설로 수량 표기 상태를 명시적으로 구분 (Option A 채택)**

## amount_type 정의

| 값 | 의미 | ingredient_amount |
|----|------|------------------|
| `MEASURED` | 수치 확정 | 수치 입력 필수 |
| `DISCRETIONARY` | 약간·적당량 등 재량 | NULL 허용 |
| `UNKNOWN` | 원본 데이터 누락 | NULL |

`amount_note`: 원문 표기 보존 ("약간", "적당량" 등)

## 스케일링 처리 방침

- `DISCRETIONARY`: 별도 규칙 (예: `N^0.7`) 적용 또는 제외, 코드에 분기 조건 명시
- `UNKNOWN`: 스케일링 대상에서 **제외**

## 기각안

| 기각안 | 이유 |
|--------|------|
| NULL만 사용 | 데이터 누락 vs 재량 표기 구분 불가 |
| 0으로 채움 | "재료 없음"으로 오인 |
| 완전 제거 | 레시피 완전성 훼손, 비교 연구 불가 |
