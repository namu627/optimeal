# OptiMeal DB ERD v4.2 — 테이블 관계 문서

**스키마 버전**: v4.2 (2026-02-23, ADR-003 반영)  
**이전 버전**: v4.1 (2026-02-22, 로드맵 v4.0 / ADR-001·002 반영)  
**총 테이블 수**: 17개 (모듈 1: 1개 / 모듈 2: 12개 / 모듈 3: 4개)  
**총 뷰 수**: 6개

---

## 1. 모듈별 테이블 구성

```
┌─────────────────────────────────────────────────────────────────┐
│                    [모듈 2] 비선형 스케일링 엔진 (12개)              │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────────────┐            │
│  │  cooking_method   │──▶│         recipe            │            │
│  │  (조리법 마스터)    │   │  (레시피 마스터, 소-대 통합)  │            │
│  │  ⭐ group_type    │   └──────┬───────────────────┘            │
│  └──────────────────┘         │                                │
│                                │                                │
│  ┌──────────────────────┐     │    ┌──────────────────────┐     │
│  │ ingredient_cooking_  │     ├───▶│ recipe_ingredient_map│     │
│  │ behavior (거동 특성)  │     │    │ (레시피-재료 매핑)      │     │
│  └──────────┬───────────┘     │    └──────────────────────┘     │
│             │                 │                                  │
│  ┌──────────▼───────────┐     │    ┌──────────────────────┐     │
│  │    ingredient         │─────┤───▶│  recipe_similarity   │     │
│  │  (식재료 마스터)       │     │    │  (다층 유사도) ⭐ NEW  │     │
│  │  ⭐ waste_rate        │     │    │  ⭐ is_manually_      │     │
│  │  ⭐ color_category    │     │    │    verified           │     │
│  └──────────┬───────────┘     │    └──────────────────────┘     │
│             │                 │                                  │
│  ┌──────────▼───────────┐     │    ┌──────────────────────┐     │
│  │ ingredient_synonym    │     ├───▶│  scaling_analysis    │     │
│  │ (정규화 사전) ⭐ NEW   │     │    │  (3종 비교 분석)      │     │
│  └──────────────────────┘     │    └──────────────────────┘     │
│                               │                                  │
│  ┌──────────────────────┐     │    ┌──────────────────────┐     │
│  │   unit_conversion     │     ├───▶│ ml_training_dataset  │     │
│  │   (단위 환산)          │     │    │ (ML 보조 학습 데이터)  │     │
│  │   ⭐ minimum_order_   │     │    │ ⭐ ratio, group_type  │     │
│  │      unit             │     │    └──────────────────────┘     │
│  └──────────────────────┘     │                                  │
│                               │    ┌──────────────────────┐     │
│                               ├───▶│ scaling_coefficient  │     │
│                               │    │ ⭐ group_type         │     │
│                               │    │ ⭐ estimation_method  │     │
│                               │    └──────────────────────┘     │
│                               │                                  │
│                               │    ┌──────────────────────┐     │
│                               └───▶│nutritionist_feedback │     │
│                                    │ (영양사 피드백)        │     │
│                                    │ ⭐ rubric_score_*     │     │
│                                    └──────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────┐     ┌──────────────────────────────────────────┐
│ [모듈 1] 영양성분 DB  │     │    [모듈 3] CSP Solver (4개)              │
│                      │     │                                          │
│ nutrition_recipe     │     │  user_group ──▶ constraints              │
│ (식약처+식품안전나라)  │────▶│  (타겟 그룹)     (제약 조건)               │
│                      │     │                                          │
└──────────────────────┘     │  ingredient ──▶ ingredient_price ⭐ v4.1 │
                             │               (식재료 시세)               │
                             │                                          │
                             │  ingredient ──▶ seasonal_ingredient ⭐ v4.1│
                             │               (제철 식재료)               │
                             └──────────────────────────────────────────┘
```

---

## 2. 테이블 관계 상세

### 2.1 핵심 관계 (모듈 2)

| FK 소유 테이블 | FK 컬럼 | 참조 테이블 | 참조 컬럼 | 관계 | 설명 |
|---------------|---------|------------|----------|------|------|
| ingredient | behavior_id | ingredient_cooking_behavior | behavior_id | N:1 | 재료의 조리 거동 특성 |
| ingredient_synonym | ingredient_id | ingredient | ingredient_id | N:1 | 이형 표기 → 표준 재료 매핑 |
| recipe | primary_method_id | cooking_method | method_id | N:1 | 주된 조리법 |
| recipe | secondary_method_id | cooking_method | method_id | N:1 | 보조 조리법 (선택) |
| recipe | base_recipe_id | recipe | recipe_id | N:1 (Self) | 기준 1인분 레시피 |
| recipe | nutrition_recipe_id | nutrition_recipe | nutrition_id | N:1 | 영양성분 참조 |
| recipe_ingredient_map | recipe_id | recipe | recipe_id | N:1 | 레시피에 포함된 재료 |
| recipe_ingredient_map | ingredient_id | ingredient | ingredient_id | N:1 | 매핑된 재료 |
| recipe_similarity | small_recipe_id | recipe | recipe_id | N:1 | 소규모 레시피 |
| recipe_similarity | large_recipe_id | recipe | recipe_id | N:1 | 대규모 레시피 |
| scaling_coefficient | cooking_method_id | cooking_method | method_id | N:1 | 조리법별 가중치 (하위호환) |
| scaling_coefficient | ingredient_id | ingredient | ingredient_id | N:1 | 재료별 가중치 |
| scaling_coefficient | ingredient_behavior_id | ingredient_cooking_behavior | behavior_id | N:1 | 거동별 가중치 |
| scaling_coefficient | parent_coefficient_id | scaling_coefficient | coefficient_id | N:1 (Self) | 버전 체인 |
| nutritionist_feedback | recipe_id | recipe | recipe_id | N:1 | 평가 대상 레시피 |
| nutritionist_feedback | applied_coefficient_id | scaling_coefficient | coefficient_id | N:1 | 반영된 가중치 |
| scaling_analysis | base_recipe_id | recipe | recipe_id | N:1 | 기준 레시피 |
| scaling_analysis | target_recipe_id | recipe | recipe_id | N:1 | 대상 레시피 |
| ml_training_dataset | base_recipe_id | recipe | recipe_id | N:1 | 소규모 레시피 |
| ml_training_dataset | target_recipe_id | recipe | recipe_id | N:1 | 대규모 레시피 |
| ml_training_dataset | ingredient_id | ingredient | ingredient_id | N:1 | 학습 대상 재료 |
| ml_training_dataset | primary_method_id | cooking_method | method_id | N:1 | 조리법 |
| ml_training_dataset | ingredient_behavior_id | ingredient_cooking_behavior | behavior_id | N:1 | 거동 특성 |

### 2.2 모듈 3 관계 (CSP Solver)

| FK 소유 테이블 | FK 컬럼 | 참조 테이블 | 참조 컬럼 | 관계 | 설명 |
|---------------|---------|------------|----------|------|------|
| constraints | group_id | user_group | group_id | N:1 | 타겟 그룹의 제약 |
| constraints | ingredient_id | ingredient | ingredient_id | N:1 | 제약 대상 재료 |
| constraints | alternative_ingredient_id | ingredient | ingredient_id | N:1 | 대체 재료 |
| ingredient_price | ingredient_id | ingredient | ingredient_id | N:1 | 시세 대상 식재료 ⭐ v4.1 |
| seasonal_ingredient | ingredient_id | ingredient | ingredient_id | N:1 | 제철 대상 식재료 ⭐ v4.1 |

---

## 3. 핵심 데이터 흐름

### 3.1 스케일링 엔진 파이프라인

```
[입력] 소규모 레시피 (recipe, serving_category='소규모')
  │
  ▼
ingredient_synonym ──▶ 재료명 정규화 (이형 표기 → 표준 ingredient_id)
  │
  ▼
recipe_ingredient_map ──▶ 재료 목록 + 투입량 (amount_in_grams)
  │
  ▼
recipe_similarity ──▶ 대규모 레시피와 매칭 (composite_score ≥ 0.7)
  │                   group_type 기준 1차 필터링 [ADR-002]
  │                   is_manually_verified 30% 이상 보장 [ADR-002]
  ▼
cooking_method.group_type ──▶ 3대분류 판별 (dry_heat/moist_heat/no_heat) [ADR-002]
  │
  ▼
scaling_coefficient ──▶ (group_type × ingredient_category) 룩업 [ADR-002]
  │                      estimation_method 기록 (mixedlm 주 / curve_fit 보조)
  │                      수식 [ADR-001]: ratio = power_law_a × N^(power_law_b - 1)
  │                      역변환: Y = power_law_a × base_amount × N^power_law_b
  ▼
[출력] 대규모 레시피 (목표 인원수에 맞는 재료량) + 신뢰도 플래그 (high/medium/low)
  │
  ▼
nutritionist_feedback ──▶ 루브릭 5점 평가 → scaling_coefficient 업데이트 [ADR-002]
```

### 3.2 CSP Solver 파이프라인

```
[입력] user_group + constraints (알레르기 22종·기저질환·식단가 예산)
  │
  ▼
ingredient_price ──▶ 1g당 단가 조회 (식단가 Hard Constraint)
  │
  ▼
seasonal_ingredient ──▶ 월별 제철 점수 조회 (freq_score, Soft Constraint)
  │
  ▼
nutrition_recipe ──▶ 메뉴별 칼로리/영양소 조회
  │
  ▼
CSP Solver (OR-Tools) ──▶ Hard Constraint 만족 + Soft Constraint 최적화
  │  Hard: 칼로리 ±10%, 알레르기 22종 완전 배제, 기저질환 상한, 예산 ≤ 식단가×N
  │  Soft: 메뉴 중복 회피(3일), 끼니별 배분, 제철 ≥40%, 색감 다양성
  │  Scoring: (영양 × 0.4) + (예산 × 0.3) + (제철·색감 × 0.2) + (다양성 × 0.1)
  ▼
recipe ──▶ 선택된 메뉴의 레시피 조회
  │
  ▼
scaling_coefficient ──▶ 목표 인원수에 맞게 스케일링 (Y = a × base × N^b)
  │
  ▼
ingredient.waste_rate ──▶ 발주량 계산 (정미량 / (1 - waste_rate))
  │
  ▼
unit_conversion.minimum_order_unit ──▶ 발주 최소 단위 Ceil 처리
  │
  ▼
[출력] 7일/31일 식단표 (공통식 + 대체식) + 발주 목록 + 로봇 JSON 명세
```

---

## 4. Self-Reference 관계

| 테이블 | FK 컬럼 | 설명 |
|--------|---------|------|
| recipe | base_recipe_id → recipe_id | 대규모 레시피가 기준 1인분 레시피를 참조 |
| scaling_coefficient | parent_coefficient_id → coefficient_id | 가중치 버전 체인 (v1→v2→...→final) |

---

## 5. v3 → v4 변경사항

| 변경 유형 | 내용 |
|-----------|------|
| **테이블 추가** | `ingredient_synonym` (재료명 정규화 사전) |
| **테이블 추가** | `recipe_similarity` (다층 유사도 매칭 결과) |
| **테이블 개편** | `scaling_coefficient` — LLM 제거, 멱함수 파라미터(a, b, R², p_value) 추가 |
| **테이블 개편** | `scaling_analysis` — scaling_method ENUM 변경 |
| **컬럼 추가** | `recipe_ingredient_map.per_serving_grams` (1인분 환산) |
| **컬럼 추가** | `ingredient.subcategory` (재료 소분류) |
| **뷰 추가** | `v_similarity_summary`, `v_scaling_lookup` |

---

## 6. v4 → v4.1 변경사항 ⭐ ADR-001·002 반영 (2026-02-22)

| 변경 유형 | 테이블 | 내용 |
|-----------|--------|------|
| **컬럼 추가** | `cooking_method` | `group_type VARCHAR(20)`: 3대분류 (dry_heat/moist_heat/no_heat) [ADR-002] |
| **컬럼 추가** | `scaling_coefficient` | `group_type VARCHAR(20)`: 룩업 키를 조리법 ID 대신 3대분류로 변경 [ADR-002] |
| **컬럼 추가** | `scaling_coefficient` | `estimation_method VARCHAR(20)`: 추정 전략 기록 (mixedlm/curve_fit/category_mean) [ADR-002] |
| **수식 변경** | `scaling_coefficient` | 구버전 `Y=a×X^b` → ADR-001 Option A: `ratio=a×N^(b-1)`, 역변환: `Y=a×base×N^b` |
| **컬럼 추가** | `recipe_similarity` | `is_manually_verified BOOLEAN`: 수동 직접 확인 완료 플래그 (목표 30% 이상) [ADR-002] |
| **컬럼 추가** | `ingredient` | `waste_rate NUMERIC(4,3)`: 폐기율 (발주 시스템용) |
| **컬럼 추가** | `ingredient` | `color_category VARCHAR(10)`: 색감 분류 (CSP Soft Constraint용) |
| **컬럼 추가** | `unit_conversion` | `minimum_order_unit DECIMAL(10,2)`: 발주 최소 단위 |
| **컬럼 추가** | `ml_training_dataset` | `ratio DECIMAL(8,4)`: ADR-001 종속변수 (ratio=실제량/(base×N)) |
| **컬럼 추가** | `ml_training_dataset` | `group_type VARCHAR(20)`: 3대분류 Feature [ADR-002] |
| **컬럼 추가** | `nutritionist_feedback` | `rubric_score_*`: 루브릭 5점 척도 3항목 + 평균 [ADR-002] |
| **테이블 추가** | `ingredient_price` | 식재료 시세 (CSP 식단가 Hard Constraint + 원가 계산용) |
| **테이블 추가** | `seasonal_ingredient` | 제철 식재료 (CSP Soft Constraint, freq_score 기반) |
| **뷰 수정** | `v_active_coefficients` | group_type, estimation_method 컬럼 추가 |
| **뷰 수정** | `v_feedback_stats` | avg_rubric_score 추가 |
| **뷰 수정** | `v_scaling_lookup` | 룩업 키를 (ingredient_category × group_type) 기준으로 변경 |

---

## 7. v4.1 → v4.2 변경사항 ⭐ ADR-003 반영 (2026-02-23)

| 변경 유형 | 테이블 | 컬럼 | 내용 |
|-----------|--------|------|------|
| **컬럼 추가** | `scaling_coefficient` | `se_b DECIMAL(8,6)` | MixedLM이 추정한 b의 표준오차(SE). 출력 클리핑 임계값 ε = 2×se_b 산출 기준. 코드 하드코딩 금지, DB에서 셀별로 읽을 것. 5월 1회차 피드백(5/4) 전 적재 완료 필수 [ADR-003] |
| **컬럼 추가** | `nutritionist_feedback` | `delta_g_original DECIMAL(10,2)` | 1단계 IQR 클리핑 적용 전 영양사 입력 원본 Δ_g(g). 감사 추적(Audit Trail) 목적 — 삭제 금지 [ADR-003] |
| **컬럼 추가** | `nutritionist_feedback` | `delta_g_clipped DECIMAL(10,2)` | 1단계 IQR 클리핑 후 실제 ratio_adj 계산에 사용된 Δ_g(g). NULL이면 클리핑 미발생 [ADR-003] |
| **컬럼 추가** | `nutritionist_feedback` | `b_new_raw DECIMAL(8,4)` | 2단계 출력 클리핑 적용 전 가중 평균 업데이트 결과 b값. 클리핑 효과 분석용 [ADR-003] |
| **컬럼 추가** | `nutritionist_feedback` | `is_clipped BOOLEAN` | 1단계(Δ_g IQR) 또는 2단계(b_new SE) 중 어느 하나라도 클리핑 발생 시 TRUE. 영양사 입력 검토 트리거 [ADR-003] |

---

## 8. v4.2 → v4.3 변경사항 ⭐ ADR-002 v3 반영 (2026-03-07)

| 변경 유형 | 테이블/뷰 | 컬럼/항목 | 내용 |
|-----------|-----------|-----------|------|
| **컬럼 값 추가** | `scaling_coefficient` | `estimation_method` 허용값 | `'nutritionist_feedback'` 추가 [ADR-002 v3]. 영양사 피드백으로 보정된 세부 조리방법별 b값 식별용. 기존 통계 도출값(mixedlm/curve_fit)과 명확히 구분 |
| **뷰 개편** | `v_scaling_lookup` | `lookup_priority` 컬럼 추가, `coefficient_id`·`cooking_method_id`·`se_b` 노출 추가 | 룩업 우선순위 컬럼 도입 [ADR-002 v3]: 1순위=세부 조리방법 피드백값, 2순위=3대분류 통계값, 3순위=category_mean. 엔진 코드에서 `ORDER BY lookup_priority ASC LIMIT 1` 패턴으로 사용 |

### 룩업 우선순위 정의 [ADR-002 v3]

| 우선순위 | 조건 | estimation_method | 설명 |
|---------|------|-------------------|------|
| 1 | `cooking_method_id IS NOT NULL` AND `estimation_method = 'nutritionist_feedback'` | `nutritionist_feedback` | 세부 조리방법 피드백 보정값 |
| 2 | `group_type IS NOT NULL` AND `estimation_method IN ('mixedlm', 'curve_fit')` | `mixedlm` / `curve_fit` | 3대분류 MixedLM 통계 도출값 |
| 3 | 그 외 | `category_mean` | 카테고리 평균 fallback |

---

## 9. 뷰 목록

| 뷰 이름 | 용도 | v4.1 변경 | v4.2 변경 | v4.3 변경 |
|---------|------|-----------|-----------|-----------|
| `v_recipe_pairs` | 확정된 소-대규모 레시피 페어 조회 | — | — | — |
| `v_active_coefficients` | 현재 활성 스케일링 가중치 (멱함수 파라미터 포함) | group_type, estimation_method 추가 ⭐ | se_b 컬럼 추가 ⭐ | — |
| `v_feedback_stats` | 영양사 피드백 통계 (회차별 상용가능 비율) | avg_rubric_score 추가 ⭐ | is_clipped 집계(clipping_rate) 추가 ⭐ | — |
| `v_cooking_method_combinations` | 조리법 조합 통계 | — | — | — |
| `v_similarity_summary` | 유사도 매칭 현황 요약 ⭐ v4 신규 | — | — | — |
| `v_scaling_lookup` | Rule-based 엔진용 룩업 테이블 뷰 ⭐ v4 신규 | group_type 기준 키로 변경 ⭐ | — | `lookup_priority` 컬럼 추가, 세부 조리방법 조회 경로 추가 ⭐ |
