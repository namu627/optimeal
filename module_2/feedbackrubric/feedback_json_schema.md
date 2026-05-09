# 피드백 수집 JSON 양식 설계

**작성일:** 2026-04-05
**기준 문서:** ADR-003, DB 스키마 v4.3 (nutritionist_feedback 테이블), FR-09
**상태:** ✅ 확정

---

## 1. 개요

영양사 피드백 수집 시 사용하는 JSON 입력 양식.
`feedback_update.py`의 `apply_feedback()` 함수가 이 구조를 입력으로 받는다.

---

## 2. 피드백 1건 JSON 구조

```json
{
  "feedback_meta": {
    "recipe_id": 201,
    "feedback_round": 1,
    "nutritionist_name": "홍길동",
    "nutritionist_id": "N001",
    "feedback_date": "2026-05-04"
  },
  "rubric_scores": {
    "taste_seasoning": 4,
    "cooking_feasibility": 5,
    "field_applicability": 4
  },
  "ingredient_adjustments": {
    "고추장": "-100g",
    "물": "+500ml",
    "소금": "0g"
  },
  "detailed_comments": "볶음 레시피 전반적으로 간이 약간 진함. 특히 고추장 투입량 축소 필요."
}
```

### 필드 설명

#### `feedback_meta`

| 필드 | 타입 | 필수 | 설명 |
|------|------|:----:|------|
| `recipe_id` | int | ✅ | 평가 대상 레시피 ID (recipe 테이블 참조) |
| `feedback_round` | int (1~5) | ✅ | 피드백 회차 |
| `nutritionist_name` | string | ✅ | 평가자 이름 |
| `nutritionist_id` | string | ✅ | 평가자 식별자 (Cohen's Kappa 계산용) |
| `feedback_date` | string (YYYY-MM-DD) | ✅ | 평가 날짜 |

#### `rubric_scores`

| 필드 | 타입 | 범위 | 설명 |
|------|------|:----:|------|
| `taste_seasoning` | int | 1~5 | 맛·간 적절성 (rubric_score_taste) |
| `cooking_feasibility` | int | 1~5 | 조리 실현 가능성 (rubric_score_feasibility) |
| `field_applicability` | int | 1~5 | 대량 조리 현장 적합성 (rubric_score_field) |

`rubric_avg = (taste_seasoning + cooking_feasibility + field_applicability) / 3`

#### `ingredient_adjustments`

| 구조 | 설명 |
|------|------|
| 키 | 재료명 (한국어 표준명 또는 ingredient_synonym 등재 이형명) |
| 값 | `"+[양]g"` / `"-[양]g"` / `"+[양]ml"` / `"-[양]ml"` / `"0g"` (변경 없음) |

**표기 규칙:**
- `"+100g"` → Δ_g = +100 (더 넣어야 함)
- `"-50g"` → Δ_g = -50 (덜 넣어야 함)
- `"0g"` 또는 키 자체 미포함 → 해당 재료 수정 없음
- ml 단위 재료는 `unit_conversion` 테이블로 g 변환 후 처리

**주의:** Δ_g 입력값 = `delta_g_original` (클리핑 전 원본). `feedback_update.py`가 IQR 클리핑 적용 후 `delta_g_clipped`를 산출함.

---

## 3. 배치 제출 양식 (회차별 10개)

```json
{
  "batch_meta": {
    "feedback_round": 1,
    "group_type": "dry_heat",
    "cooking_method": "볶기",
    "evaluation_date": "2026-05-04",
    "evaluators": ["N001", "N002"]
  },
  "feedbacks": [
    {
      "feedback_meta": { "recipe_id": 201, "feedback_round": 1, "nutritionist_name": "홍길동", "nutritionist_id": "N001", "feedback_date": "2026-05-04" },
      "rubric_scores": { "taste_seasoning": 4, "cooking_feasibility": 5, "field_applicability": 4 },
      "ingredient_adjustments": { "고추장": "-100g", "물": "+500ml" },
      "detailed_comments": ""
    },
    {
      "feedback_meta": { "recipe_id": 202, "feedback_round": 1, "nutritionist_name": "홍길동", "nutritionist_id": "N001", "feedback_date": "2026-05-04" },
      "rubric_scores": { "taste_seasoning": 5, "cooking_feasibility": 4, "field_applicability": 5 },
      "ingredient_adjustments": {},
      "detailed_comments": "양 적절. 수정 불필요."
    }
  ]
}
```

---

## 4. DB 저장 매핑

JSON 필드 → `nutritionist_feedback` 테이블 컬럼 대응:

| JSON 경로 | DB 컬럼 | 비고 |
|-----------|---------|------|
| `feedback_meta.recipe_id` | `recipe_id` | FK |
| `feedback_meta.feedback_round` | `feedback_round` | |
| `feedback_meta.nutritionist_name` | `nutritionist_name` | |
| `feedback_meta.nutritionist_id` | `nutritionist_id` | |
| `feedback_meta.feedback_date` | `feedback_date` | |
| `rubric_scores.taste_seasoning` | `rubric_score_taste` | |
| `rubric_scores.cooking_feasibility` | `rubric_score_feasibility` | |
| `rubric_scores.field_applicability` | `rubric_score_field` | |
| 자동 계산 | `rubric_avg` | `(T+F+A)/3`, round 2 |
| 자동 계산 | `overall_rating` | avg 기준 판정 |
| `ingredient_adjustments` (JSON) | `ingredient_feedback` | JSON 컬럼 |
| `feedback_update.py` 출력 | `delta_g_original` | Δ_g 클리핑 전 원본 |
| `feedback_update.py` 출력 | `delta_g_clipped` | 1단계 IQR 클리핑 후 |
| `feedback_update.py` 출력 | `b_new_raw` | 2단계 클리핑 전 b_new |
| `feedback_update.py` 출력 | `is_clipped` | 1·2단계 중 클리핑 발생 여부 |
| `detailed_comments` | `detailed_comments` | |

---

## 5. 유효성 검사 규칙

`feedback_update.py`의 `validate_feedback_json()` 함수에서 검사:

1. 필수 필드 누락 여부
2. `feedback_round` ∈ {1, 2, 3, 4, 5}
3. 루브릭 점수 ∈ {1, 2, 3, 4, 5}
4. `ingredient_adjustments` 값 형식: 정규식 `^[+-]?\d+(\.\d+)?(g|ml)$`
5. `feedback_date` YYYY-MM-DD 형식
6. ml 단위 입력 시 경고 (g 변환 필요 알림)
