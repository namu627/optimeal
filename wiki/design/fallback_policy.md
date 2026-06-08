---
type: design
tags: [fallback, 신뢰도, 룩업, 예외처리]
source: docs/decisions_summary_v5_7.md, docs/data_flow_documentation_v1_4.md
updated: 2026-04-06
---

# Fallback 정책 및 신뢰도 플래그

## 3단계 룩업 우선순위 (ADR-002 v3)

| 순위 | 키 | estimation_method | 신뢰도 플래그 |
|------|----|------------------|--------------|
| 1 | `(cooking_method_id × ingredient_category)` | `nutritionist_feedback` | `high` |
| 2 | `(group_type × ingredient_category)` | `mixedlm` / `curve_fit` | `medium` |
| 3 | `ingredient_category` 평균 | `category_mean` | `low` |

코드 패턴: `ORDER BY lookup_priority ASC LIMIT 1`

## 최종 fallback (low 단계 없을 경우)

전체 평균 지수 사용 → `estimation_method = 'category_mean'`, 신뢰도: `low`

## 신뢰도 플래그 의미

| 플래그 | 의미 | 근거 |
|--------|------|------|
| `high` | 현장 영양사 피드백으로 검증된 세부값 | 세부 조리방법 피드백 보정 |
| `medium` | 통계 분석(MixedLM)으로 도출된 3대분류값 | b=0.6163 등 통계 수치 |
| `low` | 카테고리 평균 또는 전체 평균 추정 | 미등록 조합 |

## 구현 주의사항

- 1순위 존재하면 2순위 조회 안 함 (LIMIT 1)
- `is_active = TRUE` 조건 필수
- se_b는 DB에서 읽을 것, 하드코딩 금지 (ADR-003)

→ 엔진 흐름: [[design/scaling_engine]]
→ 우선순위 근거: [[decisions/adr_002_mixedlm]]
