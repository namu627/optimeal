-- Migration: v3_ingredient_type
-- 목적   : recipe_ingredient_map 에 ingredient_type 컬럼 추가 (발주 시스템용)
-- 근거   : 간트차트 v0.0.10 '스키마 보완 2' / ADR-004 (조달 형태 구분)
-- 값     : DIRECT(직접 계량 재료) / COMMERCIAL(시판 완제품)
-- 주의   : 이 컬럼은 발주(order) 로직 전용. 스케일링 엔진(FR-05)에서 참조 금지 [ADR-004 준수사항]
-- 대상 DB: PostgreSQL (docker/init/01_schema.sql 기준)

BEGIN;

-- 1) 컬럼 추가 (nullable VARCHAR(20) + 허용값 CHECK)
ALTER TABLE recipe_ingredient_map
    ADD COLUMN IF NOT EXISTS ingredient_type VARCHAR(20)
        CHECK (ingredient_type IS NULL OR ingredient_type IN ('DIRECT', 'COMMERCIAL'));

COMMENT ON COLUMN recipe_ingredient_map.ingredient_type IS
    '조달 형태: DIRECT(직접 계량) / COMMERCIAL(시판 완제품). 발주 시스템 전용. 스케일링 엔진 참조 금지 [ADR-004]';

-- 2) 기본값 백필: 기존 모든 행을 DIRECT 로 (대부분의 원재료는 직접 계량)
UPDATE recipe_ingredient_map
SET ingredient_type = 'DIRECT'
WHERE ingredient_type IS NULL;

-- 3) COMMERCIAL 후보 표시 (휴리스틱 — ★검수 필요★)
--    ADR-004 기준 COMMERCIAL 은 원본 레시피에 '시판 완제품 사용'이 명시된 경우에만 적용.
--    아래는 재료명/원문 패턴 기반 1차 후보 자동 태깅. 실제 확정 전 영양사·팀 검수 권장.
UPDATE recipe_ingredient_map rim
SET ingredient_type = 'COMMERCIAL'
FROM ingredient i
WHERE rim.ingredient_id = i.ingredient_id
  AND (
        i.ingredient_name  LIKE '%소스%'
     OR i.ingredient_name  LIKE '%드레싱%'
     OR i.ingredient_name  LIKE '%양념장%'
     OR i.ingredient_name  LIKE '%마요네즈%'
     OR i.ingredient_name  LIKE '%케첩%'
     OR i.ingredient_name  LIKE '%페이스트%'
     OR rim.original_text  LIKE '%시판%'
     OR rim.original_text  LIKE '%완제품%'
  );

-- 4) 조회 성능용 인덱스
CREATE INDEX IF NOT EXISTS idx_map_ingredient_type
    ON recipe_ingredient_map(ingredient_type);

COMMIT;

-- ────────────────────────────────────────────────────────────
-- 적용 후 확인용 쿼리 (선택 실행)
--   SELECT ingredient_type, COUNT(*)
--     FROM recipe_ingredient_map GROUP BY ingredient_type;
--
--   -- COMMERCIAL 로 태깅된 항목 눈으로 검수
--   SELECT rim.map_id, i.ingredient_name, rim.original_text, rim.ingredient_type
--     FROM recipe_ingredient_map rim
--     JOIN ingredient i USING (ingredient_id)
--    WHERE rim.ingredient_type = 'COMMERCIAL'
--    ORDER BY i.ingredient_name;
-- ────────────────────────────────────────────────────────────

-- 롤백 (문제 발생 시)
--   BEGIN;
--   DROP INDEX IF EXISTS idx_map_ingredient_type;
--   ALTER TABLE recipe_ingredient_map DROP COLUMN IF EXISTS ingredient_type;
--   COMMIT;
