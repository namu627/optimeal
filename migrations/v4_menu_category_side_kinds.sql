-- ---------------------------------------------------------------------------
-- v4 · nutrition_recipe.menu_category 에 반상 세분 카테고리(주찬·부찬·김치) 추가
--
-- 배경 (2026-08-12 영양사 지적):
--   식단표는 주식 → 국 → 주찬 → 부찬 → 김치 순으로 작성되어야 하고,
--   끼니 구성은 주식1·국1·주찬1·부찬 2이상·김치1 이다.
--   기존 CHECK 는 '반찬' 한 덩어리만 허용해 재분류 UPDATE 가 거부된다.
--
-- '반찬'은 목록에 남긴다 — 아직 재분류하지 않은 환경의 기존 행이 CHECK 위반이
-- 되지 않게 하기 위함이다(재분류는 scripts/reclassify_menu_categories.py).
--
-- 멱등: 제약을 이름으로 지우고 다시 만든다. 반복 실행해도 결과가 같다.
--
-- 실행:
--   docker exec -i optimeal_db psql -U optimeal_user -d optimeal \
--     < migrations/v4_menu_category_side_kinds.sql
-- ---------------------------------------------------------------------------

BEGIN;

ALTER TABLE nutrition_recipe
    DROP CONSTRAINT IF EXISTS nutrition_recipe_menu_category_check;

ALTER TABLE nutrition_recipe
    ADD CONSTRAINT nutrition_recipe_menu_category_check
    CHECK (menu_category IN ('주식', '국', '찌개',
                             '주찬', '부찬', '김치',
                             '반찬', '후식', '음료', '기타'));

COMMIT;
