-- Migration: v3_ingredient_type  (ADR-004 스키마 반영 — 스키마 보완 2)
-- 목적   : recipe_ingredient_map 에 ADR-004 관련 컬럼 일괄 추가
--            (1) ingredient_type : 조달 형태 DIRECT / COMMERCIAL (발주 시스템용)
--            (2) amount_type     : 수량 표기 상태 MEASURED / DISCRETIONARY / UNKNOWN
--            (3) amount_note     : 원본 수량 표기 보존
--          + amount 의 NOT NULL 제약 완화 (DISCRETIONARY·UNKNOWN 은 NULL)
-- 근거   : 간트차트 v0.0.10 '스키마 보완 2' / ADR-004 (ingredient_amount 불확정값 처리)
-- 주의   : 스케일링 엔진(FR-05)은 amount_type='MEASURED' AND ingredient_type='DIRECT' 만
--          지수 도출 대상으로 삼음 — 코드 조건 분기 명시. ingredient_type 은 발주 전용, 엔진 참조 금지. [ADR-004 준수사항]
-- 멱등성 : ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS 사용 → 재실행 안전
-- 대상 DB: PostgreSQL (docker/init/01_schema.sql 기준)

BEGIN;

-- ============================================================
-- (1) ingredient_type — 조달 형태
-- ============================================================
ALTER TABLE recipe_ingredient_map
    ADD COLUMN IF NOT EXISTS ingredient_type VARCHAR(20)
        CHECK (ingredient_type IS NULL OR ingredient_type IN ('DIRECT', 'COMMERCIAL'));

COMMENT ON COLUMN recipe_ingredient_map.ingredient_type IS
    '조달 형태: DIRECT(직접 계량) / COMMERCIAL(시판 완제품). 발주 시스템 전용. 스케일링 엔진 참조 금지 [ADR-004]';

-- ============================================================
-- (2) amount_type — 수량 표기 상태
-- ============================================================
ALTER TABLE recipe_ingredient_map
    ADD COLUMN IF NOT EXISTS amount_type VARCHAR(20) NOT NULL DEFAULT 'MEASURED'
        CHECK (amount_type IN ('MEASURED', 'DISCRETIONARY', 'UNKNOWN'));

COMMENT ON COLUMN recipe_ingredient_map.amount_type IS
    '수량 표기 상태 [ADR-004]: MEASURED(수치확정) / DISCRETIONARY(약간·적당량 등 재량) / UNKNOWN(원본 데이터 누락)';

-- ============================================================
-- (3) amount_note — 원본 표기 보존
-- ============================================================
ALTER TABLE recipe_ingredient_map
    ADD COLUMN IF NOT EXISTS amount_note VARCHAR(100) NULL;

COMMENT ON COLUMN recipe_ingredient_map.amount_note IS
    '원본 수량 표기 보존 [ADR-004] ("약간", "적당량", "1봉지" 등). 영양사·팀 검수 시 원본 의도 확인용';

-- ============================================================
-- (4) amount NOT NULL 제약 완화
--     ADR-004: DISCRETIONARY·UNKNOWN 은 amount = NULL.
--     (0 으로 채우면 '재료 없음'으로 오인 → 엔진 오작동. ADR-004 명시적 금지.)
-- ============================================================
ALTER TABLE recipe_ingredient_map
    ALTER COLUMN amount DROP NOT NULL;

-- ============================================================
-- (5) 기존 행 백필
-- ============================================================
-- 5-1) ingredient_type 기본값 백필: 기존 행 → DIRECT (대부분 원재료는 직접 계량)
UPDATE recipe_ingredient_map
SET ingredient_type = 'DIRECT'
WHERE ingredient_type IS NULL;

-- 5-2) COMMERCIAL 후보 표시 (휴리스틱 · ★검수 필요★)
--      ADR-004: COMMERCIAL 은 원본에 '시판 완제품 사용'이 명시된 경우에만 확정.
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

-- 5-3) DISCRETIONARY 자동 태깅 (휴리스틱 · ★검수 필요★): 원문에 재량 표기
UPDATE recipe_ingredient_map
SET amount_type = 'DISCRETIONARY',
    amount_note = COALESCE(amount_note, NULLIF(TRIM(original_text), ''))
WHERE original_text ~ '(약간|적당량|적당히|조금|한[[:space:]]*꼬집|꼬집|기호에?[[:space:]]*따라|취향(껏|대로)?|입맛)';

-- 5-4) DISCRETIONARY 행의 수치는 NULL 로 정리 (ADR-004). 원본 수치는 amount_note 에 보존됨.
UPDATE recipe_ingredient_map
SET amount = NULL
WHERE amount_type = 'DISCRETIONARY';

-- ============================================================
-- (6) 조회 성능용 인덱스
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_map_ingredient_type
    ON recipe_ingredient_map(ingredient_type);
CREATE INDEX IF NOT EXISTS idx_map_amount_type
    ON recipe_ingredient_map(amount_type);

COMMIT;

-- ────────────────────────────────────────────────────────────
-- 적용 후 확인용 쿼리 (선택 실행)
--   SELECT amount_type, COUNT(*) FROM recipe_ingredient_map GROUP BY amount_type;
--   SELECT ingredient_type, COUNT(*) FROM recipe_ingredient_map GROUP BY ingredient_type;
--
--   -- 스케일링 연구 대상 추출 (ADR-004 기준: MEASURED + DIRECT)
--   SELECT COUNT(*) FROM recipe_ingredient_map
--    WHERE amount_type = 'MEASURED' AND ingredient_type = 'DIRECT';
--
--   -- 휴리스틱 태깅 검수용
--   SELECT map_id, i.ingredient_name, rim.original_text, rim.amount, rim.amount_note,
--          rim.amount_type, rim.ingredient_type
--     FROM recipe_ingredient_map rim JOIN ingredient i USING (ingredient_id)
--    WHERE rim.amount_type='DISCRETIONARY' OR rim.ingredient_type='COMMERCIAL'
--    ORDER BY i.ingredient_name;
-- ────────────────────────────────────────────────────────────

-- 참고: UNKNOWN(원본 데이터 자체 누락)은 기존 행에서 자동 판별 불가하여 백필 제외.
--       신규 대규모 레시피 입력 시 입력 규칙으로 구분한다.

-- 롤백 (문제 발생 시)
--   BEGIN;
--   DROP INDEX IF EXISTS idx_map_amount_type;
--   DROP INDEX IF EXISTS idx_map_ingredient_type;
--   ALTER TABLE recipe_ingredient_map DROP COLUMN IF EXISTS amount_note;
--   ALTER TABLE recipe_ingredient_map DROP COLUMN IF EXISTS amount_type;
--   ALTER TABLE recipe_ingredient_map DROP COLUMN IF EXISTS ingredient_type;
--   COMMIT;