-- ============================================================================
-- scaling_coefficient 시드 데이터 (B_simple MixedLM 채택 모델 v1)
-- ============================================================================
-- 원본: module_2/src/engine/scaling_coefficients.csv
-- 생성: module_2/otherstest/MixedLM .../generate_scaling_coefficient.py 기반
--
-- [채택 모델: B_simple MixedLM — 2026-03-20 확정]
--   b = 0.6163  (SE=0.1814, 95%CI [0.261, 0.972], AIC=1262.87, n=607)
--   역변환: Y = a × base_amount × N^b
--
-- [INSERT 구성]
--   id 1~ 9: 참조용 행 (영어 ingredient_category 또는 group_type 단독)
--              엔진 룩업 대상 아님 (v_scaling_lookup 뷰에서 한국어 5분류만 필터)
--   id 10~24: 엔진 룩업 실제 대상
--              group_type(3종) × ingredient_category 한국어 5분류 = 15행
--
-- 주의: MixedLM b=0.6163은 이 파일에서만 적재 — 코드 하드코딩 금지 (ADR-003)
-- ============================================================================

-- 기존 데이터 초기화 (재실행 안전성)
TRUNCATE TABLE scaling_coefficient RESTART IDENTITY CASCADE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 참조용 행 (id 1~9): 엔진 룩업 제외 (ingredient_category 영어 또는 group_type 단독)
-- ─────────────────────────────────────────────────────────────────────────────

-- id 1: B_simple 전체 — 주재료(main) 기준 참조용
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    (NULL, 'main', 7.9677, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. group_type 무관 적용. log(a)=intercept+0. 참조용(엔진 룩업은 id 10~24 사용)',
     TRUE);

-- id 2: B_simple — 양념류(seasoning) 기준 참조용
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    (NULL, 'seasoning', 6.3028, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. group_type 무관 적용. log(a)=intercept-0.2344. 참조용(엔진 룩업은 id 10~24 사용)',
     TRUE);

-- id 3: B_simple — 부재료(sub) 기준 참조용
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    (NULL, 'sub', 5.7500, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. group_type 무관 적용. log(a)=intercept-0.3262. 참조용(엔진 룩업은 id 10~24 사용)',
     TRUE);

-- id 4: 상호작용항 모델 — main
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    (NULL, 'main', 1.0, 1.1526, 1.1526,
     0.3430, 0.4805, 1.8248,
     607, 'mixedlm_interaction', '통계분석',
     '상호작용항 모델. a=1.0(미보정). 엔진 role별 b 적용 시 사용. CI 좁음(신뢰도 high)',
     TRUE);

-- id 5: 상호작용항 모델 — seasoning
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    (NULL, 'seasoning', 1.0, 0.3483, 0.3483,
     0.5851, -0.7984, 1.4951,
     607, 'mixedlm_interaction', '통계분석',
     '상호작용항 모델. a=1.0(미보정). CI가 0 포함 — 신뢰도 medium',
     TRUE);

-- id 6: 상호작용항 모델 — sub
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    (NULL, 'sub', 1.0, 0.4544, 0.4544,
     0.5605, -0.6442, 1.5530,
     607, 'mixedlm_interaction', '통계분석',
     '상호작용항 모델. a=1.0(미보정). CI가 0 포함 — 신뢰도 medium',
     TRUE);

-- id 7: OLS 서브셋 robustness — dry_heat
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     r_squared, se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('dry_heat', NULL, 1.0, 0.2008, 0.2008,
     0.0547, 0.4172, -0.6221, 1.0237,
     197, 'curve_fit', '통계분석',
     'OLS 서브셋(robustness check). CI가 0 포함. 랜덤효과 미반영 — 참조용',
     TRUE);

-- id 8: OLS 서브셋 robustness — moist_heat
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     r_squared, se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('moist_heat', NULL, 1.0, 0.7715, 0.7715,
     0.0260, 0.2540, 0.2714, 1.2716,
     268, 'curve_fit', '통계분석',
     'OLS 서브셋(robustness check). 랜덤효과 미반영 — 참조용',
     TRUE);

-- id 9: OLS 서브셋 robustness — no_heat
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     r_squared, se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('no_heat', NULL, 1.0, 0.6716, 0.6716,
     0.0438, 0.3593, -0.0388, 1.3820,
     142, 'curve_fit', '통계분석',
     'OLS 서브셋(robustness check). CI가 0 포함. 랜덤효과 미반영 — 참조용',
     TRUE);


-- ─────────────────────────────────────────────────────────────────────────────
-- 엔진 룩업 대상 행 (id 10~24)
-- group_type(3종) × ingredient_category 한국어 5분류 = 15행
-- v_scaling_lookup 뷰에서 이 행들이 실제 조회된다
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 주재료 (a=7.9677, b=0.6163) ─────────────────────────────────────────────
-- id 10: dry_heat × 주재료
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('dry_heat', '주재료', 7.9677, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=주재료 intercept exp(2.0754). b=전체 평균 0.6163.',
     TRUE);

-- id 11: moist_heat × 주재료
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('moist_heat', '주재료', 7.9677, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=주재료 intercept exp(2.0754). b=전체 평균 0.6163.',
     TRUE);

-- id 12: no_heat × 주재료
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('no_heat', '주재료', 7.9677, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=주재료 intercept exp(2.0754). b=전체 평균 0.6163.',
     TRUE);

-- ── 부재료 (a=5.7500, b=0.6163) ─────────────────────────────────────────────
-- id 13: dry_heat × 부재료
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('dry_heat', '부재료', 5.7500, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=부재료(sub) intercept exp(2.0754-0.3262). b=전체 평균 0.6163.',
     TRUE);

-- id 14: moist_heat × 부재료
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('moist_heat', '부재료', 5.7500, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=부재료(sub) intercept exp(2.0754-0.3262). b=전체 평균 0.6163.',
     TRUE);

-- id 15: no_heat × 부재료
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('no_heat', '부재료', 5.7500, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=부재료(sub) intercept exp(2.0754-0.3262). b=전체 평균 0.6163.',
     TRUE);

-- ── 양념류 (a=6.3028, b=0.6163) ─────────────────────────────────────────────
-- id 16: dry_heat × 양념류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('dry_heat', '양념류', 6.3028, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=양념류(seasoning) intercept exp(2.0754-0.2344). b=전체 평균 0.6163.',
     TRUE);

-- id 17: moist_heat × 양념류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('moist_heat', '양념류', 6.3028, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=양념류(seasoning) intercept exp(2.0754-0.2344). b=전체 평균 0.6163.',
     TRUE);

-- id 18: no_heat × 양념류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     sample_size, estimation_method, source, notes, is_active)
VALUES
    ('no_heat', '양념류', 6.3028, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     607, 'mixedlm', '통계분석',
     'B_simple 채택 모델. a=양념류(seasoning) intercept exp(2.0754-0.2344). b=전체 평균 0.6163.',
     TRUE);

-- ── 수분류 (a=5.7500, category_mean fallback) ────────────────────────────────
-- id 19: dry_heat × 수분류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     estimation_method, source, notes, is_active)
VALUES
    ('dry_heat', '수분류', 5.7500, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     'category_mean', '통계분석',
     '부재료(sub) 계열 a값 fallback. 독립 통계 미산출.',
     TRUE);

-- id 20: moist_heat × 수분류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     estimation_method, source, notes, is_active)
VALUES
    ('moist_heat', '수분류', 5.7500, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     'category_mean', '통계분석',
     '부재료(sub) 계열 a값 fallback. 독립 통계 미산출.',
     TRUE);

-- id 21: no_heat × 수분류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     estimation_method, source, notes, is_active)
VALUES
    ('no_heat', '수분류', 5.7500, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     'category_mean', '통계분석',
     '부재료(sub) 계열 a값 fallback. 독립 통계 미산출.',
     TRUE);

-- ── 유지류 (a=6.3028, category_mean fallback) ────────────────────────────────
-- id 22: dry_heat × 유지류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     estimation_method, source, notes, is_active)
VALUES
    ('dry_heat', '유지류', 6.3028, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     'category_mean', '통계분석',
     '양념류(seasoning) 계열 a값 fallback. 독립 통계 미산출.',
     TRUE);

-- id 23: moist_heat × 유지류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     estimation_method, source, notes, is_active)
VALUES
    ('moist_heat', '유지류', 6.3028, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     'category_mean', '통계분석',
     '양념류(seasoning) 계열 a값 fallback. 독립 통계 미산출.',
     TRUE);

-- id 24: no_heat × 유지류
INSERT INTO scaling_coefficient
    (group_type, ingredient_category, power_law_a, power_law_b, scaling_exponent,
     se_b, confidence_interval_lower, confidence_interval_upper,
     estimation_method, source, notes, is_active)
VALUES
    ('no_heat', '유지류', 6.3028, 0.6163, 0.6163,
     0.1814, 0.2607, 0.9720,
     'category_mean', '통계분석',
     '양념류(seasoning) 계열 a값 fallback. 독립 통계 미산출.',
     TRUE);

-- 적재 확인
DO $$
DECLARE
    cnt INT;
BEGIN
    SELECT COUNT(*) INTO cnt FROM scaling_coefficient;
    RAISE NOTICE '[시드] scaling_coefficient 적재 완료: % 행', cnt;
END;
$$;
