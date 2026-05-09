-- Migration: v2_power_law_b_precision
-- power_law_b 컬럼 정밀도 손실 수정
-- NUMERIC(5,3) → NUMERIC(8,4): 소수점 4자리 보존 (0.6163 등)

BEGIN;

-- 의존 뷰 제거
DROP VIEW IF EXISTS v_scaling_lookup;
DROP VIEW IF EXISTS v_active_coefficients;

-- 컬럼 타입 변경
ALTER TABLE scaling_coefficient
    ALTER COLUMN power_law_b TYPE NUMERIC(8,4);

-- v_active_coefficients 재생성
CREATE VIEW v_active_coefficients AS
SELECT
    sc.coefficient_id,
    sc.power_law_a,
    sc.power_law_b,
    sc.scaling_exponent,
    sc.r_squared,
    sc.p_value,
    sc.confidence_interval_lower,
    sc.confidence_interval_upper,
    sc.source,
    sc.version,
    sc.ingredient_category,
    sc.group_type,
    sc.estimation_method,
    sc.sample_size,
    i.ingredient_name,
    cm.method_name AS cooking_method_name,
    icb.behavior_name,
    sc.test_error_rate,
    sc.created_at
FROM scaling_coefficient sc
LEFT JOIN ingredient i ON sc.ingredient_id = i.ingredient_id
LEFT JOIN cooking_method cm ON sc.cooking_method_id = cm.method_id
LEFT JOIN ingredient_cooking_behavior icb ON sc.ingredient_behavior_id = icb.behavior_id
WHERE sc.is_active = true
ORDER BY sc.ingredient_category, sc.group_type;

-- v_scaling_lookup 재생성
CREATE VIEW v_scaling_lookup AS
SELECT
    sc.coefficient_id,
    sc.ingredient_category,
    sc.group_type,
    sc.cooking_method_id,
    cm.method_name AS cooking_method_name,
    sc.power_law_a,
    sc.power_law_b,
    sc.estimation_method,
    sc.sample_size,
    sc.r_squared,
    sc.p_value,
    sc.se_b,
    sc.version,
    sc.source,
    CASE
        WHEN sc.cooking_method_id IS NOT NULL AND sc.estimation_method = 'nutritionist_feedback' THEN 1
        WHEN sc.group_type IS NOT NULL AND sc.estimation_method = ANY (ARRAY['mixedlm', 'curve_fit']) THEN 2
        ELSE 3
    END AS lookup_priority
FROM scaling_coefficient sc
LEFT JOIN cooking_method cm ON sc.cooking_method_id = cm.method_id
WHERE sc.is_active = true
ORDER BY sc.ingredient_category,
    CASE
        WHEN sc.cooking_method_id IS NOT NULL AND sc.estimation_method = 'nutritionist_feedback' THEN 1
        WHEN sc.group_type IS NOT NULL AND sc.estimation_method = ANY (ARRAY['mixedlm', 'curve_fit']) THEN 2
        ELSE 3
    END,
    sc.group_type;

COMMIT;
