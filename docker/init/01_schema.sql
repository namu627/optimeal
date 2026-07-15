-- ============================================================================
-- 비선형 AI 스케일링 모델 — DB 스키마 PostgreSQL 변환판
-- ============================================================================
-- 원본: docs/optimized_schema_v4_3_20260316.sql (MySQL 문법)
-- 변환: PostgreSQL 18.1 호환
-- 변환 내역:
--   - AUTO_INCREMENT → GENERATED ALWAYS AS IDENTITY
--   - ENGINE=InnoDB / CHARSET=utf8mb4 제거 (PostgreSQL UTF8 기본)
--   - ENUM('a','b') → TEXT CHECK (col IN ('a','b'))
--   - TINYINT → SMALLINT
--   - JSON → JSONB
--   - ON UPDATE CURRENT_TIMESTAMP → updated_at 트리거 함수로 대체
--   - GROUP_CONCAT → STRING_AGG
--   - FULLTEXT INDEX → GIN 인덱스 (pg_trgm 확장 사용)
--   - 인라인 COMMENT '...' 제거 (-- 스타일 주석으로 대체)
-- ============================================================================

-- pg_trgm 확장 (FULLTEXT 대체용 GIN 인덱스에 필요)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- updated_at 자동 갱신 트리거 함수
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- [모듈 2] 핵심 테이블
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 조리법 테이블 (cooking_method)
-- ----------------------------------------------------------------------------
CREATE TABLE cooking_method (
    method_id                  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    method_name                VARCHAR(50)    NOT NULL UNIQUE,
    -- 조리 방식 대분류 (구버전 하위호환용)
    method_category            TEXT           NOT NULL CHECK (method_category IN ('습식', '건식', '혼합')),
    -- 3대분류 [ADR-002]: dry_heat / moist_heat / no_heat
    group_type                 VARCHAR(20)    NOT NULL,
    moisture_evaporation_rate  DECIMAL(4,3)   NULL,
    heat_transfer_efficiency   DECIMAL(4,3)   NULL,
    description                TEXT           NULL,
    created_at                 TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
    updated_at                 TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cooking_method_group_type ON cooking_method(group_type);

-- updated_at 트리거
CREATE TRIGGER trg_cooking_method_updated_at
    BEFORE UPDATE ON cooking_method
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- 초기 데이터
INSERT INTO cooking_method
    (method_name, method_category, group_type, moisture_evaporation_rate, heat_transfer_efficiency, description)
VALUES
    ('끓이기', '습식', 'moist_heat', 0.850, 0.800, '국, 찌개류 - 수분 증발 높음'),
    ('볶음',   '건식', 'dry_heat',   0.300, 0.900, '볶음 요리 - 수분 증발 낮음'),
    ('찜',     '습식', 'moist_heat', 0.500, 0.750, '찜 요리 - 중간 수분 증발'),
    ('구이',   '건식', 'dry_heat',   0.200, 0.950, '구이 요리 - 직접 열전달'),
    ('무침',   '건식', 'no_heat',    0.000, 1.000, '무침 - 열 가하지 않음 (비가열)'),
    ('조림',   '혼합', 'moist_heat', 0.600, 0.850, '조림 요리 - 볶음+끓이기 (습열 주도)'),
    ('튀김',   '건식', 'dry_heat',   0.100, 0.980, '튀김 요리'),
    ('삶기',   '습식', 'moist_heat', 0.700, 0.800, '삶기 - 물에 익힘');


-- ----------------------------------------------------------------------------
-- 2. 식재료 조리 거동 특성 테이블 (ingredient_cooking_behavior)
-- ----------------------------------------------------------------------------
CREATE TABLE ingredient_cooking_behavior (
    behavior_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    behavior_name      VARCHAR(50)  NOT NULL UNIQUE,
    behavior_type      TEXT         NOT NULL CHECK (behavior_type IN ('수분배출형', '수분흡수형', '형태유지형', '농도결정형')),
    scaling_coefficient DECIMAL(4,3) NULL,
    description        TEXT         NULL
);

INSERT INTO ingredient_cooking_behavior (behavior_name, behavior_type, scaling_coefficient, description)
VALUES
    ('고수분_배출', '수분배출형', 0.820, '양파, 호박 등 - 대량 조리 시 수분이 예상보다 많이 배출'),
    ('수분_흡수',   '수분흡수형', 0.900, '당면, 버섯 등 - 국물을 흡수하여 예상보다 많이 필요'),
    ('형태_유지',   '형태유지형', 0.880, '고기, 뿌리채소 등 - 비교적 선형에 가까움'),
    ('농도_결정',   '농도결정형', 0.650, '소금, 간장, 설탕 등 - 농도 포화로 인해 강한 비선형성');


-- ----------------------------------------------------------------------------
-- 3. 식재료 마스터 테이블 (ingredient)
-- ----------------------------------------------------------------------------
CREATE TABLE ingredient (
    ingredient_id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingredient_name            VARCHAR(100)  NOT NULL,
    behavior_id                INT           NULL,
    standard_unit              VARCHAR(20)   DEFAULT 'g',
    standard_weight_per_unit   DECIMAL(8,2)  NULL,
    category                   VARCHAR(50)   NULL,
    subcategory                VARCHAR(50)   NULL,
    is_seasoning               BOOLEAN       DEFAULT FALSE,
    moisture_content           TEXT          NULL CHECK (moisture_content IN ('높음', '중간', '낮음')),
    -- 폐기율 (0~1): 발주 시스템에서 정미량→발주량 계산용 [ADR-002]
    waste_rate                 NUMERIC(4,3)  DEFAULT 0.000,
    -- 색감 분류: CSP 색감 Soft Constraint용 [ADR-002]
    color_category             VARCHAR(10)   NULL,
    data_source                VARCHAR(100)  NULL,
    created_at                 TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (behavior_id) REFERENCES ingredient_cooking_behavior(behavior_id)
);

CREATE INDEX idx_ingredient_category    ON ingredient(category);
CREATE INDEX idx_ingredient_subcategory ON ingredient(subcategory);
CREATE INDEX idx_ingredient_seasoning   ON ingredient(is_seasoning);


-- ----------------------------------------------------------------------------
-- 4. 재료명 정규화 사전 (ingredient_synonym)
-- ----------------------------------------------------------------------------
-- 목적: 소규모/대규모 레시피 간 동일 재료의 이형 표기를 통합
-- 예: "돼지고기 앞다리살" = "돼지 앞다리" → ingredient_id=1
-- ----------------------------------------------------------------------------
CREATE TABLE ingredient_synonym (
    synonym_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingredient_id   INT            NOT NULL,
    synonym_name    VARCHAR(200)   NOT NULL,
    match_type      TEXT           DEFAULT '수동매핑' CHECK (match_type IN ('완전일치', '부분일치', '수동매핑')),
    confidence      DECIMAL(3,2)   DEFAULT 1.00,
    created_by      VARCHAR(100)   NULL,
    created_at      TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE (synonym_name)
);

CREATE INDEX idx_synonym_ingredient ON ingredient_synonym(ingredient_id);
-- GIN 인덱스 (FULLTEXT 대체)
CREATE INDEX idx_synonym_gin ON ingredient_synonym USING GIN (synonym_name gin_trgm_ops);


-- ----------------------------------------------------------------------------
-- 5. 영양성분 참조 테이블 (nutrition_recipe) — 모듈 1
-- ----------------------------------------------------------------------------
CREATE TABLE nutrition_recipe (
    nutrition_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipe_name     VARCHAR(200)  NOT NULL,
    serving_size_g  DECIMAL(10,2) NULL,
    calories        DECIMAL(8,2)  NOT NULL,
    protein         DECIMAL(6,2)  NULL,
    fat             DECIMAL(6,2)  NULL,
    carbs           DECIMAL(6,2)  NULL,
    sodium          DECIMAL(8,2)  NULL,
    sugar           DECIMAL(6,2)  NULL,
    cholesterol     DECIMAL(6,2)  NULL,
    saturated_fat   DECIMAL(6,2)  NULL,
    trans_fat       DECIMAL(6,2)  NULL,
    data_source     TEXT          NOT NULL CHECK (data_source IN ('식약처', '식품안전나라')),
    menu_category   TEXT          NULL CHECK (menu_category IN ('주식', '국', '찌개', '반찬', '후식', '음료', '기타')),
    original_data   JSONB         NULL,
    created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_nutrition_name     ON nutrition_recipe(recipe_name);
CREATE INDEX idx_nutrition_category ON nutrition_recipe(menu_category);
CREATE INDEX idx_nutrition_source   ON nutrition_recipe(data_source);
CREATE INDEX idx_nutrition_gin      ON nutrition_recipe USING GIN (recipe_name gin_trgm_ops);

CREATE TRIGGER trg_nutrition_updated_at
    BEFORE UPDATE ON nutrition_recipe
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();


-- ----------------------------------------------------------------------------
-- 6. 레시피 마스터 테이블 (recipe) — 모듈 2
-- ----------------------------------------------------------------------------
CREATE TABLE recipe (
    recipe_id           INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipe_name         VARCHAR(200)  NOT NULL,
    primary_method_id   INT           NOT NULL,
    secondary_method_id INT           NULL,
    serving_size        INT           NOT NULL,
    serving_category    TEXT          NOT NULL CHECK (serving_category IN ('소규모', '중규모', '대규모')),
    cooking_time_minutes INT          NULL,
    difficulty_level    TEXT          NULL CHECK (difficulty_level IN ('쉬움', '보통', '어려움')),
    recipe_group_id     INT           NULL,
    -- 기준 레시피 ID (1인분 기준)
    base_recipe_id      INT           NULL,
    nutrition_recipe_id INT           NULL,
    data_source         TEXT          NOT NULL CHECK (data_source IN ('식약처API', '영양사협회', '산업체', '영양사협조', '커뮤니티')),
    is_verified         BOOLEAN       DEFAULT FALSE,
    verification_date   DATE          NULL,
    description         TEXT          NULL,
    notes               TEXT          NULL,
    created_at          TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (primary_method_id)   REFERENCES cooking_method(method_id),
    FOREIGN KEY (secondary_method_id) REFERENCES cooking_method(method_id),
    FOREIGN KEY (base_recipe_id)      REFERENCES recipe(recipe_id),
    FOREIGN KEY (nutrition_recipe_id) REFERENCES nutrition_recipe(nutrition_id)
);

CREATE INDEX idx_recipe_serving_category ON recipe(serving_category);
CREATE INDEX idx_recipe_group            ON recipe(recipe_group_id);
CREATE INDEX idx_recipe_verified         ON recipe(is_verified);
CREATE INDEX idx_recipe_method           ON recipe(primary_method_id);

CREATE TRIGGER trg_recipe_updated_at
    BEFORE UPDATE ON recipe
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();


-- ----------------------------------------------------------------------------
-- 7. 레시피-식재료 매핑 테이블 (recipe_ingredient_map)
-- ----------------------------------------------------------------------------
CREATE TABLE recipe_ingredient_map (
    map_id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipe_id           INT           NOT NULL,
    ingredient_id       INT           NOT NULL,
    amount              DECIMAL(10,2) NOT NULL,
    unit                VARCHAR(20)   NOT NULL,
    amount_in_grams     DECIMAL(10,2) NULL,
    -- 1인분당 중량(g) — 대규모 레시피 환산용
    per_serving_grams   DECIMAL(10,2) NULL,
    ingredient_role     TEXT          DEFAULT '부재료' CHECK (ingredient_role IN ('주재료', '부재료', '조미료', '양념')),
    cooking_step_order  INT           NULL,
    original_text       VARCHAR(200)  NULL,
    ingredient_type     VARCHAR(20)   NULL
        CHECK (ingredient_type IS NULL OR ingredient_type IN ('DIRECT','COMMERCIAL')),
    created_at          TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (recipe_id)     REFERENCES recipe(recipe_id) ON DELETE CASCADE,
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id)
);

CREATE INDEX idx_map_recipe     ON recipe_ingredient_map(recipe_id);
CREATE INDEX idx_map_ingredient ON recipe_ingredient_map(ingredient_id);
CREATE INDEX idx_map_role       ON recipe_ingredient_map(ingredient_role);
CREATE INDEX idx_map_ingredient_type ON recipe_ingredient_map(ingredient_type);


-- ----------------------------------------------------------------------------
-- 8. 단위 환산 테이블 (unit_conversion)
-- ----------------------------------------------------------------------------
CREATE TABLE unit_conversion (
    conversion_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_unit            VARCHAR(50)    NOT NULL,
    to_unit              VARCHAR(20)    DEFAULT 'g',
    conversion_ratio     DECIMAL(10,4)  NOT NULL,
    ingredient_id        INT            NULL,
    is_standard          BOOLEAN        DEFAULT TRUE,
    reference_source     VARCHAR(200)   NULL,
    -- 발주 최소 단위(g 기준): 발주 목록 자동 생성 시 Ceil 계산용
    minimum_order_unit   DECIMAL(10,2)  NULL,
    created_at           TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE (from_unit, ingredient_id)
);


-- ----------------------------------------------------------------------------
-- 9. 레시피 유사도 테이블 (recipe_similarity)
-- ----------------------------------------------------------------------------
-- 목적: 다층 유사도 알고리즘 결과 저장 (소규모-대규모 매칭)
-- 복합점수 = 0.4×메뉴명유사도 + 0.6×재료Cosine [ADR-002 v5]
-- ----------------------------------------------------------------------------
CREATE TABLE recipe_similarity (
    similarity_id             INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    small_recipe_id           INT           NOT NULL,
    large_recipe_id           INT           NOT NULL,
    -- 메뉴명 유사도 (SequenceMatcher): 0.4 가중치
    menu_name_similarity_score DECIMAL(4,3) NOT NULL,
    -- 재료 binary 벡터 Cosine 유사도: 0.6 가중치
    cosine_similarity_score   DECIMAL(4,3)  NOT NULL,
    composite_score           DECIMAL(4,3)  NOT NULL,
    match_decision            TEXT          NOT NULL CHECK (match_decision IN ('자동태깅', '수동검토', '제외')),
    is_confirmed              BOOLEAN       DEFAULT FALSE,
    confirmed_by              VARCHAR(100)  NULL,
    -- 수동 직접 확인 완료 여부 [ADR-002]: 전체 쌍의 30% 이상 목표
    is_manually_verified      BOOLEAN       DEFAULT FALSE,
    algorithm_version         VARCHAR(20)   DEFAULT 'v1',
    created_at                TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (small_recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY (large_recipe_id) REFERENCES recipe(recipe_id),
    UNIQUE (small_recipe_id, large_recipe_id)
);

CREATE INDEX idx_similarity_composite ON recipe_similarity(composite_score);
CREATE INDEX idx_similarity_decision  ON recipe_similarity(match_decision);
CREATE INDEX idx_similarity_verified  ON recipe_similarity(is_manually_verified);


-- ----------------------------------------------------------------------------
-- 10. 스케일링 분석 테이블 (scaling_analysis)
-- ----------------------------------------------------------------------------
CREATE TABLE scaling_analysis (
    analysis_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    base_recipe_id     INT           NOT NULL,
    target_recipe_id   INT           NOT NULL,
    scaling_factor     DECIMAL(8,2)  NOT NULL,
    scaling_method     TEXT          NOT NULL CHECK (scaling_method IN ('선형', 'Rule-based', 'ML보조', '하이브리드')),
    analysis_date      DATE          NOT NULL,
    overall_error_rate DECIMAL(6,3)  NULL,
    mae                DECIMAL(10,2) NULL,
    mape               DECIMAL(6,3)  NULL,
    r_squared          DECIMAL(5,3)  NULL,
    notes              TEXT          NULL,
    analyzed_by        VARCHAR(100)  NULL,
    created_at         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (base_recipe_id)   REFERENCES recipe(recipe_id),
    FOREIGN KEY (target_recipe_id) REFERENCES recipe(recipe_id)
);

CREATE INDEX idx_analysis_method ON scaling_analysis(scaling_method);
CREATE INDEX idx_analysis_date   ON scaling_analysis(analysis_date);


-- ----------------------------------------------------------------------------
-- 11. ML 학습 데이터셋 테이블 (ml_training_dataset)
-- ----------------------------------------------------------------------------
CREATE TABLE ml_training_dataset (
    dataset_id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    base_recipe_id          INT           NOT NULL,
    target_recipe_id        INT           NOT NULL,
    base_serving_size       INT           NOT NULL,
    target_serving_size     INT           NOT NULL,
    -- 스케일링 배수 (구버전 호환용)
    scaling_ratio           DECIMAL(8,2)  NOT NULL,
    -- ratio = 실제대규모량 / (base×N): ADR-001 Option A 종속변수
    ratio                   DECIMAL(8,4)  NULL,
    ingredient_id           INT           NOT NULL,
    ingredient_category     VARCHAR(50)   NULL,
    ingredient_behavior_id  INT           NULL,
    moisture_content        TEXT          NULL CHECK (moisture_content IN ('높음', '중간', '낮음')),
    is_seasoning            BOOLEAN       NOT NULL,
    ingredient_role         TEXT          NULL CHECK (ingredient_role IN ('주재료', '부재료', '조미료', '양념')),
    base_amount_g           DECIMAL(10,2) NOT NULL,
    target_amount_g         DECIMAL(10,2) NOT NULL,
    primary_method_id       INT           NOT NULL,
    secondary_method_id     INT           NULL,
    -- 3대분류 [ADR-002]: dry_heat / moist_heat / no_heat
    group_type              VARCHAR(20)   NULL,
    -- 조리법 대분류 (구버전 하위호환용)
    cooking_method_category TEXT          NULL CHECK (cooking_method_category IN ('습식', '건식', '혼합')),
    has_mixed_cooking       BOOLEAN       NOT NULL,
    data_quality            TEXT          NOT NULL CHECK (data_quality IN ('검증완료', '미검증')),
    is_training_set         BOOLEAN       DEFAULT TRUE,
    created_at              TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (base_recipe_id)          REFERENCES recipe(recipe_id),
    FOREIGN KEY (target_recipe_id)        REFERENCES recipe(recipe_id),
    FOREIGN KEY (ingredient_id)           REFERENCES ingredient(ingredient_id),
    FOREIGN KEY (ingredient_behavior_id)  REFERENCES ingredient_cooking_behavior(behavior_id),
    FOREIGN KEY (primary_method_id)       REFERENCES cooking_method(method_id),
    FOREIGN KEY (secondary_method_id)     REFERENCES cooking_method(method_id)
);

CREATE INDEX idx_ml_training ON ml_training_dataset(is_training_set);
CREATE INDEX idx_ml_quality  ON ml_training_dataset(data_quality);
CREATE INDEX idx_ml_method   ON ml_training_dataset(primary_method_id);


-- ----------------------------------------------------------------------------
-- 12. 스케일링 가중치 테이블 (scaling_coefficient) ★ 핵심 테이블
-- ----------------------------------------------------------------------------
-- 수식 [ADR-001 Option A]:
--   ratio = power_law_a × N^(power_law_b - 1)
--   역변환: Y = power_law_a × base_amount × N^power_law_b
--
-- estimation_method 허용값 [ADR-002 v3]:
--   'mixedlm'              — MixedLM 통계 도출 (B_simple 채택 모델)
--   'curve_fit'            — OLS 서브셋 (robustness check)
--   'category_mean'        — 카테고리 평균 fallback
--   'nutritionist_feedback'— 영양사 피드백 보정 (5월 이후)
--
-- se_b [ADR-003]:
--   출력 클리핑 ε = 2×se_b 산출 기준 — 코드 하드코딩 금지, DB에서 읽을 것
-- ----------------------------------------------------------------------------
CREATE TABLE scaling_coefficient (
    coefficient_id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 적용 대상 (NULL이면 전체 적용)
    ingredient_id               INT           NULL,
    -- 하위호환용 (group_type 사용 권장)
    cooking_method_id           INT           NULL,
    -- 3대분류 기반 룩업 키 [ADR-002]
    group_type                  VARCHAR(20)   NULL,
    ingredient_behavior_id      INT           NULL,
    ingredient_category         VARCHAR(50)   NULL,

    -- 멱함수 파라미터 [ADR-001 Option A]
    power_law_a                 DECIMAL(8,4)  DEFAULT 1.0000,
    power_law_b                 DECIMAL(8,4)  NOT NULL,
    -- 하위호환 컬럼 (power_law_b와 동일)
    scaling_exponent            DECIMAL(5,3)  NOT NULL,

    -- 통계 검증 결과
    r_squared                   DECIMAL(5,3)  NULL,
    p_value                     DECIMAL(8,6)  NULL,
    confidence_interval_lower   DECIMAL(5,3)  NULL,
    confidence_interval_upper   DECIMAL(5,3)  NULL,
    sample_size                 INT           NULL,
    -- 추정 전략 [ADR-002 v3]
    estimation_method           VARCHAR(30)   NULL,
    -- MixedLM 표준오차 SE(b) [ADR-003]: ε = 2×se_b
    se_b                        DECIMAL(8,6)  NULL,

    -- 버전 관리
    version                     INT           DEFAULT 1,
    source                      TEXT          NOT NULL CHECK (source IN ('통계분석', '영양사피드백', '수동조정')),
    parent_coefficient_id       INT           NULL,
    is_active                   BOOLEAN       DEFAULT TRUE,

    -- 성능 기록
    test_error_rate             DECIMAL(6,3)  NULL,
    test_sample_size            INT           NULL,

    notes                       TEXT          NULL,
    created_by                  VARCHAR(100)  NULL,
    created_at                  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (ingredient_id)          REFERENCES ingredient(ingredient_id),
    FOREIGN KEY (cooking_method_id)      REFERENCES cooking_method(method_id),
    FOREIGN KEY (ingredient_behavior_id) REFERENCES ingredient_cooking_behavior(behavior_id),
    FOREIGN KEY (parent_coefficient_id)  REFERENCES scaling_coefficient(coefficient_id)
);

CREATE INDEX idx_coef_active   ON scaling_coefficient(is_active);
CREATE INDEX idx_coef_version  ON scaling_coefficient(version);
CREATE INDEX idx_coef_source   ON scaling_coefficient(source);
CREATE INDEX idx_coef_category ON scaling_coefficient(ingredient_category);


-- ----------------------------------------------------------------------------
-- 13. 영양사 피드백 테이블 (nutritionist_feedback)
-- ----------------------------------------------------------------------------
-- 클리핑 감사 추적 컬럼 [ADR-003] — 삭제 금지 (학술 재현성 요건)
-- ----------------------------------------------------------------------------
CREATE TABLE nutritionist_feedback (
    feedback_id             INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipe_id               INT           NOT NULL,
    feedback_round          INT           NOT NULL,
    nutritionist_name       VARCHAR(100)  NULL,
    nutritionist_id         VARCHAR(50)   NULL,
    feedback_date           DATE          NOT NULL,
    overall_rating          TEXT          NOT NULL CHECK (overall_rating IN ('상용가능', '수정필요', '부적합')),
    -- 루브릭 점수 (1~5점) [ADR-002]
    rubric_score_taste        SMALLINT    NULL,
    rubric_score_feasibility  SMALLINT    NULL,
    rubric_score_field        SMALLINT    NULL,
    -- 루브릭 3항목 평균 (4.00 이상=상용가능 판정)
    rubric_avg              DECIMAL(3,2)  NULL,
    ingredient_feedback     JSONB         NULL,

    -- [ADR-003] 클리핑 감사 추적 컬럼 (삭제 금지)
    delta_g_original        DECIMAL(10,2) NULL,  -- 1단계 클리핑 전 원본 Δ_g
    delta_g_clipped         DECIMAL(10,2) NULL,  -- 1단계 IQR 클리핑 후 실제 Δ_g
    b_new_raw               DECIMAL(8,4)  NULL,  -- 2단계 클리핑 전 가중 평균 b값
    is_clipped              BOOLEAN       DEFAULT FALSE,

    detailed_comments       TEXT          NULL,
    is_applied              BOOLEAN       DEFAULT FALSE,
    applied_date            DATE          NULL,
    applied_coefficient_id  INT           NULL,
    notes                   TEXT          NULL,
    created_at              TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (recipe_id)               REFERENCES recipe(recipe_id),
    FOREIGN KEY (applied_coefficient_id)  REFERENCES scaling_coefficient(coefficient_id)
);

CREATE INDEX idx_feedback_round   ON nutritionist_feedback(feedback_round);
CREATE INDEX idx_feedback_rating  ON nutritionist_feedback(overall_rating);
CREATE INDEX idx_feedback_applied ON nutritionist_feedback(is_applied);


-- ============================================================================
-- [모듈 3] CSP Solver 테이블
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 14. 타겟 그룹 테이블 (user_group)
-- ----------------------------------------------------------------------------
CREATE TABLE user_group (
    group_id             INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_name           VARCHAR(100)  NOT NULL,
    group_type           TEXT          NOT NULL CHECK (group_type IN ('학생', '군인', '환자', '노인', '일반')),
    daily_calories       INT           NOT NULL,
    daily_protein_g      DECIMAL(6,2)  NULL,
    daily_sodium_mg      DECIMAL(8,2)  NULL,
    daily_fat_g          DECIMAL(6,2)  NULL,
    daily_carbs_g        DECIMAL(6,2)  NULL,
    activity_multiplier  DECIMAL(3,2)  DEFAULT 1.0,
    has_restrictions     BOOLEAN       DEFAULT FALSE,
    description          TEXT          NULL,
    created_at           TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);


-- ----------------------------------------------------------------------------
-- 15. 제약 조건 테이블 (constraints)
-- ----------------------------------------------------------------------------
CREATE TABLE constraints (
    constraint_id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id                   INT   NULL,
    constraint_type            TEXT  NOT NULL CHECK (constraint_type IN ('알레르기', '종교', '질병', '기피', '선호')),
    ingredient_id              INT   NOT NULL,
    severity                   TEXT  DEFAULT '주의' CHECK (severity IN ('절대금지', '제한', '주의', '선호')),
    alternative_ingredient_id  INT   NULL,
    description                TEXT  NULL,
    created_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (group_id)                  REFERENCES user_group(group_id),
    FOREIGN KEY (ingredient_id)             REFERENCES ingredient(ingredient_id),
    FOREIGN KEY (alternative_ingredient_id) REFERENCES ingredient(ingredient_id)
);

CREATE INDEX idx_constraint_group ON constraints(group_id);
CREATE INDEX idx_constraint_type  ON constraints(constraint_type);


-- ----------------------------------------------------------------------------
-- 16. 식재료 시세 테이블 (ingredient_price)
-- ----------------------------------------------------------------------------
-- 목적: CSP Solver 식단가(예산) Hard Constraint 및 원가 계산용
-- ----------------------------------------------------------------------------
CREATE TABLE ingredient_price (
    price_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingredient_id  INT           NOT NULL,
    price_per_g    DECIMAL(10,4) NOT NULL,
    price_date     DATE          NOT NULL,
    source         VARCHAR(100)  DEFAULT '서울시농수산식품공사',
    notes          TEXT          NULL,
    created_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE (ingredient_id, price_date)
);

CREATE INDEX idx_price_ingredient ON ingredient_price(ingredient_id);
CREATE INDEX idx_price_date       ON ingredient_price(price_date);


-- ----------------------------------------------------------------------------
-- 17. 제철 식재료 테이블 (seasonal_ingredient)
-- ----------------------------------------------------------------------------
-- 목적: CSP Solver 제철 식재료 Soft Constraint용
-- ----------------------------------------------------------------------------
CREATE TABLE seasonal_ingredient (
    seasonal_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingredient_id INT           NOT NULL,
    month         SMALLINT      NOT NULL CHECK (month BETWEEN 1 AND 12),
    -- 제철 빈도 점수 (0~1): 급식 식단 데이터 기반
    freq_score    DECIMAL(4,3)  NOT NULL,
    -- 제철 피크 여부 (freq_score >= 0.7)
    is_peak_season BOOLEAN      DEFAULT FALSE,
    notes         VARCHAR(200)  NULL,
    created_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE (ingredient_id, month)
);

CREATE INDEX idx_seasonal_month ON seasonal_ingredient(month);
CREATE INDEX idx_seasonal_peak  ON seasonal_ingredient(is_peak_season);


-- ============================================================================
-- 뷰 (View) 정의
-- ============================================================================

-- 레시피 페어 뷰 (확정된 매칭만)
CREATE OR REPLACE VIEW v_recipe_pairs AS
SELECT
    r_small.recipe_id   AS small_recipe_id,
    r_small.recipe_name AS small_recipe_name,
    r_small.serving_size AS small_serving_size,
    r_large.recipe_id   AS large_recipe_id,
    r_large.recipe_name AS large_recipe_name,
    r_large.serving_size AS large_serving_size,
    (r_large.serving_size::FLOAT / r_small.serving_size) AS scaling_factor,
    r_small.recipe_group_id,
    r_small.primary_method_id,
    r_small.secondary_method_id,
    (r_small.secondary_method_id IS NOT NULL) AS has_mixed_cooking,
    rs.composite_score AS similarity_score
FROM recipe r_small
INNER JOIN recipe r_large
    ON r_small.recipe_group_id = r_large.recipe_group_id
LEFT JOIN recipe_similarity rs
    ON rs.small_recipe_id = r_small.recipe_id
    AND rs.large_recipe_id = r_large.recipe_id
WHERE r_small.serving_category = '소규모'
  AND r_large.serving_category = '대규모'
  AND r_small.recipe_group_id IS NOT NULL;


-- 현재 활성 가중치 뷰 (멱함수 파라미터 포함)
CREATE OR REPLACE VIEW v_active_coefficients AS
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
LEFT JOIN ingredient i                   ON sc.ingredient_id        = i.ingredient_id
LEFT JOIN cooking_method cm              ON sc.cooking_method_id    = cm.method_id
LEFT JOIN ingredient_cooking_behavior icb ON sc.ingredient_behavior_id = icb.behavior_id
WHERE sc.is_active = TRUE
ORDER BY sc.ingredient_category, sc.group_type;


-- 영양사 피드백 통계 뷰
CREATE OR REPLACE VIEW v_feedback_stats AS
SELECT
    feedback_round,
    overall_rating,
    COUNT(*)            AS count,
    AVG(rubric_avg)     AS avg_rubric_score,
    SUM(CASE WHEN is_applied THEN 1 ELSE 0 END) AS applied_count
FROM nutritionist_feedback
GROUP BY feedback_round, overall_rating
ORDER BY feedback_round, overall_rating;


-- 조리법 조합 통계 뷰 (MySQL GROUP_CONCAT → PostgreSQL STRING_AGG)
CREATE OR REPLACE VIEW v_cooking_method_combinations AS
SELECT
    cm_primary.method_name   AS primary_method,
    cm_secondary.method_name AS secondary_method,
    COUNT(*)                 AS recipe_count,
    STRING_AGG(DISTINCT r.recipe_name, '; ') AS example_recipes
FROM recipe r
JOIN cooking_method cm_primary   ON r.primary_method_id   = cm_primary.method_id
LEFT JOIN cooking_method cm_secondary ON r.secondary_method_id = cm_secondary.method_id
GROUP BY cm_primary.method_name, cm_secondary.method_name
ORDER BY recipe_count DESC;


-- ============================================================================
-- 스키마 버전 관리 테이블
-- ============================================================================
-- 목적: 현재 적용된 스키마 버전 추적
--       load_recipe_data.py 실행 시 버전 확인 기준
-- 관리 규칙:
--   - 초기 버전: v1 (현재 파일)
--   - 이후 변경은 migrations/v2_변경내용.sql 형식으로 관리
-- ============================================================================
CREATE TABLE schema_version (
    version     VARCHAR(20)  PRIMARY KEY,
    applied_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- 현재 스키마를 v1으로 기록
INSERT INTO schema_version (version, description)
VALUES (
    'v1',
    '초기 스키마: 모듈2 핵심 테이블 (recipe, ingredient, scaling_coefficient 등 17개 테이블)'
);


-- 유사도 매칭 현황 뷰
CREATE OR REPLACE VIEW v_similarity_summary AS
SELECT
    match_decision,
    COUNT(*)              AS pair_count,
    AVG(composite_score)  AS avg_composite_score,
    MIN(composite_score)  AS min_score,
    MAX(composite_score)  AS max_score,
    SUM(CASE WHEN is_confirmed THEN 1 ELSE 0 END) AS confirmed_count
FROM recipe_similarity
GROUP BY match_decision;


-- ============================================================================
-- 스케일링 지수 룩업 뷰 (Rule-based 엔진용) ★ 핵심 뷰
-- ============================================================================
-- 룩업 우선순위 [ADR-002 v3]:
--   1순위: (cooking_method_id × ingredient_category) — nutritionist_feedback
--   2순위: (group_type × ingredient_category)        — mixedlm / curve_fit
--   3순위: ingredient_category 평균                  — category_mean fallback
--
-- 엔진(lookup.py)에서 ORDER BY lookup_priority ASC LIMIT 1 패턴 사용
-- ============================================================================
CREATE OR REPLACE VIEW v_scaling_lookup AS
SELECT
    sc.coefficient_id,
    sc.ingredient_category,
    sc.group_type,
    sc.cooking_method_id,
    cm.method_name  AS cooking_method_name,
    sc.power_law_a,
    sc.power_law_b,
    sc.estimation_method,
    sc.sample_size,
    sc.r_squared,
    sc.p_value,
    -- se_b: ADR-003 클리핑 임계값 ε = 2×se_b 산출 기준
    sc.se_b,
    sc.version,
    sc.source,
    CASE
        WHEN sc.cooking_method_id IS NOT NULL
             AND sc.estimation_method = 'nutritionist_feedback'   THEN 1
        WHEN sc.group_type IS NOT NULL
             AND sc.estimation_method IN ('mixedlm', 'curve_fit') THEN 2
        ELSE 3
    END AS lookup_priority
FROM scaling_coefficient sc
LEFT JOIN cooking_method cm ON sc.cooking_method_id = cm.method_id
WHERE sc.is_active = TRUE
ORDER BY sc.ingredient_category, lookup_priority, sc.group_type;
