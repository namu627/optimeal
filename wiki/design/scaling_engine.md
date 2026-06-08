---
type: design
tags: [엔진, rule-based, 설계, 흐름]
source: docs/data_flow_documentation_v1_4.md, docs/PRD_v1_5_20260314.md
updated: 2026-04-06
---

# Rule-based 스케일링 엔진 설계

## 처리 흐름

```
[입력] recipe_id, target_serving (N)
  │
  ▼ Step 1: 레시피 조리방법 조회 → group_type 확인
  │         (dry_heat / moist_heat / no_heat)
  │
  ▼ Step 2: 재료 목록 조회
  │         ingredient_synonym → ingredient.category
  │
  ▼ Step 3: v_scaling_lookup 뷰에서 파라미터 조회
  │         우선순위: → [[design/fallback_policy]]
  │
  ▼ Step 4: 역변환 계산
  │         Y = power_law_a × base_amount × N^power_law_b
  │
  [출력] 재료별 Y 값 + 신뢰도 플래그 (high/medium/low)
```

## 핵심 수식 (ADR-001)

```python
Y = power_law_a * base_amount_g * (target_serving ** power_law_b)
```

→ 수식 근거: [[decisions/adr_001_ratio]]

## 룩업 테이블 구조

- 뷰: `v_scaling_lookup`
- 키: `(group_type × ingredient_category)` — 기본
- 키: `(cooking_method_id × ingredient_category)` — 세부 피드백 보정값

## 구현 파일

| 파일 | 역할 | 상태 |
|------|------|------|
| `module_2/src/engine/lookup.py` | DataFrame/딕셔너리 룩업 | D-3 진행 중 |
| `module_2/src/engine/scaling_engine.py` | 통합 엔진 | D-4 예정 |
| `module_2/src/engine/feedback_update.py` | 파라미터 업데이트 | ✅ 완료 |

→ 진행 현황: [[status/module2_progress]]

## 검증 기준

- Baseline 대비 MAE 20% 이상 개선
- MAPE < 15%
- → [[analysis/baseline_mape]]
