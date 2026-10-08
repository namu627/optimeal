-- v6 (2026-10-07): 재료 영양성분(당류·인·칼륨) 테이블
--
-- 왜: 기저질환 대체식(FR-11 — 당뇨 당류, 신장질환 칼륨·인)을 걸려면 메뉴별 값이 필요한데, 식단 후보
--     메뉴(식품안전나라 COOKRCP01)에는 나트륨·탄수화물만 있고 당류는 비어 있으며 칼륨·인 칸은 없다.
--     식약처 식품영양성분DB 의 재료(원재료성) 값을 재료에 붙이고 메뉴값은 재료 g 합산을 메뉴 공식 열량에
--     맞춰 보정해 계산한다(module_3/src/disease_diet.py).
-- 적재: scripts/load_ingredient_nutrient.py --apply (원본 data/manual/ingredient_nutrient_map.csv)
-- 멱등: 몇 번 돌려도 같은 상태.

CREATE TABLE IF NOT EXISTS ingredient_nutrient (
    ingredient_id  INT           PRIMARY KEY,
    energy_kcal    DECIMAL(8,1)  NULL,
    sugar_g        DECIMAL(8,2)  NULL,
    phosphorus_mg  DECIMAL(8,1)  NULL,
    potassium_mg   DECIMAL(8,1)  NULL,
    basis          VARCHAR(20)   NOT NULL,
    source_name    VARCHAR(200)  NULL,
    food_cd        VARCHAR(40)   NULL,
    updated_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ingredient_id) REFERENCES ingredient(ingredient_id)
);

-- 초기 v6 초안(열량 칸 없음)으로 만든 DB 도 같은 상태로 맞춘다
ALTER TABLE ingredient_nutrient ADD COLUMN IF NOT EXISTS energy_kcal DECIMAL(8,1) NULL;
