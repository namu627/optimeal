-- ============================================================================
-- 비선형 AI 스케일링 모델 - 최적화된 DB 스키마 v4.4
-- ============================================================================
-- 프로젝트: OptiMeal - 대량 조리 레시피 비선형 스케일링 엔진
-- 목적: 소규모(1-4인분) → 대규모(100인분+) 레시피 변환
-- v4 업데이트 (로드맵 v3.1 반영):
--   - ingredient_synonym 테이블 추가 (재료명 정규화 사전)
--   - recipe_similarity 테이블 추가 (다층 유사도 점수 저장)
--   - scaling_coefficient 테이블 개편 (LLM 제거, 멱함수 파라미터 추가)
--   - scaling_analysis 테이블 개편 (LLM 제거, hybrid 추가)
--   - 뷰 추가/수정
-- v4.1 업데이트 (로드맵 v4.0 / ADR-001·002 반영, 2026-02-22):
--   - cooking_method.group_type 컬럼 추가 (3대분류: dry_heat/moist_heat/no_heat) [ADR-002]
--   - scaling_coefficient.estimation_method 컬럼 추가 ('mixedlm'|'curve_fit'|'category_mean') [ADR-002]
--   - recipe_similarity.is_manually_verified 컬럼 추가 (수동 검수 완료 플래그) [ADR-002]
--   - ingredient.waste_rate 컬럼 추가 (폐기율, 발주 시스템용)
--   - ingredient.color_category 컬럼 추가 (색감 다양성 Soft Constraint용)
--   - ingredient_price 테이블 추가 (식재료 시세, CSP 식단가 제약용)
--   - seasonal_ingredient 테이블 추가 (제철 식재료, CSP Soft Constraint용)
--   - unit_conversion.minimum_order_unit 컬럼 추가 (발주 최소 단위)
--   - v_scaling_lookup 뷰: group_type 기준으로 키 변경
-- v4.2 업데이트 (ADR-003 반영, 2026-02-23):
--   - scaling_coefficient.se_b 컬럼 추가 (MixedLM 표준오차, 출력 클리핑 임계값 ε 산출 기준) [ADR-003]
--   - nutritionist_feedback.delta_g_original 컬럼 추가 (클리핑 전 원본 Δ_g, 감사 추적용) [ADR-003]
--   - nutritionist_feedback.delta_g_clipped 컬럼 추가 (클리핑 후 실제 적용 Δ_g) [ADR-003]
--   - nutritionist_feedback.b_new_raw 컬럼 추가 (출력 클리핑 전 업데이트 지수) [ADR-003]
--   - nutritionist_feedback.is_clipped 컬럼 추가 (1·2단계 중 클리핑 발생 여부 플래그) [ADR-003]
-- v4.3 업데이트 (ADR-002 v3 반영, 2026-03-07):
--   - scaling_coefficient.estimation_method 허용값에 'nutritionist_feedback' 추가
--   - v_scaling_lookup 뷰: lookup_priority 컬럼 도입 (세부 조리방법 조회 경로) [ADR-002 v3]
-- v4.4 업데이트 (ADR-004 반영, 2026-07-13):
--   - recipe_ingredient_map.ingredient_type 컬럼 추가 (조달 형태: DIRECT/COMMERCIAL, 발주 시스템용) [ADR-004]
-- ============================================================================


-- ============================================================================
-- [모듈 2] 핵심 테이블 - 비선형 스케일링 엔진
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 조리법 테이블 (cooking_method)
-- ----------------------------------------------------------------------------
CREATE TABLE cooking_method (
    method_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '조리법 고유 ID',
    method_name VARCHAR(50) NOT NULL UNIQUE COMMENT '조리법명 (찜, 탕, 국, 볶음, 구이 등)',
    method_category ENUM('습식', '건식', '혼합') NOT NULL COMMENT '조리 방식 대분류 (구버전, 하위호환용)',
    group_type VARCHAR(20) NOT NULL COMMENT '3대분류 [ADR-002]: dry_heat(건열)/moist_heat(습열)/no_heat(비가열)',
    moisture_evaporation_rate DECIMAL(4,3) NULL COMMENT '수분 증발 계수 (0~1)',
    heat_transfer_efficiency DECIMAL(4,3) NULL COMMENT '열전달 효율 (0~1)',
    description TEXT NULL COMMENT '조리법 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 일시'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='조리법 마스터 테이블';

CREATE INDEX idx_cooking_method_group_type ON cooking_method(group_type);

INSERT INTO cooking_method (method_name, method_category, group_type, moisture_evaporation_rate, heat_transfer_efficiency, description) VALUES
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
    behavior_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '거동 특성 ID',
    behavior_name VARCHAR(50) NOT NULL UNIQUE COMMENT '거동 특성명',
    behavior_type ENUM('수분배출형', '수분흡수형', '형태유지형', '농도결정형') NOT NULL COMMENT '거동 타입',
    scaling_coefficient DECIMAL(4,3) NULL COMMENT '기본 스케일링 계수 (초기 추정값)',
    description TEXT NULL COMMENT '설명'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='식재료 조리 거동 특성';

INSERT INTO ingredient_cooking_behavior (behavior_name, behavior_type, scaling_coefficient, description) VALUES
('고수분_배출', '수분배출형', 0.820, '양파, 호박 등 - 대량 조리 시 수분이 예상보다 많이 배출'),
('수분_흡수', '수분흡수형', 0.900, '당면, 버섯 등 - 국물을 흡수하여 예상보다 많이 필요'),
('형태_유지', '형태유지형', 0.880, '고기, 뿌리채소 등 - 비교적 선형에 가까움'),
('농도_결정', '농도결정형', 0.650, '소금, 간장, 설탕 등 - 농도 포화로 인해 강한 비선형성');


-- ----------------------------------------------------------------------------
-- 3. 식재료 마스터 테이블 (ingredient)
-- ----------------------------------------------------------------------------
CREATE TABLE ingredient (
    ingredient_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '식재료 ID',
    ingredient_name VARCHAR(100) NOT NULL COMMENT '식재료 표준명',
    behavior_id INT NULL COMMENT '조리 거동 특성 ID',
    standard_unit VARCHAR(20) DEFAULT 'g' COMMENT '표준 단위',
    standard_weight_per_unit DECIMAL(8,2) NULL COMMENT '단위당 표준 중량 (예: 양파 1개=200g)',
    category VARCHAR(50) NULL COMMENT '재료 대분류 (육류, 채소, 조미료 등)',
    subcategory VARCHAR(50) NULL COMMENT '재료 소분류 (육류_돼지, 채소_엽경 등)',
    is_seasoning BOOLEAN DEFAULT FALSE COMMENT '조미료 여부',
    moisture_content ENUM('높음', '중간', '낮음') NULL COMMENT '수분 함량',
    waste_rate NUMERIC(4,3) DEFAULT 0.000 COMMENT '폐기율 (0~1): 발주 시스템에서 정미량→발주량 계산용 [ADR-002]',
    color_category VARCHAR(10) NULL COMMENT '색감 분류 (red/orange/yellow/green/white/black 등): CSP 색감 Soft Constraint용 [ADR-002]',
    data_source VARCHAR(100) NULL COMMENT '데이터 출처',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (behavior_id) REFERENCES ingredient_cooking_behavior(behavior_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='식재료 마스터';

CREATE INDEX idx_ingredient_category ON ingredient(category);
CREATE INDEX idx_ingredient_subcategory ON ingredient(subcategory);
CREATE INDEX idx_ingredient_seasoning ON ingredient(is_seasoning);


-- ----------------------------------------------------------------------------
-- 4. 재료명 정규화 사전 (ingredient_synonym) ⭐ v4 신규
-- ----------------------------------------------------------------------------
-- 목적: 소규모/대규모 레시피 간 동일 재료의 이형 표기를 통합
-- 예: "돼지고기 앞다리살" = "돼지 앞다리" = "돼지고기(앞다리)" → ingredient_id=1
-- ----------------------------------------------------------------------------
CREATE TABLE ingredient_synonym (
    synonym_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '동의어 ID',
    ingredient_id INT NOT NULL COMMENT '표준 식재료 ID (ingredient 테이블 참조)',
    synonym_name VARCHAR(200) NOT NULL COMMENT '이형 표기 (원본 텍스트)',
    match_type ENUM('완전일치', '부분일치', '수동매핑') DEFAULT '수동매핑' COMMENT '매칭 방식',
    confidence DECIMAL(3,2) DEFAULT 1.00 COMMENT '매칭 신뢰도 (0~1)',
    created_by VARCHAR(100) NULL COMMENT '등록자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE KEY unique_synonym (synonym_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='재료명 정규화 사전 (이형 표기 → 표준 식재료 매핑)';

CREATE INDEX idx_synonym_ingredient ON ingredient_synonym(ingredient_id);
CREATE FULLTEXT INDEX idx_synonym_fulltext ON ingredient_synonym(synonym_name);


-- ----------------------------------------------------------------------------
-- 5. 영양성분 참조 테이블 (nutrition_recipe) - 모듈 1
-- ----------------------------------------------------------------------------
CREATE TABLE nutrition_recipe (
    nutrition_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '영양성분 레시피 ID',
    recipe_name VARCHAR(200) NOT NULL COMMENT '레시피명',
    serving_size_g DECIMAL(10,2) NULL COMMENT '1회 제공량 (g)',
    calories DECIMAL(8,2) NOT NULL COMMENT '열량 (kcal)',
    protein DECIMAL(6,2) NULL COMMENT '단백질 (g)',
    fat DECIMAL(6,2) NULL COMMENT '지방 (g)',
    carbs DECIMAL(6,2) NULL COMMENT '탄수화물 (g)',
    sodium DECIMAL(8,2) NULL COMMENT '나트륨 (mg)',
    sugar DECIMAL(6,2) NULL COMMENT '당류 (g)',
    cholesterol DECIMAL(6,2) NULL COMMENT '콜레스테롤 (mg)',
    saturated_fat DECIMAL(6,2) NULL COMMENT '포화지방 (g)',
    trans_fat DECIMAL(6,2) NULL COMMENT '트랜스지방 (g)',
    data_source ENUM('식약처', '식품안전나라') NOT NULL COMMENT '데이터 출처',
    menu_category ENUM('주식', '국', '찌개', '반찬', '후식', '음료', '기타') NULL COMMENT 'CSP 식단 생성 시 사용',
    original_data JSON NULL COMMENT '원본 JSON 데이터',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 일시'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='영양성분 참조 DB (CSP Solver용 - 모듈 1)';

CREATE INDEX idx_nutrition_name ON nutrition_recipe(recipe_name);
CREATE INDEX idx_nutrition_category ON nutrition_recipe(menu_category);
CREATE INDEX idx_nutrition_source ON nutrition_recipe(data_source);
CREATE FULLTEXT INDEX idx_nutrition_fulltext ON nutrition_recipe(recipe_name);


-- ----------------------------------------------------------------------------
-- 6. 레시피 마스터 테이블 (recipe) - 모듈 2
-- ----------------------------------------------------------------------------
CREATE TABLE recipe (
    recipe_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '레시피 ID',
    recipe_name VARCHAR(200) NOT NULL COMMENT '레시피명',
    primary_method_id INT NOT NULL COMMENT '주된 조리법 ID',
    secondary_method_id INT NULL COMMENT '보조 조리법 ID',
    serving_size INT NOT NULL COMMENT '기준 인원수',
    serving_category ENUM('소규모', '중규모', '대규모') NOT NULL COMMENT '규모 카테고리',
    cooking_time_minutes INT NULL COMMENT '총 조리 시간 (분)',
    difficulty_level ENUM('쉬움', '보통', '어려움') NULL COMMENT '난이도',
    recipe_group_id INT NULL COMMENT '같은 메뉴 그룹 ID',
    base_recipe_id INT NULL COMMENT '기준 레시피 ID (1인분)',
    nutrition_recipe_id INT NULL COMMENT '영양성분 참조 ID',
    data_source ENUM('식약처API', '영양사협회', '산업체', '영양사협조', '커뮤니티') NOT NULL COMMENT '데이터 출처',
    is_verified BOOLEAN DEFAULT FALSE COMMENT '실측 검증 완료 여부',
    verification_date DATE NULL COMMENT '검증 날짜',
    description TEXT NULL COMMENT '레시피 설명',
    notes TEXT NULL COMMENT '비고',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 일시',
    
    FOREIGN KEY (primary_method_id) REFERENCES cooking_method(method_id),
    FOREIGN KEY (secondary_method_id) REFERENCES cooking_method(method_id),
    FOREIGN KEY (base_recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY (nutrition_recipe_id) REFERENCES nutrition_recipe(nutrition_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='레시피 마스터 (소규모-대규모 통합)';

CREATE INDEX idx_recipe_serving_category ON recipe(serving_category);
CREATE INDEX idx_recipe_group ON recipe(recipe_group_id);
CREATE INDEX idx_recipe_verified ON recipe(is_verified);
CREATE INDEX idx_recipe_method ON recipe(primary_method_id);


-- ----------------------------------------------------------------------------
-- 7. 레시피-식재료 매핑 테이블 (recipe_ingredient_map)
-- ----------------------------------------------------------------------------
CREATE TABLE recipe_ingredient_map (
    map_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '매핑 ID',
    recipe_id INT NOT NULL COMMENT '레시피 ID',
    ingredient_id INT NOT NULL COMMENT '식재료 ID',
    amount DECIMAL(10,2) NOT NULL COMMENT '재료량 (숫자)',
    unit VARCHAR(20) NOT NULL COMMENT '단위 (g, ml, 개, 컵 등)',
    amount_in_grams DECIMAL(10,2) NULL COMMENT '표준화된 중량 (g)',
    per_serving_grams DECIMAL(10,2) NULL COMMENT '1인분당 중량 (g) - 대규모 레시피 환산용',
    ingredient_role ENUM('주재료', '부재료', '조미료', '양념') DEFAULT '부재료' COMMENT '재료 역할',
    cooking_step_order INT NULL COMMENT '투입 순서',
    original_text VARCHAR(200) NULL COMMENT '원본 텍스트',
    ingredient_type VARCHAR(20) NULL COMMENT '조달 형태: DIRECT(직접 계량)/COMMERCIAL(시판 완제품). 발주용, 엔진 참조금지 [ADR-004]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (recipe_id) REFERENCES recipe(recipe_id) ON DELETE CASCADE,
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='레시피-식재료 매핑';

CREATE INDEX idx_map_recipe ON recipe_ingredient_map(recipe_id);
CREATE INDEX idx_map_ingredient ON recipe_ingredient_map(ingredient_id);
CREATE INDEX idx_map_role ON recipe_ingredient_map(ingredient_role);
CREATE INDEX idx_map_ingredient_type ON recipe_ingredient_map(ingredient_type);


-- ----------------------------------------------------------------------------
-- 8. 단위 환산 테이블 (unit_conversion)
-- ----------------------------------------------------------------------------
CREATE TABLE unit_conversion (
    conversion_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '환산 ID',
    from_unit VARCHAR(50) NOT NULL COMMENT '원본 단위',
    to_unit VARCHAR(20) DEFAULT 'g' COMMENT '변환 단위',
    conversion_ratio DECIMAL(10,4) NOT NULL COMMENT '환산 비율',
    ingredient_id INT NULL COMMENT '특정 식재료용 환산 (NULL이면 범용)',
    is_standard BOOLEAN DEFAULT TRUE COMMENT '표준 환산 여부',
    reference_source VARCHAR(200) NULL COMMENT '환산 근거 출처',
    minimum_order_unit DECIMAL(10,2) NULL COMMENT '발주 최소 단위 (g 기준): 발주 목록 자동 생성 시 Ceil 계산용',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE KEY unique_conversion (from_unit, ingredient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='단위 환산 테이블';


-- ----------------------------------------------------------------------------
-- 9. 레시피 유사도 테이블 (recipe_similarity) ⭐ v4 신규
-- ----------------------------------------------------------------------------
-- 목적: 다층 유사도 알고리즘의 결과 저장 (소규모-대규모 매칭)
-- ----------------------------------------------------------------------------
CREATE TABLE recipe_similarity (
    similarity_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '유사도 ID',
    small_recipe_id INT NOT NULL COMMENT '소규모 레시피 ID',
    large_recipe_id INT NOT NULL COMMENT '대규모 레시피 ID',
    
    -- 3단계 다층 유사도 점수 [ADR-002 v5: 가중 Jaccard 제거, 2026-03-14]
    menu_name_similarity_score DECIMAL(4,3) NOT NULL COMMENT '복합점수 구성 요소: 메뉴명 유사도 (SequenceMatcher, difflib) — 0.4 가중치 적용',
    cosine_similarity_score DECIMAL(4,3) NOT NULL COMMENT '복합점수 구성 요소: 재료 binary 벡터 Cosine 유사도 — 0.6 가중치 적용',
    composite_score DECIMAL(4,3) NOT NULL COMMENT '복합 유사도 = 0.4*메뉴명유사도 + 0.6*재료Cosine [ADR-002 v5]',
    
    -- 매칭 결정
    match_decision ENUM('자동태깅', '수동검토', '제외') NOT NULL COMMENT '매칭 결정 (≥0.7/0.6~0.7/<0.6)',
    is_confirmed BOOLEAN DEFAULT FALSE COMMENT '최종 확정 여부 (수동검토 후)',
    confirmed_by VARCHAR(100) NULL COMMENT '확정자',
    is_manually_verified BOOLEAN DEFAULT FALSE COMMENT '수동 직접 확인 완료 여부 [ADR-002]: 전체 쌍의 30% 이상 목표',
    
    -- 메타
    algorithm_version VARCHAR(20) DEFAULT 'v1' COMMENT '유사도 알고리즘 버전',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (small_recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY (large_recipe_id) REFERENCES recipe(recipe_id),
    UNIQUE KEY unique_pair (small_recipe_id, large_recipe_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='레시피 유사도 (다층 유사도 매칭 결과)';

CREATE INDEX idx_similarity_composite ON recipe_similarity(composite_score);
CREATE INDEX idx_similarity_decision ON recipe_similarity(match_decision);
CREATE INDEX idx_similarity_verified ON recipe_similarity(is_manually_verified);


-- ----------------------------------------------------------------------------
-- 10. 스케일링 분석 테이블 (scaling_analysis)
-- ----------------------------------------------------------------------------
CREATE TABLE scaling_analysis (
    analysis_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '분석 ID',
    base_recipe_id INT NOT NULL COMMENT '기준 레시피 (소규모)',
    target_recipe_id INT NOT NULL COMMENT '대상 레시피 (대규모)',
    scaling_factor DECIMAL(8,2) NOT NULL COMMENT '스케일링 배수',
    scaling_method ENUM('선형', 'Rule-based', 'ML보조', '하이브리드') NOT NULL COMMENT '스케일링 방법',
    analysis_date DATE NOT NULL COMMENT '분석 날짜',
    overall_error_rate DECIMAL(6,3) NULL COMMENT '전체 평균 오차율 (%)',
    mae DECIMAL(10,2) NULL COMMENT 'Mean Absolute Error',
    mape DECIMAL(6,3) NULL COMMENT 'Mean Absolute Percentage Error (%)',
    r_squared DECIMAL(5,3) NULL COMMENT 'R² 결정계수',
    notes TEXT NULL COMMENT '분석 비고',
    analyzed_by VARCHAR(100) NULL COMMENT '분석 수행자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (base_recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY (target_recipe_id) REFERENCES recipe(recipe_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='스케일링 방법 비교 분석 (3종 비교: 선형/Rule-based/하이브리드)';

CREATE INDEX idx_analysis_method ON scaling_analysis(scaling_method);
CREATE INDEX idx_analysis_date ON scaling_analysis(analysis_date);


-- ----------------------------------------------------------------------------
-- 11. ML 학습 데이터셋 테이블 (ml_training_dataset)
-- ----------------------------------------------------------------------------
CREATE TABLE ml_training_dataset (
    dataset_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '데이터셋 ID',
    base_recipe_id INT NOT NULL COMMENT '기준 레시피 (소규모)',
    target_recipe_id INT NOT NULL COMMENT '대상 레시피 (대규모)',
    base_serving_size INT NOT NULL COMMENT '기준 인원수 (base)',
    target_serving_size INT NOT NULL COMMENT '목표 인원수 (N)',
    scaling_ratio DECIMAL(8,2) NOT NULL COMMENT '스케일링 배수 (target/base), 구버전 호환용',
    ratio DECIMAL(8,4) NULL COMMENT 'ratio = 실제대규모량 / (base×N): ADR-001 Option A 종속변수 [ADR-001]',
    ingredient_id INT NOT NULL COMMENT '식재료 ID',
    ingredient_category VARCHAR(50) NULL COMMENT '재료 카테고리',
    ingredient_behavior_id INT NULL COMMENT '조리 거동 특성 ID',
    moisture_content ENUM('높음', '중간', '낮음') NULL COMMENT '수분 함량',
    is_seasoning BOOLEAN NOT NULL COMMENT '조미료 여부',
    ingredient_role ENUM('주재료', '부재료', '조미료', '양념') NULL COMMENT '재료 역할',
    base_amount_g DECIMAL(10,2) NOT NULL COMMENT '기준 재료량 (g) - Feature',
    target_amount_g DECIMAL(10,2) NOT NULL COMMENT '실제 대규모 재료량 (g) - Label',
    primary_method_id INT NOT NULL COMMENT '주된 조리법 ID',
    secondary_method_id INT NULL COMMENT '보조 조리법 ID',
    group_type VARCHAR(20) NULL COMMENT '3대분류 [ADR-002]: dry_heat/moist_heat/no_heat (cooking_method.group_type 참조)',
    cooking_method_category ENUM('습식', '건식', '혼합') NULL COMMENT '조리법 대분류 (구버전, 하위호환용)',
    has_mixed_cooking BOOLEAN NOT NULL COMMENT '혼합 조리법 여부',
    data_quality ENUM('검증완료', '미검증') NOT NULL COMMENT '데이터 신뢰도',
    is_training_set BOOLEAN DEFAULT TRUE COMMENT '학습용(TRUE) / 검증용(FALSE)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (base_recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY (target_recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    FOREIGN KEY (ingredient_behavior_id) REFERENCES ingredient_cooking_behavior(behavior_id),
    FOREIGN KEY (primary_method_id) REFERENCES cooking_method(method_id),
    FOREIGN KEY (secondary_method_id) REFERENCES cooking_method(method_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ML 보조 모듈 학습 데이터셋';

CREATE INDEX idx_ml_training ON ml_training_dataset(is_training_set);
CREATE INDEX idx_ml_quality ON ml_training_dataset(data_quality);
CREATE INDEX idx_ml_method ON ml_training_dataset(primary_method_id);


-- ----------------------------------------------------------------------------
-- 12. 스케일링 가중치 테이블 (scaling_coefficient) ⭐ v4 대폭 개편 / v4.1 estimation_method 추가 / v4.2 se_b 추가
-- ----------------------------------------------------------------------------
-- v3→v4 변경:
--   - source ENUM에서 'LLM추출' 제거
--   - 멱함수 파라미터 컬럼 추가 (power_law_a, power_law_b)
--   - 통계 검증 컬럼 추가 (r_squared, p_value, confidence_interval_*)
--   - scaling_exponent → power_law_b로 의미 명확화 (하위호환 위해 유지)
-- v4→v4.1 변경 [ADR-001·002]:
--   - estimation_method 컬럼 추가: 추정 전략 기록 ('mixedlm'|'curve_fit'|'category_mean')
--   - cooking_method_id → group_type 기반으로 룩업 키 재정의 (cooking_method.group_type 참조)
--   - 수식 변경: Y = a × X^b (구버전) → ratio = a × N^(b-1), 역변환: Y = a × base × N^b [ADR-001]
-- v4.1→v4.2 변경 [ADR-003]:
--   - se_b 컬럼 추가: MixedLM이 추정한 b의 표준오차 (Standard Error)
--     용도: 출력 클리핑 임계값 ε = 2 × se_b 산출 기준 (코드 하드코딩 금지, DB에서 읽을 것)
--     의존성: 5월 1회차 피드백(5/4) 전 적재 완료 필수 (ADR-003 준수사항)
-- v4.2→v4.3 변경 [ADR-002 v3]:
--   - estimation_method 허용값에 'nutritionist_feedback' 추가
--     역할: 영양사 피드백으로 보정된 세부 조리방법별 b값 식별
--     구분: 통계 도출(mixedlm/curve_fit) vs 피드백 보정(nutritionist_feedback) 층위 분리
--     룩업 우선순위: (cooking_method_id × ingredient_category) > (group_type × ingredient_category) > category_mean
-- ----------------------------------------------------------------------------
CREATE TABLE scaling_coefficient (
    coefficient_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '가중치 ID',
    
    -- 적용 대상 (NULL이면 전체 적용)
    ingredient_id INT NULL COMMENT '특정 식재료 (NULL이면 카테고리/거동별 적용)',
    cooking_method_id INT NULL COMMENT '특정 조리법 ID (하위호환용, group_type 사용 권장)',
    group_type VARCHAR(20) NULL COMMENT '3대분류 기반 룩업 키 [ADR-002]: dry_heat/moist_heat/no_heat',
    ingredient_behavior_id INT NULL COMMENT '거동 특성별',
    ingredient_category VARCHAR(50) NULL COMMENT '카테고리별 (육류, 채소 등)',
    
    -- 멱함수 파라미터 [ADR-001 Option A]
    -- 모델: ratio = power_law_a × N^(power_law_b - 1)
    -- 역변환: Y = power_law_a × base_amount × N^power_law_b
    power_law_a DECIMAL(8,4) DEFAULT 1.0000 COMMENT '멱함수 계수 a',
    power_law_b DECIMAL(5,3) NOT NULL COMMENT '멱함수 지수 b (b=1이면 선형, b<1이면 양념류 등)',
    scaling_exponent DECIMAL(5,3) NOT NULL COMMENT '스케일링 지수 (power_law_b와 동일, 하위호환)',
    
    -- 통계 검증 결과
    r_squared DECIMAL(5,3) NULL COMMENT 'R² 결정계수 (0~1)',
    p_value DECIMAL(8,6) NULL COMMENT 'p-value (유의수준 목표: < 0.05)',
    confidence_interval_lower DECIMAL(5,3) NULL COMMENT '95% 신뢰구간 하한 (b의 하한)',
    confidence_interval_upper DECIMAL(5,3) NULL COMMENT '95% 신뢰구간 상한 (b의 상한)',
    sample_size INT NULL COMMENT '분석에 사용된 샘플 수 (셀 단위)',
    estimation_method VARCHAR(20) NULL COMMENT '추정 전략 [ADR-002 v3]: mixedlm(주)/curve_fit(보조,N≥10)/category_mean(fallback)/nutritionist_feedback(피드백 보정)',
    se_b DECIMAL(8,6) NULL COMMENT 'MixedLM 표준오차 SE(b) [ADR-003]: 출력 클리핑 ε = 2×se_b 산출 기준. 코드 하드코딩 금지',
    
    -- 버전 관리
    version INT DEFAULT 1 COMMENT '버전 번호 (영양사 피드백 반영 시 증가)',
    source ENUM('통계분석', '영양사피드백', '수동조정') NOT NULL COMMENT '가중치 출처',
    parent_coefficient_id INT NULL COMMENT '이전 버전 가중치 ID',
    is_active BOOLEAN DEFAULT TRUE COMMENT '현재 사용 중인 버전',
    
    -- 성능 기록
    test_error_rate DECIMAL(6,3) NULL COMMENT '이 가중치의 테스트 오차율 (MAPE %)',
    test_sample_size INT NULL COMMENT '테스트 샘플 수',
    
    notes TEXT NULL COMMENT '변경 사유 / 비고',
    created_by VARCHAR(100) NULL COMMENT '생성자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    FOREIGN KEY (cooking_method_id) REFERENCES cooking_method(method_id),
    FOREIGN KEY (ingredient_behavior_id) REFERENCES ingredient_cooking_behavior(behavior_id),
    FOREIGN KEY (parent_coefficient_id) REFERENCES scaling_coefficient(coefficient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='스케일링 가중치 (ADR-001: ratio=a×N^(b-1), 역변환 Y=a×base×N^b, 버전 관리)';

CREATE INDEX idx_coef_active ON scaling_coefficient(is_active);
CREATE INDEX idx_coef_version ON scaling_coefficient(version);
CREATE INDEX idx_coef_source ON scaling_coefficient(source);
CREATE INDEX idx_coef_category ON scaling_coefficient(ingredient_category);


-- ----------------------------------------------------------------------------
-- 13. 영양사 피드백 테이블 (nutritionist_feedback) v4.1: 루브릭 점수 추가 / v4.2: 클리핑 감사 컬럼 추가
-- ----------------------------------------------------------------------------
-- v4→v4.1 변경 [ADR-002]:
--   - rubric_score_taste, rubric_score_feasibility, rubric_score_field, rubric_avg 컬럼 추가
-- v4.1→v4.2 변경 [ADR-003]:
--   - delta_g_original: 클리핑 전 원본 Δ_g (감사 추적, Audit Trail — 삭제 금지)
--   - delta_g_clipped:  1단계 IQR 클리핑 적용 후 실제 사용된 Δ_g
--   - b_new_raw:        2단계 클리핑 전 가중 평균 업데이트 결과값
--   - is_clipped:       1·2단계 중 어느 하나라도 클리핑 발생 시 TRUE
-- ----------------------------------------------------------------------------
-- ----------------------------------------------------------------------------
CREATE TABLE nutritionist_feedback (
    feedback_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '피드백 ID',
    recipe_id INT NOT NULL COMMENT '평가 대상 레시피',
    feedback_round INT NOT NULL COMMENT '피드백 회차 (1~5차)',
    nutritionist_name VARCHAR(100) NULL COMMENT '평가자명',
    nutritionist_id VARCHAR(50) NULL COMMENT '평가자 식별자',
    feedback_date DATE NOT NULL COMMENT '피드백 날짜',
    overall_rating ENUM('상용가능', '수정필요', '부적합') NOT NULL COMMENT '전체 평가 (4점 이상=상용가능)',
    rubric_score_taste TINYINT NULL COMMENT '루브릭 항목1: 맛·간 적절성 (1~5점) [ADR-002]',
    rubric_score_feasibility TINYINT NULL COMMENT '루브릭 항목2: 조리 실현 가능성 (1~5점) [ADR-002]',
    rubric_score_field TINYINT NULL COMMENT '루브릭 항목3: 대량 조리 현장 적합성 (1~5점) [ADR-002]',
    rubric_avg DECIMAL(3,2) NULL COMMENT '루브릭 3항목 평균 점수 (4.00 이상=상용가능 판정)',
    ingredient_feedback JSON NULL COMMENT '재료별 수정 사항 (Δ_g JSON)',
    
    -- 파라미터 업데이트 클리핑 감사 추적 컬럼 [ADR-003] — 삭제 금지 (학술 재현성 요건)
    delta_g_original DECIMAL(10,2) NULL COMMENT '[ADR-003] 1단계 클리핑 전 원본 Δ_g (g). 영양사 입력 원본값 보존',
    delta_g_clipped  DECIMAL(10,2) NULL COMMENT '[ADR-003] 1단계 IQR 클리핑 후 실제 적용된 Δ_g (g). NULL이면 클리핑 미발생',
    b_new_raw        DECIMAL(8,4)  NULL COMMENT '[ADR-003] 2단계 출력 클리핑 전 가중 평균 업데이트 결과 b값 (b_new_raw)',
    is_clipped       BOOLEAN DEFAULT FALSE COMMENT '[ADR-003] 1단계(Δ_g) 또는 2단계(b_new) 클리핑 발생 여부. TRUE 시 원본값 검토 권장',
    detailed_comments TEXT NULL COMMENT '상세 의견',
    is_applied BOOLEAN DEFAULT FALSE COMMENT '피드백 반영 여부',
    applied_date DATE NULL COMMENT '반영 날짜',
    applied_coefficient_id INT NULL COMMENT '반영된 가중치 ID',
    notes TEXT NULL COMMENT '비고',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY (applied_coefficient_id) REFERENCES scaling_coefficient(coefficient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='영양사 피드백 관리 (루브릭 5점 척도 + ADR-003 클리핑 감사 추적 컬럼 포함)';

CREATE INDEX idx_feedback_round ON nutritionist_feedback(feedback_round);
CREATE INDEX idx_feedback_rating ON nutritionist_feedback(overall_rating);
CREATE INDEX idx_feedback_applied ON nutritionist_feedback(is_applied);


-- ============================================================================
-- [모듈 3] CSP Solver 테이블
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 14. 타겟 그룹 테이블 (user_group)
-- ----------------------------------------------------------------------------
CREATE TABLE user_group (
    group_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '그룹 ID',
    group_name VARCHAR(100) NOT NULL COMMENT '그룹명',
    group_type ENUM('학생', '군인', '환자', '노인', '일반') NOT NULL COMMENT '그룹 유형',
    daily_calories INT NOT NULL COMMENT '1일 권장 칼로리 (kcal)',
    daily_protein_g DECIMAL(6,2) NULL COMMENT '1일 권장 단백질 (g)',
    daily_sodium_mg DECIMAL(8,2) NULL COMMENT '1일 권장 나트륨 (mg)',
    daily_fat_g DECIMAL(6,2) NULL COMMENT '1일 권장 지방 (g)',
    daily_carbs_g DECIMAL(6,2) NULL COMMENT '1일 권장 탄수화물 (g)',
    activity_multiplier DECIMAL(3,2) DEFAULT 1.0 COMMENT '활동 계수',
    has_restrictions BOOLEAN DEFAULT FALSE COMMENT '특수 제약 조건 존재 여부',
    description TEXT NULL COMMENT '그룹 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='타겟 그룹 (CSP Solver용)';


-- ----------------------------------------------------------------------------
-- 15. 제약 조건 테이블 (constraints)
-- ----------------------------------------------------------------------------
CREATE TABLE constraints (
    constraint_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '제약 ID',
    group_id INT NULL COMMENT '타겟 그룹 ID (NULL이면 전체 적용)',
    constraint_type ENUM('알레르기', '종교', '질병', '기피', '선호') NOT NULL COMMENT '제약 유형',
    ingredient_id INT NOT NULL COMMENT '제약 대상 식재료',
    severity ENUM('절대금지', '제한', '주의', '선호') DEFAULT '주의' COMMENT '심각도',
    alternative_ingredient_id INT NULL COMMENT '대체 식재료 ID',
    description TEXT NULL COMMENT '제약 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (group_id) REFERENCES user_group(group_id),
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    FOREIGN KEY (alternative_ingredient_id) REFERENCES ingredient(ingredient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='제약 조건 (CSP Solver용)';

CREATE INDEX idx_constraint_group ON constraints(group_id);
CREATE INDEX idx_constraint_type ON constraints(constraint_type);


-- ============================================================================
-- [v4.1 신규] CSP Solver 보조 테이블
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 16. 식재료 시세 테이블 (ingredient_price) ⭐ v4.1 신규
-- ----------------------------------------------------------------------------
-- 목적: CSP Solver의 식단가(예산) Hard Constraint 및 원가 계산용
-- 출처: 서울시농수산식품공사 API (또는 aT KAMIS API)
-- ----------------------------------------------------------------------------
CREATE TABLE ingredient_price (
    price_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '시세 ID',
    ingredient_id INT NOT NULL COMMENT '식재료 ID',
    price_per_g DECIMAL(10,4) NOT NULL COMMENT '1g당 단가 (원)',
    price_date DATE NOT NULL COMMENT '시세 기준일',
    source VARCHAR(100) DEFAULT '서울시농수산식품공사' COMMENT '시세 출처',
    notes TEXT NULL COMMENT '비고',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE KEY unique_price_date (ingredient_id, price_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='식재료 시세 (CSP 식단가 제약 및 원가 계산용)';

CREATE INDEX idx_price_ingredient ON ingredient_price(ingredient_id);
CREATE INDEX idx_price_date ON ingredient_price(price_date);


-- ----------------------------------------------------------------------------
-- 17. 제철 식재료 테이블 (seasonal_ingredient) ⭐ v4.1 신규
-- ----------------------------------------------------------------------------
-- 목적: CSP Solver의 제철 식재료 Soft Constraint용
-- 구축 방법: 월별 급식 식단 데이터 빈도 분석으로 제철 패턴 추출
-- ----------------------------------------------------------------------------
CREATE TABLE seasonal_ingredient (
    seasonal_id INT PRIMARY KEY AUTO_INCREMENT COMMENT '제철 ID',
    ingredient_id INT NOT NULL COMMENT '식재료 ID',
    month TINYINT NOT NULL COMMENT '월 (1~12)',
    freq_score DECIMAL(4,3) NOT NULL COMMENT '제철 빈도 점수 (0~1): 급식 식단 데이터 기반',
    is_peak_season BOOLEAN DEFAULT FALSE COMMENT '제철 피크 여부 (freq_score ≥ 0.7)',
    notes VARCHAR(200) NULL COMMENT '비고 (예: 봄나물, 여름철 수박 등)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 일시',
    
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id),
    UNIQUE KEY unique_ingredient_month (ingredient_id, month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='제철 식재료 (CSP Soft Constraint: 제철 식재료 포함 비율 ≥ 40% 목표)';

CREATE INDEX idx_seasonal_month ON seasonal_ingredient(month);
CREATE INDEX idx_seasonal_peak ON seasonal_ingredient(is_peak_season);


-- ============================================================================
-- 뷰 (View) 정의
-- ============================================================================

-- 레시피 페어 뷰 (확정된 매칭만)
CREATE OR REPLACE VIEW v_recipe_pairs AS
SELECT 
    r_small.recipe_id AS small_recipe_id,
    r_small.recipe_name AS small_recipe_name,
    r_small.serving_size AS small_serving_size,
    r_large.recipe_id AS large_recipe_id,
    r_large.recipe_name AS large_recipe_name,
    r_large.serving_size AS large_serving_size,
    (r_large.serving_size / r_small.serving_size) AS scaling_factor,
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


-- 현재 활성 가중치 뷰 (멱함수 파라미터 포함, v4.1: group_type·estimation_method 추가)
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
LEFT JOIN ingredient i ON sc.ingredient_id = i.ingredient_id
LEFT JOIN cooking_method cm ON sc.cooking_method_id = cm.method_id
LEFT JOIN ingredient_cooking_behavior icb ON sc.ingredient_behavior_id = icb.behavior_id
WHERE sc.is_active = TRUE
ORDER BY sc.ingredient_category, sc.group_type;


-- 영양사 피드백 통계 뷰 (v4.1: 루브릭 평균 점수 포함)
CREATE OR REPLACE VIEW v_feedback_stats AS
SELECT 
    feedback_round,
    overall_rating,
    COUNT(*) AS count,
    AVG(rubric_avg) AS avg_rubric_score,
    SUM(CASE WHEN is_applied = TRUE THEN 1 ELSE 0 END) AS applied_count
FROM nutritionist_feedback
GROUP BY feedback_round, overall_rating
ORDER BY feedback_round, overall_rating;


-- 조리법 조합 통계 뷰
CREATE OR REPLACE VIEW v_cooking_method_combinations AS
SELECT 
    cm_primary.method_name AS primary_method,
    cm_secondary.method_name AS secondary_method,
    COUNT(*) AS recipe_count,
    GROUP_CONCAT(DISTINCT r.recipe_name SEPARATOR '; ') AS example_recipes
FROM recipe r
JOIN cooking_method cm_primary ON r.primary_method_id = cm_primary.method_id
LEFT JOIN cooking_method cm_secondary ON r.secondary_method_id = cm_secondary.method_id
GROUP BY cm_primary.method_name, cm_secondary.method_name
ORDER BY recipe_count DESC;


-- 유사도 매칭 현황 뷰 ⭐ v4 신규
CREATE OR REPLACE VIEW v_similarity_summary AS
SELECT 
    match_decision,
    COUNT(*) AS pair_count,
    AVG(composite_score) AS avg_composite_score,
    MIN(composite_score) AS min_score,
    MAX(composite_score) AS max_score,
    SUM(CASE WHEN is_confirmed = TRUE THEN 1 ELSE 0 END) AS confirmed_count
FROM recipe_similarity
GROUP BY match_decision;


-- 스케일링 지수 룩업 뷰 (Rule-based 엔진용) ⭐ v4.1: group_type 기준으로 키 변경 / v4.3: 세부 조리방법 조회 경로 추가
-- 룩업 우선순위 [ADR-002 v3]:
--   1순위: (cooking_method_id × ingredient_category) — 세부 조리방법 피드백 보정값 (estimation_method='nutritionist_feedback')
--   2순위: (group_type × ingredient_category)        — MixedLM 통계 도출값 (estimation_method='mixedlm'/'curve_fit')
--   3순위: ingredient_category 평균                  — category_mean fallback
-- 엔진 코드에서 반드시 위 우선순위 순서로 조회할 것. 1순위 미존재 시 2순위, 2순위 미존재 시 3순위 사용.
CREATE OR REPLACE VIEW v_scaling_lookup AS
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
    -- 우선순위 컬럼: 엔진에서 ORDER BY lookup_priority ASC LIMIT 1 로 사용
    CASE
        WHEN sc.cooking_method_id IS NOT NULL AND sc.estimation_method = 'nutritionist_feedback' THEN 1
        WHEN sc.group_type IS NOT NULL AND sc.estimation_method IN ('mixedlm', 'curve_fit') THEN 2
        ELSE 3
    END AS lookup_priority
FROM scaling_coefficient sc
LEFT JOIN cooking_method cm ON sc.cooking_method_id = cm.method_id
WHERE sc.is_active = TRUE
ORDER BY sc.ingredient_category, lookup_priority, sc.group_type;


-- ============================================================================
-- 스키마 정보 요약
-- ============================================================================
/*
총 테이블 수: 17개 (v4.1: v4의 15개 + 2개 추가)

[모듈 2 - 비선형 스케일링 엔진] (12개)
 1. cooking_method              조리법 마스터 (v4.1: group_type 컬럼 추가)
 2. ingredient_cooking_behavior 식재료 거동 특성
 3. ingredient                  식재료 마스터 (v4.1: waste_rate, color_category 추가)
 4. ingredient_synonym          재료명 정규화 사전 ⭐ v4 신규
 6. recipe                      레시피 마스터 (통합)
 7. recipe_ingredient_map       레시피-재료 매핑 (v4.4: ingredient_type 추가)
 8. unit_conversion             단위 환산 (v4.1: minimum_order_unit 추가)
 9. recipe_similarity           레시피 유사도 ⭐ v4 신규 (v4.1: is_manually_verified 추가)
10. scaling_analysis            방법별 성능 비교
11. ml_training_dataset         ML 보조 모듈 학습 데이터 (v4.1: ratio, group_type 컬럼 추가)
12. scaling_coefficient         스케일링 가중치 ⭐ v4 개편 (v4.1: estimation_method, group_type 추가 / v4.2: se_b 추가)
13. nutritionist_feedback       영양사 피드백 (v4.1: 루브릭 점수 추가 / v4.2: 클리핑 감사 추적 컬럼 추가)

[모듈 1 - 영양성분 DB] (1개)
 5. nutrition_recipe            영양성분 참조 DB

[모듈 3 - CSP Solver] (4개)
14. user_group                  타겟 그룹
15. constraints                 제약 조건
16. ingredient_price            식재료 시세 ⭐ v4.1 신규
17. seasonal_ingredient         제철 식재료 ⭐ v4.1 신규

v4 변경사항:
- ingredient_synonym, recipe_similarity 테이블 추가
- scaling_coefficient: LLM 제거, 멱함수 파라미터(a, b, R², p_value) 추가
- scaling_analysis: 'LLM' 제거, 'ML보조'/'하이브리드' 추가
- 뷰 2개 추가: v_similarity_summary, v_scaling_lookup

v4.1 변경사항 (ADR-001·002 반영, 2026-02-22):
- cooking_method: group_type 컬럼 추가 (dry_heat/moist_heat/no_heat)
- scaling_coefficient: estimation_method 컬럼 추가 (mixedlm/curve_fit/category_mean)
  ※ 수식 변경: Y=a×X^b → ratio=a×N^(b-1), 역변환: Y=a×base×N^b
- recipe_similarity: is_manually_verified 컬럼 추가
- ingredient: waste_rate, color_category 추가
- unit_conversion: minimum_order_unit 추가
- ml_training_dataset: ratio, group_type 컬럼 추가
- nutritionist_feedback: rubric_score_* 컬럼 추가 (루브릭 5점 척도)
- ingredient_price, seasonal_ingredient 테이블 추가 (CSP 식단가·제철 제약용)
- v_active_coefficients: group_type, estimation_method 컬럼 추가
- v_feedback_stats: avg_rubric_score 컬럼 추가
- v_scaling_lookup: group_type 기준 룩업 키로 변경

v4.2 변경사항 (ADR-003 반영, 2026-02-23):
- scaling_coefficient: se_b 컬럼 추가 (MixedLM SE(b), 출력 클리핑 ε 산출 기준)
- nutritionist_feedback: delta_g_original, delta_g_clipped, b_new_raw, is_clipped 컬럼 추가 (클리핑 감사 추적)

v4.3 변경사항 (ADR-002 v3 반영, 2026-03-07):
- scaling_coefficient: estimation_method 허용값에 'nutritionist_feedback' 추가
  (통계 도출값 mixedlm/curve_fit과 피드백 보정값 구분)
- v_scaling_lookup: 세부 조리방법 조회 경로 추가 + lookup_priority 컬럼 도입
  룩업 우선순위: 세부 조리방법 피드백값(1) > 3대분류 통계값(2) > category_mean(3)

v4.4 변경사항 (ADR-004 반영, 2026-07-13):
- recipe_ingredient_map: ingredient_type 컬럼 추가 (조달 형태 DIRECT/COMMERCIAL, 발주 시스템용, 스케일링 엔진 참조 금지)
*/

SELECT '✅ DB 스키마 v4.4 생성 완료: 17개 테이블 (모듈 1+2+3 통합, ADR-001·002·003·004 반영)' AS status;
