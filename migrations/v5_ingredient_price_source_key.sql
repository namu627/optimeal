-- v5 (2026-09-30): ingredient_price 유일키에 source 포함
--
-- 왜: 원가는 출처 우선순위(KAMIS_W > 가락 > KAMIS_R > 참가격_R > 수기_참조 > 파생 > 정책 0원)로 고르는데,
--     기존 UNIQUE(ingredient_id, price_date) 에서는 같은 날 두 출처가 행을 함께 가질 수 없어
--     먼저 돈 로더가 그날 자리를 차지했다(예: 가락이 먼저 9/30 행을 넣으면 KAMIS_W 9/30 이 버려짐).
--     그러면 로더 실행 순서에 따라 고르는 가격이 달라진다. 출처별로 행을 따로 두면 순서와 무관해진다.
-- 멱등: 몇 번 돌려도 같은 상태.

UPDATE ingredient_price SET source = '서울시농수산식품공사' WHERE source IS NULL;   -- 스키마 기본값과 같은 값
ALTER TABLE ingredient_price ALTER COLUMN source SET NOT NULL;                    -- NULL 은 UNIQUE 에서 중복 허용이라 막는다

ALTER TABLE ingredient_price DROP CONSTRAINT IF EXISTS ingredient_price_ingredient_id_price_date_key;
ALTER TABLE ingredient_price DROP CONSTRAINT IF EXISTS ingredient_price_ingredient_id_price_date_source_key;
ALTER TABLE ingredient_price
    ADD CONSTRAINT ingredient_price_ingredient_id_price_date_source_key UNIQUE (ingredient_id, price_date, source);
