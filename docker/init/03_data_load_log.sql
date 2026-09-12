-- 03_data_load_log.sql
-- 적재 추적 테이블 (재현성). 로더(scripts/load_*.py)가 파일 해시로 멱등 적재를 기록한다.
--
-- [Finding E 수정] 이 테이블은 로더가 필수로 쓰는데 01_schema.sql 에 누락돼 있었다.
-- 그 결과 fresh `docker compose up` 환경에서 로더가 적재 로그 기록 단계에서 실패→
-- 트랜잭션 롤백→적재 0건이 됐다. init 에 포함해 신규 환경에서도 로더가 정상 동작하게 한다.
-- (API 통합테스트에서 발견·수정, 2026-08-18 남유찬)

CREATE TABLE IF NOT EXISTS data_load_log (
    load_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_name      TEXT,
    file_hash      TEXT UNIQUE,          -- 로더의 ON CONFLICT (file_hash) 대상
    row_count      INTEGER,
    schema_version TEXT,
    loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
