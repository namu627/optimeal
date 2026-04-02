# OptiMeal — 데이터 흐름 문서 (Data Flow Documentation)

**문서 버전**: v1.2  
**작성일**: 2026-02-16  
**최종 수정일**: 2026-03-06 (로드맵 v5.4.0 / ADR-002 v2 반영)
**기준 문서**: DB 스키마 v4.2, 요구사항 정의서 v1.2, 로드맵 v5.4.0  
**작성자**: 권성민

---

## 1. 전체 데이터 흐름 개요

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         외부 데이터 소스                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ 식약처 OpenAPI │  │ 식품안전나라   │  │ 영양사 서적   │  │ 웹/커뮤니티   │   │
│  │  (1.9GB JSON) │  │ OpenAPI      │  │ (Excel 입력) │  │ (Excel 입력) │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                  │                 │            │
└─────────┼─────────────────┼──────────────────┼─────────────────┼────────────┘
          │                 │                  │                 │
          ▼                 ▼                  ▼                 ▼
┌─────────────────┐ ┌──────────────┐  ┌──────────────────────────────┐
│ [모듈 1]         │ │ 소규모 레시피 │  │ 대규모 레시피                  │
│ nutrition_recipe │ │ (~4,000건)   │  │ (300~350건)                  │
│ (영양성분 DB)    │ │ 1~4인분      │  │ 50~300인분                   │
└────────┬────────┘ └──────┬───────┘  └──────────────┬───────────────┘
         │                 │                          │
         │                 ▼                          ▼
         │         ┌──────────────────────────────────────────┐
         │         │ [모듈 2] 비선형 스케일링 엔진               │
         │         │                                          │
         │         │  Phase 1: 정규화 (ingredient_synonym)     │
         │         │  Phase 2: 매칭 (recipe_similarity)        │
         │         │  Phase 3: 통계 분석 (scaling_coefficient)  │
         │         │  Phase 4: Rule-based + ML 하이브리드       │
         │         │  Phase 5: 영양사 피드백                    │
         │         │  Phase 6: 최종 검증                       │
         │         └──────────────────┬───────────────────────┘
         │                            │
         ▼                            ▼
┌──────────────────────────────────────────────┐
│ [모듈 3] CSP Solver                           │
│ nutrition_recipe + 스케일링 엔진 → 식단 생성    │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ [모듈 4] Frontend + REST API                  │
│ FastAPI → React → 사용자                      │
└──────────────────────────────────────────────┘
```

---

## 2. 모듈 1: 영양성분 DB 데이터 흐름

### 2.1 수집 → 적재

```
식약처 OpenAPI (1.9GB JSON)
  │
  ├─ AsyncIO 비동기 처리
  ├─ JSON 파싱 (식품코드, 식품명, 영양성분 140개 항목)
  ├─ 필수 영양소 추출 (kcal, protein, fat, carbs, sodium, sugar, cholesterol)
  │
  ▼
nutrition_recipe 테이블 INSERT
  │
  ├─ data_source = '식약처'
  ├─ menu_category 자동 분류 (식품대분류명 기반)
  └─ original_data에 원본 JSON 보존

식품안전나라 OpenAPI (1,900건 레시피)
  │
  ├─ 레시피명, 조리방법, 1인분량, 영양성분
  │
  ▼
nutrition_recipe 테이블 INSERT
  │
  ├─ data_source = '식품안전나라'
  └─ 레시피 재료 정보 → recipe + recipe_ingredient_map에도 적재
```

### 2.2 조회 (CSP Solver용)

```sql
-- 메뉴 카테고리별 영양성분 조회
SELECT recipe_name, calories, protein, fat, carbs, sodium
FROM nutrition_recipe
WHERE menu_category = '반찬'
  AND calories BETWEEN 100 AND 300;

-- 메뉴명 FULLTEXT 검색
SELECT * FROM nutrition_recipe
WHERE MATCH(recipe_name) AGAINST('김치찌개' IN NATURAL LANGUAGE MODE);
```

---

## 3. 모듈 2: 비선형 스케일링 엔진 데이터 흐름

### 3.1 Phase 1 — 데이터 수집 및 정규화

#### 3.1.1 소규모 레시피 수집 → 적재

```
식품안전나라 1,900건 + 추가 수집
  │
  ▼
recipe 테이블 INSERT
  ├─ serving_category = '소규모'
  ├─ serving_size = 1 (또는 4)
  ├─ primary_method_id → cooking_method 참조
  └─ data_source = '식약처API'
  │
  ▼
recipe_ingredient_map INSERT (재료별)
  ├─ amount, unit (원본)
  ├─ original_text (비정형 텍스트 보존)
  ├─ amount_in_grams (unit_conversion 기반 변환)
  └─ ingredient_role (주재료/부재료/조미료/양념)
```

#### 3.1.2 대규모 레시피 수집 → 적재

```
서적/웹/커뮤니티 (Excel 템플릿 입력)
  │
  ▼
recipe 테이블 INSERT
  ├─ serving_category = '대규모'
  ├─ serving_size = 100, 200, 300 등
  ├─ data_source = '영양사협회' / '산업체' / '커뮤니티'
  └─ is_verified = TRUE/FALSE
  │
  ▼
recipe_ingredient_map INSERT (재료별)
  ├─ amount_in_grams (g 단위 통일)
  └─ per_serving_grams = amount_in_grams / serving_size  ← ⭐ 1인분 환산
```

```sql
-- 대규모 레시피 1인분 환산 업데이트
UPDATE recipe_ingredient_map rim
JOIN recipe r ON rim.recipe_id = r.recipe_id
SET rim.per_serving_grams = rim.amount_in_grams / r.serving_size
WHERE r.serving_category = '대규모';
```

#### 3.1.3 재료명 정규화

```
레시피 원본 재료명 (예: "돼지고기 앞다리살")
  │
  ▼
ingredient_synonym 테이블 FULLTEXT 검색
  │
  ├─ 매칭됨 → ingredient_id 반환 (표준명: "돼지고기(앞다리)")
  │
  └─ 미매칭 → 수동 매핑 후 등록
       INSERT INTO ingredient_synonym
       (ingredient_id, synonym_name, match_type) VALUES (1, '돼지고기 앞다리살', '수동매핑');
```

```sql
-- 정규화 조회 (이형 표기 → 표준 ingredient_id)
SELECT i.ingredient_id, i.ingredient_name, i.category, syn.synonym_name
FROM ingredient_synonym syn
JOIN ingredient i ON syn.ingredient_id = i.ingredient_id
WHERE syn.synonym_name = '돼지고기 앞다리살';

-- 정규화 적용 현황
SELECT
    COUNT(*) AS total_ingredients,
    SUM(CASE WHEN rim.ingredient_id IS NOT NULL THEN 1 ELSE 0 END) AS matched,
    SUM(CASE WHEN rim.ingredient_id IS NULL THEN 1 ELSE 0 END) AS unmatched
FROM recipe_ingredient_map rim;
```

---

### 3.2 Phase 2 — 다층 유사도 매칭

```
소규모 레시피 (N건)  ×  대규모 레시피 (M건)
  │
  ▼ [1단계] group_type 필터링 [ADR-002 v5]
  │
  ├─ 동일 group_type (건열/습열/비가열) → 후보군 유지
  └─ 불일치 → 제외
  │
  ▼ [2단계] 재료 binary 벡터 Cosine Similarity [ADR-002 v5]
  │
  │  정규화된 재료 집합을 binary 벡터로 표현
  │  cosine_similarity_score = cos(소규모벡터, 대규모벡터)
  │  (기록 상세도 불균형에 강건 — 가중 Jaccard 대체)
  │
  ▼ [3단계] 복합 유사도 [ADR-002 v5]
  │
  │  menu_name_similarity = difflib.SequenceMatcher 기반 부분 일치
  │  composite_score = 0.4 × menu_name_similarity + 0.6 × cosine
  │
  ▼ 결과 저장
  │
  recipe_similarity 테이블 INSERT
  ├─ composite_score ≥ 0.7 → match_decision = '자동태깅'
  ├─ 0.6 ~ 0.7 → match_decision = '수동검토'
  └─ < 0.6 → match_decision = '제외'
  │
  ▼ 확정된 쌍에 recipe_group_id 부여
  │
  UPDATE recipe SET recipe_group_id = ?, base_recipe_id = ?
  WHERE recipe_id IN (small_id, large_id);
```

> **[ADR-002 v5 변경 사유]** 가중 Jaccard(임계값 ≥0.5)는 소규모(평균 10~18종) vs 대규모(평균 5~13종)
> 이종 DB 기록 상세도 불균형으로 체계적 편향 실증. 전수 스캔(583건) 복합점수 최댓값 0.57 —
> 임계값 조정으로 FR-03 기준(≥0.7) 달성 불가 확인. (2026-03-14)
> **재현 스크립트**: matching_optionB.py (Python 3.12, pandas 2.x, numpy 1.x, scikit-learn 1.x, difflib)

```sql
-- 매칭 현황 조회
SELECT * FROM v_similarity_summary;

-- 확정된 레시피 쌍 조회
SELECT * FROM v_recipe_pairs WHERE similarity_score >= 0.7;

-- 조리방법별 매칭 쌍 수
SELECT cm.method_name, COUNT(*) AS pair_count
FROM v_recipe_pairs vp
JOIN cooking_method cm ON vp.primary_method_id = cm.method_id
GROUP BY cm.method_name;
```

---

### 3.3 Phase 3 — 통계 분석 기반 스케일링 지수 도출

```
매칭된 레시피 쌍 (≥ 150건)
  │
  ▼ ml_training_dataset 테이블 구축
  │
  │  INSERT INTO ml_training_dataset
  │  (base_recipe_id, target_recipe_id, ingredient_id,
  │   base_amount_g, target_amount_g, primary_method_id, ...)
  │  SELECT ... FROM v_recipe_pairs JOIN recipe_ingredient_map ...
  │
  ▼ [EDA] pandas DataFrame → 시각화 [ADR-001]
  │
  │  종속변수: ratio = target_amount_g / (base_amount_g × N)  (1에 가까울수록 선형)
  │  3대분류 × 재료 카테고리별 ratio 분포 시각화 (boxplot)
  │  이상치 탐지: IQR × 1.5 기준, ratio 이상치 기준 명시적 정의
  │  Baseline: ratio = 1 (b=1 동치, 선형 기준선)
  │
  ▼ [MixedLM — 주 전략] statsmodels.MixedLM [ADR-002 v2]
  │
  │  설계식: log(ratio) = log(a) + (b-1)×log(N) + β₁×Category + u_method
  │  고정효과: log(N), Category (재료 카테고리 5종)
  │  랜덤효과: u_method ~ N(0, σ²)
  │    ← 그룹 구조: EDA(3/25) 후 ADR-002 v2 결정 결과 적용
  │    ← 셀당 n ≥ 10: 독립 그룹 분리 (볶기, 끓이기 등)
  │    ← 셀당 n < 10: 3대분류(건열/습열/비가열) 병합 유지
  │    ← 그룹 수 k < 5 시 논문에 랜덤효과 분산 추정 한계 명시 의무
  │  수렴 실패 시: REML→ML 전환 시도
  │
  │  # [ADR-002 v2] 적응형 그룹 전략 적용 코드
  │  method_counts = df.groupby('cooking_method')['pair_id'].count()
  │  group_map = {}
  │  for method, count in method_counts.items():
  │      if count / 5 >= 10:  # 셀당 n ≥ 10 기준 (사전 선언)
  │          group_map[method] = method        # 독립 그룹
  │      else:
  │          group_map[method] = df.loc[df['cooking_method']==method, 'group_type'].iloc[0]  # 3대분류 병합
  │  df['mixedlm_group'] = df['cooking_method'].map(group_map)
  │
  │  import statsmodels.formula.api as smf
  │  model = smf.mixedlm("log_ratio ~ log_N + C(category)",
  │                       data=df, groups=df["mixedlm_group"])
  │  result = model.fit(reml=True)
  │
  ▼ [curve_fit — 보조, N≥10 셀만] scipy.optimize.curve_fit [ADR-002]
  │
  │  모델: ratio = a × N^(b-1)
  │  셀당 N < 10인 경우 MixedLM 결과 사용, curve_fit 실행 안 함
  │
  │  from scipy.optimize import curve_fit
  │  def ratio_model(N, a, b): return a * np.power(N, b - 1)
  │  popt, pcov = curve_fit(ratio_model, N_arr, ratio_arr)  # popt = [a, b]
  │
  ▼ [교차 검증] GroupKFold (5-Fold) [ADR-002 v2]
  │
  │  # 분할 단위: base_recipe_id (레시피 쌍 기준, data leakage 방지)
  │  # 동일 메뉴의 소규모-대규모 쌍이 train/test에 동시 포함되지 않도록 처리
  │  # 일반 KFold 사용 금지
  │  from sklearn.model_selection import GroupKFold
  │  gkf = GroupKFold(n_splits=5)
  │  for train_idx, test_idx in gkf.split(X, y, groups=df['base_recipe_id']):
  │      # MixedLM 지수 안정성 확인 (폴드별 b 분포 기록)
  │      ...
  │  선형(ratio=1, b=1) vs 멱함수: AIC/BIC 비교
  │
  ▼ 결과 저장
  │
  INSERT INTO scaling_coefficient
  (ingredient_category, group_type, power_law_a, power_law_b,
   r_squared, p_value, confidence_interval_lower, confidence_interval_upper,
   sample_size, estimation_method, version, source, is_active)
  VALUES ('양념류', 'moist_heat', 1.0200, 0.650, 0.920, 0.001, 0.580, 0.720, 45, 'mixedlm', 1, '통계분석', TRUE);
  -- estimation_method: 'mixedlm'(주) / 'curve_fit'(N≥10 셀) / 'category_mean'(fallback)
```

```sql
-- 활성 가중치 조회 (Rule-based 엔진의 룩업 테이블)
SELECT * FROM v_scaling_lookup;

-- 카테고리별 스케일링 지수 요약
SELECT
    ingredient_category,
    AVG(power_law_b) AS avg_b,
    MIN(power_law_b) AS min_b,
    MAX(power_law_b) AS max_b,
    AVG(r_squared) AS avg_r2
FROM scaling_coefficient
WHERE is_active = TRUE AND source = '통계분석'
GROUP BY ingredient_category;
```

---

### 3.4 Phase 4 — Rule-based + ML 하이브리드 스케일링

#### 3.4.1 Rule-based 엔진 데이터 흐름

```
[입력] recipe_id=101, target_serving=100
  │
  ▼ Step 1: 레시피 정보 조회
  │
  SELECT r.primary_method_id, r.secondary_method_id, r.serving_size
  FROM recipe r WHERE r.recipe_id = 101;
  │
  ▼ Step 2: 재료 목록 조회
  │
  SELECT rim.ingredient_id, rim.amount_in_grams, rim.ingredient_role,
         i.category, i.subcategory
  FROM recipe_ingredient_map rim
  JOIN ingredient i ON rim.ingredient_id = i.ingredient_id
  WHERE rim.recipe_id = 101;
  │
  ▼ Step 3: 재료별 스케일링 파라미터 조회 [ADR-001·002]
  │
  SELECT sc.power_law_a, sc.power_law_b, sc.estimation_method
  FROM scaling_coefficient sc
  JOIN cooking_method cm ON cm.method_id = <primary_method_id>
  WHERE sc.ingredient_category = '양념류'        -- 재료 카테고리
    AND sc.group_type = cm.group_type           -- 3대분류 기준 룩업 [ADR-002]
    AND sc.is_active = TRUE;
  │
  ├─ [조회 성공] → 해당 파라미터 사용 (신뢰도: high, estimation_method 기록)
  │
  ├─ [미등록 조합] Fallback 1: 동일 카테고리 평균 (estimation_method='category_mean')
  │    SELECT AVG(power_law_a), AVG(power_law_b)
  │    FROM scaling_coefficient
  │    WHERE ingredient_category = '양념류' AND is_active = TRUE;
  │    (신뢰도: medium)
  │
  └─ [최종] Fallback 2: 전체 평균 (estimation_method='category_mean')
       SELECT AVG(power_law_a), AVG(power_law_b)
       FROM scaling_coefficient WHERE is_active = TRUE;
       (신뢰도: low)
  │
  ▼ Step 4: 대규모 투입량 역변환 계산 [ADR-001 Option A]
  │
  │  역변환 수식: Y = power_law_a × base_amount_g × target_serving^power_law_b
  │
  ▼ [출력] 대규모 레시피 (재료별 Y값 + 신뢰도 플래그)
```

#### 3.4.2 ML 보조 모듈 데이터 흐름

```
[ML 분류기] 미등록 재료 자동 분류
  │
  새 재료 텍스트 → TF-IDF 벡터화 → 로지스틱 회귀 → 예측 카테고리
  │
  └─ 학습 데이터:
     SELECT i.ingredient_name, i.category
     FROM ingredient i
     JOIN recipe_ingredient_map rim ON i.ingredient_id = rim.ingredient_id;
     -- ※ 학습 데이터 규모: 레시피 건수가 아닌 재료 행 수 기준으로 기록할 것
  │
  └─ 평가 지표 [ADR-002 v2]:
     - Macro-F1 (주지표 — 클래스 불균형 대응)
     - Per-class Precision / Recall (카테고리별 오분류 패턴)
     - Confusion Matrix
     - 민감도 분석: 주재료↔부재료 오분류가 스케일링 지수 조회에 미치는 영향

[ML 예측기] 미등록 조합 지수 예측 (Fallback)
  │
  (category, method_id, moisture, target_serving) → RF/XGBoost → 예측 b
  │
  └─ 학습 데이터:
     SELECT ingredient_category, group_type,        -- group_type Feature [ADR-002]
            base_amount_g, target_serving_size,
            ratio AS actual_ratio                    -- ratio = target / (base×N) [ADR-001]
     FROM ml_training_dataset
     WHERE data_quality = '검증완료';
```

#### 3.4.3 하이브리드 파이프라인 통합

```
[입력] 소규모 레시피 + 목표 인원수
  │
  ▼ 재료 카테고리 확인
  ├─ ingredient 테이블에 등록됨 → category 사용
  └─ 미등록 → ML 분류기(FR-06) → 예측 카테고리
  │
  ▼ Rule-based 엔진(FR-05) 실행
  ├─ 룩업 성공 → 결과 출력
  └─ 룩업 실패 → ML 예측기(FR-07) fallback
  │
  ▼ [출력] 대규모 레시피 + 각 재료의 신뢰도 플래그
```

---

### 3.5 Phase 5 — 영양사 피드백 루프

```
[하이브리드 엔진] → 대규모 레시피 생성 (10개/회차)
  │
  ▼ 영양사 평가
  │
  INSERT INTO nutritionist_feedback
  (recipe_id, feedback_round, overall_rating, ingredient_feedback, feedback_date)
  VALUES (201, 1, '수정필요',
    '{"고추장": "-100g", "물": "+500ml", "소금": "적정"}', '2026-05-04');
  │
  ▼ 피드백 분석 → 파라미터 업데이트
  │
  │  영양사가 "고추장 -100g" → 양념류 b를 약간 낮춤
  │  기존 b=0.65 → 피드백 반영 후 b=0.62 (가중 평균, ADR-003 수식)
  │
  │  [ADR-002 v3] 피드백 적용 대상 셀 결정:
  │  IF 해당 cooking_method_id(볶음=2)의 nutritionist_feedback b값 이미 존재
  │      → (cooking_method_id=2 × ingredient_category='양념류') 셀 업데이트
  │  ELSE (최초 피드백)
  │      → 3대분류(group_type='dry_heat' × ingredient_category='양념류') 통계 b값 기반 신규 행 생성
  │
  -- 세부 조리방법(볶음) 피드백 보정 b값 적재 [ADR-002 v3]
  INSERT INTO scaling_coefficient
  (ingredient_category, cooking_method_id, group_type,
   power_law_a, power_law_b,
   estimation_method,          -- ← 'nutritionist_feedback' 필수 (통계 도출값과 구분)
   version, source, parent_coefficient_id, is_active)
  VALUES ('양념류', 2, 'dry_heat', 1.02, 0.620,
          'nutritionist_feedback',
          2, '영양사피드백', 이전_coefficient_id, TRUE);
  │
  │  이전 버전 비활성화:
  UPDATE scaling_coefficient SET is_active = FALSE WHERE coefficient_id = 이전_coefficient_id;
  │
  ▼ 반복 (총 5회차) [ADR-002 v3 세부 조리방법 단위]
  │
  │  Round 1 (5/4):  볶음 10개           → 볶음 양념류·수분류 b 보정 → version 2
  │  Round 2 (5/8):  구이·부침 + 끓이기  → 세부 조리방법별 b 보정   → version 3
  │  Round 3 (5/12): 찜·조림 + 비가열    → 세부 조리방법별 b 보정   → version 4
  │  Round 4 (5/15): 혼합 조리법          → 전체 조정                → version 5
  │  Round 5 (5/19): 전체 재검증          → 최종 파라미터 확정       → version final
  │
  ▼ 피드백 반영 기록
  │
  UPDATE nutritionist_feedback
  SET is_applied = TRUE, applied_date = CURDATE(),
      applied_coefficient_id = 새_coefficient_id
  WHERE feedback_id = ?;
```

```sql
-- 피드백 통계 (회차별 상용가능 비율)
SELECT * FROM v_feedback_stats;

-- [ADR-002 v3] 통계 도출 b vs 피드백 보정 b 비교 조회
SELECT
    ingredient_category,
    group_type,
    cm.method_name AS cooking_method,
    estimation_method,
    power_law_b,
    version,
    source
FROM scaling_coefficient sc
LEFT JOIN cooking_method cm ON sc.cooking_method_id = cm.method_id
WHERE sc.is_active = TRUE
  AND sc.ingredient_category = '양념류'
ORDER BY ingredient_category,
    CASE estimation_method
        WHEN 'nutritionist_feedback' THEN 1  -- 세부 조리방법 피드백 보정값 (1순위)
        WHEN 'mixedlm' THEN 2               -- 3대분류 통계 도출값 (2순위)
        WHEN 'curve_fit' THEN 3
        ELSE 4
    END;

-- 가중치 버전 히스토리 (양념류 볶음)
SELECT coefficient_id, version, power_law_b, source, test_error_rate, created_at
FROM scaling_coefficient
WHERE ingredient_category = '양념류' AND cooking_method_id = 2
ORDER BY version;
```

---

### 3.6 Phase 6 — 최종 검증 (3종 비교)

```
검증 데이터셋 (is_training_set = FALSE)
  │
  ▼ 3종 모델 실행
  │
  ├─ [1] 선형: predicted = base_amount_g × (target_serving / base_serving)
  ├─ [2] Rule-based: predicted = a × (base × ratio)^b  (룩업 테이블)
  └─ [3] 하이브리드: Rule-based + ML fallback
  │
  ▼ 성능 측정
  │
  INSERT INTO scaling_analysis
  (base_recipe_id, target_recipe_id, scaling_factor, scaling_method,
   mae, mape, r_squared, analysis_date)
  VALUES
  (1, 3, 100, '선형',       312.50, 25.300, 0.720, '2026-05-25'),
  (1, 3, 100, 'Rule-based', 128.30, 10.500, 0.910, '2026-05-25'),
  (1, 3, 100, '하이브리드',  87.60,  7.200, 0.945, '2026-05-25');
  │
  ▼ 통계적 유의성 검증
  │
  │  paired t-test: 선형 vs 멱함수
  │  paired t-test: Rule-based vs 하이브리드
  │  p < 0.05 확인
```

```sql
-- 3종 비교 결과 조회
SELECT scaling_method,
       AVG(mae) AS avg_mae,
       AVG(mape) AS avg_mape,
       AVG(r_squared) AS avg_r2
FROM scaling_analysis
WHERE analysis_date = '2026-05-25'
GROUP BY scaling_method
ORDER BY avg_mape;
```

---

## 4. 모듈 3: CSP Solver 데이터 흐름

```
[입력] group_id=3 (중학생), 급식일수=7일, 끼니=점심
  │
  ▼ Step 1: 영양 기준 조회
  │
  SELECT daily_calories, daily_protein_g, daily_sodium_mg
  FROM user_group WHERE group_id = 3;
  -- 결과: 2400kcal, 60g protein, 2000mg sodium
  │
  ▼ Step 2: 끼니별 배분
  │
  │  점심 = 40% → 960kcal, 24g protein, 800mg sodium
  │
  ▼ Step 3: 제약 조건 조회
  │
  SELECT c.constraint_type, i.ingredient_name, c.severity
  FROM constraints c
  JOIN ingredient i ON c.ingredient_id = i.ingredient_id
  WHERE c.group_id = 3 OR c.group_id IS NULL;
  │
  ▼ Step 4: 메뉴 후보 조회
  │
  SELECT nutrition_id, recipe_name, calories, protein, sodium, menu_category
  FROM nutrition_recipe
  WHERE menu_category IN ('주식', '국', '반찬');
  │
  ▼ Step 5: CSP Solver (OR-Tools)
  │
  │  변수: 각 메뉴의 선택 여부 (Boolean)
  │
  │  Hard Constraints:
  │  - 하루 칼로리 합 = 960 ± 96 (±10%)
  │  - 알레르기 식재료 포함 메뉴 제외
  │  - 끼니당 구성: 주식 1 + 국 1 + 반찬 2~3
  │
  │  Soft Constraints:
  │  - 3일 이내 동일 메뉴 재등장 금지
  │  - 단백질/지방/탄수화물 비율 권장 범위
  │
  │  Scoring: Total = 0.5×영양 + 0.3×선호 + 0.2×단가
  │
  ▼ Step 6: 결과 조합
  │
  │  7일 × (주식1 + 국1 + 반찬2~3) = 약 21~28개 메뉴
  │
  ▼ Step 7: 스케일링 연동 (모듈 2)
  │
  │  선택된 메뉴 → recipe 테이블 매칭 → 하이브리드 엔진으로 목표 인원수 변환
  │
  ▼ [출력] 7일 식단표
  │
  │  {
  │    "day_1": {
  │      "lunch": {
  │        "rice": {"name": "잡곡밥", "calories": 320, ...},
  │        "soup": {"name": "미역국", "calories": 85, ...},
  │        "side_1": {"name": "제육볶음", "calories": 380, ...},
  │        "side_2": {"name": "시금치나물", "calories": 45, ...}
  │      },
  │      "total_calories": 830,
  │      "total_protein": 28.5
  │    },
  │    ...
  │  }
```

---

## 5. 모듈 4: API + Frontend 데이터 흐름

### 5.1 REST API 엔드포인트별 데이터 흐름

```
[GET /api/nutrition/search?q=김치찌개]
  │
  └─ nutrition_recipe FULLTEXT 검색 → JSON 응답

[POST /api/scaling/predict]
  │
  Body: { "recipe_id": 101, "target_serving": 100 }
  │
  └─ 하이브리드 파이프라인 실행 (Phase 4)
     ├─ ingredient_synonym 조회
     ├─ recipe_ingredient_map 조회
     ├─ v_scaling_lookup 조회
     ├─ 멱함수 계산
     └─ JSON 응답: { "ingredients": [...], "confidence": "high" }

[POST /api/menu/generate]
  │
  Body: { "group_id": 3, "days": 7, "meals": ["lunch"] }
  │
  └─ CSP Solver 실행 (모듈 3)
     ├─ user_group 조회
     ├─ constraints 조회
     ├─ nutrition_recipe 후보 조회
     ├─ OR-Tools 최적화
     └─ JSON 응답: { "meal_plan": [...] }
```

### 5.2 Frontend 페이지별 API 호출

```
[홈]
  └─ 정적 페이지 (API 호출 없음)

[영양성분 검색]
  └─ GET /api/nutrition/search?q={query}
     → 결과 리스트 렌더링

[레시피 스케일링]
  ├─ GET /api/recipes (레시피 목록)
  └─ POST /api/scaling/predict (스케일링 실행)
     → 재료별 투입량 테이블 렌더링

[식단 생성]
  ├─ GET /api/groups (타겟 그룹 목록)
  └─ POST /api/menu/generate (식단 생성 실행)
     → 캘린더 형태 식단표 렌더링

[식단표 상세]
  └─ GET /api/menu/{id}
     → 일별 메뉴 + 영양소 합산
     → PDF 다운로드 버튼
```

---

## 6. 테이블 간 주요 JOIN 패턴

### 6.1 레시피 + 재료 + 정규화 사전

```sql
-- 특정 레시피의 정규화된 재료 목록
SELECT
    r.recipe_name,
    i.ingredient_name AS 표준명,
    rim.original_text AS 원본텍스트,
    rim.amount_in_grams,
    rim.per_serving_grams,
    rim.ingredient_role,
    i.category,
    icb.behavior_name
FROM recipe r
JOIN recipe_ingredient_map rim ON r.recipe_id = rim.recipe_id
JOIN ingredient i ON rim.ingredient_id = i.ingredient_id
LEFT JOIN ingredient_cooking_behavior icb ON i.behavior_id = icb.behavior_id
WHERE r.recipe_id = 101;
```

### 6.2 레시피 쌍 + 스케일링 지수

```sql
-- 매칭된 쌍의 스케일링 지수 조회
SELECT
    vp.small_recipe_name,
    vp.large_recipe_name,
    vp.scaling_factor,
    i.ingredient_name,
    i.category,
    sc.power_law_a,
    sc.power_law_b,
    sc.r_squared
FROM v_recipe_pairs vp
JOIN recipe_ingredient_map rim ON vp.small_recipe_id = rim.recipe_id
JOIN ingredient i ON rim.ingredient_id = i.ingredient_id
LEFT JOIN scaling_coefficient sc
    ON sc.ingredient_category = i.category
    AND sc.cooking_method_id = vp.primary_method_id
    AND sc.is_active = TRUE
WHERE vp.recipe_group_id = 1;
```

### 6.3 영양사 피드백 + 가중치 변경 이력

```sql
-- 특정 레시피에 대한 피드백 이력 + 반영된 가중치 변화
SELECT
    nf.feedback_round,
    nf.overall_rating,
    nf.ingredient_feedback,
    nf.feedback_date,
    sc.power_law_b AS 반영된_지수,
    sc.version AS 가중치_버전,
    sc_parent.power_law_b AS 이전_지수
FROM nutritionist_feedback nf
LEFT JOIN scaling_coefficient sc ON nf.applied_coefficient_id = sc.coefficient_id
LEFT JOIN scaling_coefficient sc_parent ON sc.parent_coefficient_id = sc_parent.coefficient_id
WHERE nf.recipe_id = 201
ORDER BY nf.feedback_round;
```

---

## 7. 데이터 볼륨 예상치

| 테이블 | 예상 레코드 수 | 증가 패턴 |
|--------|---------------|-----------|
| cooking_method | 8~12 | 고정 |
| ingredient_cooking_behavior | 4~6 | 고정 |
| ingredient | 500~1,000 | 점진 증가 |
| ingredient_synonym | 1,000~3,000 | 점진 증가 |
| nutrition_recipe | 20,000~50,000 | 초기 대량 적재 후 고정 |
| recipe | 4,500~5,000 | 초기 대량 적재 후 고정 |
| recipe_ingredient_map | 30,000~50,000 | recipe에 비례 |
| unit_conversion | 50~100 | 점진 증가 |
| recipe_similarity | 10,000~50,000 | 매칭 시 일괄 생성 |
| scaling_coefficient | 50~200 | 피드백마다 버전 추가 |
| ml_training_dataset | 5,000~15,000 | 매칭 완료 시 일괄 생성 |
| scaling_analysis | 50~200 | 검증 시 생성 |
| nutritionist_feedback | 50~100 | 5회차 × 10~20개 |
| user_group | 8~15 | 고정 |
| constraints | 20~50 | 점진 증가 |
