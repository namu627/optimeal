#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_recipe_data.py
목적: data/raw/ 폴더의 xlsx/csv 파일을 PostgreSQL DB에 적재
실행: docker exec optimeal_app python scripts/load_recipe_data.py

적재 대상:
  - 소규모_레시피_DB_남유찬_v0_10.xlsx   → recipe 테이블 (serving_category='소규모')
  - 대규모_레시피_DB_남유찬_v0_08.xlsx   → recipe 테이블 (serving_category='대규모')
  - 소규모대규모_레시피_매칭쌍_*.csv      → recipe_similarity 테이블
  - df_B.csv                            → ml_training_dataset 테이블
  - 재료명정규화테이블_최종_*_권성민.xlsx  → ingredient_synonym 테이블
"""

import hashlib
import os
import re
import sys
import warnings
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore", category=UserWarning)

# ─────────────────────────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────────────────────────
# 스크립트 위치: /workspace/scripts/load_recipe_data.py
# 데이터 위치:   /workspace/data/raw/
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "raw"

# 현재 스키마 버전 (schema_version 테이블 기준)
SCHEMA_VERSION = "v1"

# 적재 대상 파일명
FILE_SMALL_RECIPE    = "소규모_레시피_DB_남유찬_v0_10.xlsx"
FILE_LARGE_RECIPE    = "대규모_레시피_DB_남유찬_v0_08.xlsx"
FILE_MATCHING        = "소규모-대규모 레시피 매칭 쌍 단일 csv_박소희.csv"
FILE_ML_DATASET      = "df_B.csv"
FILE_INGREDIENT_NORM = "재료명정규화테이블_최종_20260307_권성민.xlsx"

# ─────────────────────────────────────────────────────────────────
# 조리방법 이름 정규화 매핑 (xlsx/csv 값 → cooking_method.method_name)
# ─────────────────────────────────────────────────────────────────
COOKING_METHOD_NAME_MAP = {
    # 대규모 xlsx 값
    "끓이기":         "끓이기",
    "끓이기(삶기)":   "끓이기",
    "삶기":           "삶기",
    "볶기":           "볶음",
    "볶음":           "볶음",
    "찌기":           "찜",
    "찜":             "찜",
    "굽기":           "구이",
    "구이":           "구이",
    "부침":           "구이",
    "무치기":         "무침",
    "무침":           "무침",
    "절이기(담그기)": "무침",   # 비가열 → 무침으로 대응
    "조림":           "조림",
    "졸이기":         "조림",
    "졸이기(조리기)": "조림",
    "조리기(졸이기)": "조림",
    "튀기기":         "튀김",
    "튀김":           "튀김",
    "샐러드":         "무침",   # 비가열 처리
    "밥짓기":         "끓이기",
    # 소규모 xlsx 값 (group_type 형태)
    "습열":           "끓이기",  # moist_heat 대표값
    "건열":           "볶음",    # dry_heat 대표값
    "비가열":         "무침",    # no_heat
}

# data_source 값 → recipe 테이블 CHECK 제약 ('식약처API','영양사협회','산업체','영양사협조','커뮤니티')
DATA_SOURCE_MAP = {
    "영양사협회":   "영양사협회",
    "영양사협조":   "영양사협조",
    "식품안전나라": "식약처API",
    "영양사도우미": "영양사협회",
    "레시피코리아": "커뮤니티",
    "서적":         "산업체",
    "대한영양사협회": "영양사협회",
}

# match_decision 값 → recipe_similarity CHECK 제약 ('자동태깅','수동검토','제외')
MATCH_DECISION_MAP = {
    "auto_tag":      "자동태깅",
    "manual_review": "수동검토",
    "excluded":      "제외",
}

# ingredient_role 값 → ml_training_dataset CHECK 제약 ('주재료','부재료','조미료','양념')
ROLE_MAP = {
    "main":      "주재료",
    "sub":       "부재료",
    "seasoning": "조미료",
    "sauce":     "양념",
}

# group_type (한국어) → cooking_method.group_type (영어)
GROUP_TYPE_MAP = {
    "비가열": "no_heat",
    "습열":   "moist_heat",
    "건열":   "dry_heat",
}

# group_type → cooking_method_category CHECK 제약 ('습식','건식','혼합')
GROUP_TYPE_TO_CATEGORY = {
    "no_heat":   "건식",
    "moist_heat": "습식",
    "dry_heat":  "건식",
}

# ingredient_synonym match_type 매핑
MATCH_TYPE_MAP = {
    "수동규칙":   "수동매핑",
    "접두어규칙": "부분일치",
    "완전일치":   "완전일치",
}


# ─────────────────────────────────────────────────────────────────
# DB 연결
# ─────────────────────────────────────────────────────────────────
def get_engine():
    """환경변수에서 PostgreSQL 연결 정보를 읽어 SQLAlchemy 엔진 반환."""
    host     = os.getenv("POSTGRES_HOST", "db")
    port     = os.getenv("POSTGRES_PORT", "5432")
    db       = os.getenv("POSTGRES_DB",   "optimeal")
    user     = os.getenv("POSTGRES_USER", "optimeal")
    password = os.getenv("POSTGRES_PASSWORD", "optimeal_dev_pw")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return create_engine(url)


# ─────────────────────────────────────────────────────────────────
# 조리방법 ID 룩업 테이블 구축
# ─────────────────────────────────────────────────────────────────
def build_cooking_method_lookup(conn):
    """DB에서 cooking_method 테이블을 읽어 {method_name: method_id} 딕셔너리 반환."""
    rows = conn.execute(
        text("SELECT method_id, method_name FROM cooking_method")
    ).fetchall()
    return {row.method_name: row.method_id for row in rows}


def resolve_method_id(raw_value, method_lookup, default_id=None):
    """
    xlsx/csv의 조리방법 텍스트를 method_id로 변환.
    - COOKING_METHOD_NAME_MAP으로 표준명 변환 후 DB 룩업
    - 매칭 실패 시 default_id 반환 (None이면 최솟값 사용)
    """
    if pd.isna(raw_value) or not str(raw_value).strip():
        return default_id
    val = str(raw_value).strip()
    # 표준 이름으로 변환
    std_name = COOKING_METHOD_NAME_MAP.get(val, val)
    mid = method_lookup.get(std_name)
    if mid is None:
        # 부분 일치 시도 (끝부분 비교)
        for k, v in method_lookup.items():
            if k in val or val in k:
                mid = v
                break
    return mid if mid is not None else default_id


# ─────────────────────────────────────────────────────────────────
# 재료 조회/생성 헬퍼
# ─────────────────────────────────────────────────────────────────
def get_or_create_ingredient(conn, ingredient_name, ingredient_cache):
    """
    ingredient_name으로 ingredient 테이블 조회.
    없으면 INSERT 후 ingredient_id 반환.
    ingredient_cache는 {name: id} 딕셔너리로 DB 조회 최소화.
    """
    name = str(ingredient_name).strip()
    if name in ingredient_cache:
        return ingredient_cache[name]

    row = conn.execute(
        text("SELECT ingredient_id FROM ingredient WHERE ingredient_name = :n"),
        {"n": name}
    ).fetchone()

    if row:
        ingredient_cache[name] = row.ingredient_id
        return row.ingredient_id

    # 재료가 없으면 신규 생성 (최소 필드만 입력)
    result = conn.execute(
        text("""
            INSERT INTO ingredient (ingredient_name, standard_unit)
            VALUES (:n, 'g')
            RETURNING ingredient_id
        """),
        {"n": name}
    )
    new_id = result.fetchone().ingredient_id
    ingredient_cache[name] = new_id
    return new_id


# ─────────────────────────────────────────────────────────────────
# 파일 존재 여부 확인 헬퍼
# ─────────────────────────────────────────────────────────────────
def check_file(file_path: Path) -> bool:
    """파일이 없으면 경고 출력 후 False 반환."""
    if not file_path.exists():
        print(f"  [경고] 파일 없음, 건너뜀: {file_path.name}")
        return False
    return True


# ─────────────────────────────────────────────────────────────────
# 파일 해시 및 적재 이력 관리
# ─────────────────────────────────────────────────────────────────
def compute_file_hash(file_path: Path) -> str:
    """파일 SHA256 해시 계산 (64자리 16진수 문자열 반환)."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        # 대용량 파일 대응: 64KB 청크 단위로 읽기
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def ensure_data_load_log_table(engine):
    """
    data_load_log 테이블이 없으면 생성.
    - 파일명, 파일해시(UNIQUE), 적재일시, 행수, 스키마버전 기록
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS data_load_log (
                log_id         SERIAL        PRIMARY KEY,
                file_name      VARCHAR(500)  NOT NULL,
                file_hash      VARCHAR(64)   NOT NULL UNIQUE,
                loaded_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                row_count      INT,
                schema_version VARCHAR(20)
            )
        """))


def is_already_loaded(engine, file_hash: str) -> bool:
    """동일 해시로 이미 적재된 파일인지 data_load_log에서 확인."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT log_id FROM data_load_log WHERE file_hash = :h"),
            {"h": file_hash}
        ).fetchone()
    return row is not None


def record_load_log(engine, file_name: str, file_hash: str, row_count: int):
    """적재 완료 후 data_load_log에 이력 기록."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO data_load_log (file_name, file_hash, row_count, schema_version)
                VALUES (:fn, :fh, :rc, :sv)
            """),
            {"fn": file_name, "fh": file_hash, "rc": row_count, "sv": SCHEMA_VERSION}
        )


def get_schema_version(engine) -> str:
    """
    DB schema_version 테이블에서 현재 적용 중인 스키마 버전 조회.
    테이블이 없거나 조회 실패 시 안내 문자열 반환.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
            ).fetchone()
            return row.version if row else "(버전 없음)"
    except Exception:
        return "(schema_version 테이블 없음)"


# ─────────────────────────────────────────────────────────────────
# 1. 레시피 xlsx 적재 (소규모/대규모 공용)
# ─────────────────────────────────────────────────────────────────
def load_recipes(engine, file_path: Path, serving_category: str):
    """
    소규모/대규모 레시피 xlsx를 읽어 recipe 테이블에 적재.
    - 헤더: 엑셀 2행(index 1), 한국어 설명 행(index 2) 스킵
    - serving_category: '소규모' 또는 '대규모'
    - 원본 recipe_id는 notes 필드에 '[orig:{id}]' 형태로 보존
    - 이미 적재된 행은 notes 패턴 검색으로 중복 방지 (skip)
    """
    if not check_file(file_path):
        return {}

    print(f"\n[1] 레시피 적재 ({serving_category}): {file_path.name}")

    # ── 파일 해시 계산 및 중복 적재 확인 ──
    file_hash = compute_file_hash(file_path)
    print(f"  파일 해시 (SHA256): {file_hash[:16]}...")
    if is_already_loaded(engine, file_hash):
        print(f"  [스킵] 이미 적재된 버전입니다. (hash: {file_hash[:16]}...)")
        # 스킵되더라도 notes 컬럼에서 원본 ID → DB ID 매핑 복원
        recipe_id_map = {}
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT recipe_id, notes FROM recipe WHERE serving_category = :sc AND notes LIKE '[orig:%'"),
                {"sc": serving_category}
            ).fetchall()
            for r in rows:
                # notes = '[orig:A1034]' 형태에서 원본 ID 추출
                import re
                m = re.search(r'\[orig:(.+?)\]', r.notes or "")
                if m:
                    recipe_id_map[m.group(1)] = r.recipe_id
        print(f"  매핑 복원: {len(recipe_id_map)}건")
        return recipe_id_map

    # 엑셀 구조: row0=메모, row1=컬럼명(영문), row2=컬럼설명(한글), row3~=데이터
    df = pd.read_excel(file_path, header=1, skiprows=[2])
    df = df.dropna(subset=["recipe_id"])  # recipe_id 없는 행 제거
    print(f"  파일 행 수: {len(df)}")

    inserted = 0
    skipped  = 0
    recipe_id_map = {}  # {원본_str_id: db_int_id}

    with engine.begin() as conn:
        method_lookup = build_cooking_method_lookup(conn)
        # 기본 method_id (끓이기 등 첫 번째 항목 사용)
        default_method_id = min(method_lookup.values()) if method_lookup else 1

        for _, row in df.iterrows():
            orig_id = str(row["recipe_id"]).strip()
            marker  = f"[orig:{orig_id}]"

            # ── 중복 체크: notes 필드에 마커가 이미 있으면 스킵 ──
            existing = conn.execute(
                text("SELECT recipe_id FROM recipe WHERE notes LIKE :m"),
                {"m": f"%{marker}%"}
            ).fetchone()
            if existing:
                recipe_id_map[orig_id] = existing.recipe_id
                skipped += 1
                continue

            # ── 조리방법 ID 변환 ──
            primary_method_id   = resolve_method_id(
                row.get("cooking_method_primary"), method_lookup, default_method_id
            )
            secondary_method_id = resolve_method_id(
                row.get("cooking_method_secondary"), method_lookup
            )

            # ── data_source 변환 (CHECK 제약 대응) ──
            raw_source = str(row.get("data_source", "")).strip()
            data_source = DATA_SOURCE_MAP.get(raw_source, "영양사협회")

            # ── serving_size ──
            try:
                ssize = int(float(row["serving_size"]))
            except (ValueError, TypeError):
                ssize = 1

            # ── is_verified ──
            is_verified = bool(row.get("is_verified")) if pd.notna(row.get("is_verified")) else False

            # ── notes: 원본 ID 보존 ──
            notes_val = marker

            # ── INSERT ──
            result = conn.execute(
                text("""
                    INSERT INTO recipe (
                        recipe_name, primary_method_id, secondary_method_id,
                        serving_size, serving_category, data_source,
                        is_verified, notes
                    ) VALUES (
                        :name, :pm, :sm,
                        :ssize, :scat, :dsrc,
                        :iv, :notes
                    )
                    RETURNING recipe_id
                """),
                {
                    "name":  str(row["recipe_name"]).strip(),
                    "pm":    primary_method_id,
                    "sm":    secondary_method_id,
                    "ssize": ssize,
                    "scat":  serving_category,
                    "dsrc":  data_source,
                    "iv":    is_verified,
                    "notes": notes_val,
                }
            )
            new_id = result.fetchone().recipe_id
            recipe_id_map[orig_id] = new_id
            inserted += 1

    # ── 적재 이력 기록 ──
    record_load_log(engine, file_path.name, file_hash, len(df))
    print(f"  신규 삽입: {inserted}건 / 중복 스킵: {skipped}건")
    return recipe_id_map


# ─────────────────────────────────────────────────────────────────
# 2. recipe_similarity 적재 (매칭쌍 CSV)
# ─────────────────────────────────────────────────────────────────
def load_recipe_similarity(engine, file_path: Path, recipe_id_map: dict):
    """
    소규모-대규모 레시피 매칭쌍 CSV를 recipe_similarity 테이블에 적재.
    - small_recipe_id, large_recipe_id: 문자열 → 정수 FK 변환
    - match_decision: 영문 값 → 한글 CHECK 값 변환
    - ON CONFLICT (small_recipe_id, large_recipe_id) DO NOTHING
    """
    if not check_file(file_path):
        return

    print(f"\n[2] recipe_similarity 적재: {file_path.name}")

    # ── 파일 해시 계산 및 중복 적재 확인 ──
    file_hash = compute_file_hash(file_path)
    print(f"  파일 해시 (SHA256): {file_hash[:16]}...")
    if is_already_loaded(engine, file_hash):
        print(f"  [스킵] 이미 적재된 버전입니다. (hash: {file_hash[:16]}...)")
        return

    # cp949 인코딩으로 읽기
    df = pd.read_csv(file_path, encoding="cp949")
    print(f"  파일 행 수: {len(df)}")

    inserted  = 0
    skipped   = 0
    no_recipe = 0

    rows_to_insert = []

    for _, row in df.iterrows():
        s_orig = str(row["small_recipe_id"]).strip()
        l_orig = str(row["large_recipe_id"]).strip()

        # 레시피 ID 매핑 확인
        s_id = recipe_id_map.get(s_orig)
        l_id = recipe_id_map.get(l_orig)
        if s_id is None or l_id is None:
            no_recipe += 1
            continue

        # match_decision 변환
        raw_decision = str(row.get("match_decision", "")).strip()
        decision = MATCH_DECISION_MAP.get(raw_decision, "수동검토")

        # is_manually_verified 처리
        raw_verified = row.get("is_manually_verified", False)
        is_verified  = bool(raw_verified) if pd.notna(raw_verified) else False

        rows_to_insert.append({
            "s_id":       s_id,
            "l_id":       l_id,
            "menu_score": float(row.get("menu_name_similarity_score", 0) or 0),
            "cos_score":  float(row.get("cosine_similarity_score",   0) or 0),
            "comp_score": float(row.get("composite_score",           0) or 0),
            "decision":   decision,
            "is_verified": is_verified,
        })

    # Bulk INSERT (chunksize=500)
    chunk_size = 500
    with engine.begin() as conn:
        for i in range(0, len(rows_to_insert), chunk_size):
            chunk = rows_to_insert[i:i + chunk_size]
            for rec in chunk:
                result = conn.execute(
                    text("""
                        INSERT INTO recipe_similarity (
                            small_recipe_id, large_recipe_id,
                            menu_name_similarity_score, cosine_similarity_score,
                            composite_score, match_decision, is_manually_verified
                        ) VALUES (
                            :s_id, :l_id,
                            :menu_score, :cos_score,
                            :comp_score, :decision, :is_verified
                        )
                        ON CONFLICT (small_recipe_id, large_recipe_id) DO NOTHING
                    """),
                    rec
                )
                if result.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1

    # ── 적재 이력 기록 ──
    record_load_log(engine, file_path.name, file_hash, len(df))
    print(f"  신규 삽입: {inserted}건 / 중복 스킵: {skipped}건 / 레시피 미매핑: {no_recipe}건")


# ─────────────────────────────────────────────────────────────────
# 3. ml_training_dataset 적재 (df_B.csv)
# ─────────────────────────────────────────────────────────────────
def load_ml_training_dataset(engine, file_path: Path, recipe_id_map: dict):
    """
    df_B.csv를 ml_training_dataset 테이블에 적재.
    - small_recipe_id → base_recipe_id (FK)
    - large_recipe_id → target_recipe_id (FK)
    - ingredient_name으로 ingredient 조회/생성 → ingredient_id
    - N → target_serving_size, base serving_size는 recipe 테이블에서 조회
    - 미검증 데이터로 기본 설정 (data_quality='미검증')
    - 이미 데이터 있으면 전체 스킵 (테이블 비어 있을 때만 적재)
    """
    if not check_file(file_path):
        return

    print(f"\n[3] ml_training_dataset 적재: {file_path.name}")

    # ── 파일 해시 계산 및 중복 적재 확인 ──
    file_hash = compute_file_hash(file_path)
    print(f"  파일 해시 (SHA256): {file_hash[:16]}...")
    if is_already_loaded(engine, file_hash):
        print(f"  [스킵] 이미 적재된 버전입니다. (hash: {file_hash[:16]}...)")
        return

    # 이미 데이터 있으면 스킵
    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM ml_training_dataset")).scalar()
        if cnt > 0:
            print(f"  이미 {cnt}건 존재. 중복 방지로 전체 스킵.")
            return

    df = pd.read_csv(file_path, encoding="utf-8-sig")
    print(f"  파일 행 수: {len(df)}")

    # recipe 테이블에서 serving_size 조회용 딕셔너리 구축
    # {db_recipe_id: serving_size}
    recipe_serving_map = {}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT recipe_id, serving_size FROM recipe")
        ).fetchall()
        for r in rows:
            recipe_serving_map[r.recipe_id] = r.serving_size

        method_lookup    = build_cooking_method_lookup(conn)
        default_method_id = min(method_lookup.values()) if method_lookup else 1

    ingredient_cache = {}  # {이름: ingredient_id} 캐시
    rows_to_insert = []
    skipped = 0

    for _, row in df.iterrows():
        s_orig = str(row["small_recipe_id"]).strip()
        l_orig = str(row["large_recipe_id"]).strip()

        base_recipe_id   = recipe_id_map.get(s_orig)
        target_recipe_id = recipe_id_map.get(l_orig)
        if base_recipe_id is None or target_recipe_id is None:
            skipped += 1
            continue

        # 조리방법 ID
        primary_method_id = resolve_method_id(
            row.get("cooking_method"), method_lookup, default_method_id
        )

        # serving_size
        base_serving   = recipe_serving_map.get(base_recipe_id, 1)
        target_serving = int(float(row["N"])) if pd.notna(row.get("N")) else 1

        # scaling_ratio: 대/소 인원 비율
        scaling_ratio = (target_serving / base_serving) if base_serving > 0 else float(row.get("N", 1))

        # ingredient 조회/생성 (트랜잭션 내에서 처리)
        ing_name = str(row["ingredient_name"]).strip() if pd.notna(row.get("ingredient_name")) else None
        if not ing_name:
            skipped += 1
            continue

        # group_type 변환
        raw_group   = str(row.get("group_type", "")).strip()
        eng_group   = GROUP_TYPE_MAP.get(raw_group, raw_group)
        method_cat  = GROUP_TYPE_TO_CATEGORY.get(eng_group)

        # ingredient_role 변환
        raw_role = str(row.get("role", "")).strip()
        ing_role = ROLE_MAP.get(raw_role, "부재료")

        # is_seasoning: role == 'seasoning'
        is_seasoning = (raw_role == "seasoning")

        rows_to_insert.append({
            "base_recipe_id":        base_recipe_id,
            "target_recipe_id":      target_recipe_id,
            "base_serving_size":     base_serving,
            "target_serving_size":   target_serving,
            "scaling_ratio":         round(scaling_ratio, 4),
            "ratio":                 float(row["ratio"]) if pd.notna(row.get("ratio")) else None,
            "ing_name":              ing_name,
            "ingredient_category":   row.get("derived_category") if pd.notna(row.get("derived_category")) else None,
            "is_seasoning":          is_seasoning,
            "ingredient_role":       ing_role,
            "base_amount_g":         float(row["base"]) if pd.notna(row.get("base")) else 0.0,
            "target_amount_g":       float(row["Y"])    if pd.notna(row.get("Y"))    else 0.0,
            "primary_method_id":     primary_method_id,
            "group_type":            eng_group or None,
            "cooking_method_category": method_cat,
            "has_mixed_cooking":     False,
            "data_quality":          "미검증",
            "is_training_set":       True,
        })

    # Bulk INSERT (chunksize=500)
    inserted = 0
    chunk_size = 500

    with engine.begin() as conn:
        for i in range(0, len(rows_to_insert), chunk_size):
            chunk = rows_to_insert[i:i + chunk_size]
            for rec in chunk:
                # 재료 조회/생성
                ing_id = get_or_create_ingredient(conn, rec["ing_name"], ingredient_cache)

                conn.execute(
                    text("""
                        INSERT INTO ml_training_dataset (
                            base_recipe_id, target_recipe_id,
                            base_serving_size, target_serving_size,
                            scaling_ratio, ratio,
                            ingredient_id, ingredient_category,
                            is_seasoning, ingredient_role,
                            base_amount_g, target_amount_g,
                            primary_method_id, group_type,
                            cooking_method_category, has_mixed_cooking,
                            data_quality, is_training_set
                        ) VALUES (
                            :base_recipe_id, :target_recipe_id,
                            :base_serving_size, :target_serving_size,
                            :scaling_ratio, :ratio,
                            :ing_id, :ingredient_category,
                            :is_seasoning, :ingredient_role,
                            :base_amount_g, :target_amount_g,
                            :primary_method_id, :group_type,
                            :cooking_method_category, :has_mixed_cooking,
                            :data_quality, :is_training_set
                        )
                    """),
                    {**rec, "ing_id": ing_id}
                )
                inserted += 1

    # ── 적재 이력 기록 ──
    record_load_log(engine, file_path.name, file_hash, len(df))
    print(f"  신규 삽입: {inserted}건 / 레시피 미매핑 스킵: {skipped}건")


# ─────────────────────────────────────────────────────────────────
# 4. ingredient_synonym 적재 (재료명 정규화 테이블 xlsx)
# ─────────────────────────────────────────────────────────────────
def load_ingredient_synonym(engine, file_path: Path):
    """
    재료명 정규화 테이블 xlsx를 ingredient_synonym 테이블에 적재.
    - 엑셀 구조: row0=메모, row1=컬럼명, row2~=데이터
    - 확정(Y)인 행만 적재
    - 최종 표준명으로 ingredient 조회/생성 → ingredient_id 확보
    - ON CONFLICT (synonym_name) DO NOTHING
    """
    if not check_file(file_path):
        return

    print(f"\n[4] ingredient_synonym 적재: {file_path.name}")

    # ── 파일 해시 계산 및 중복 적재 확인 ──
    file_hash = compute_file_hash(file_path)
    print(f"  파일 해시 (SHA256): {file_hash[:16]}...")
    if is_already_loaded(engine, file_hash):
        print(f"  [스킵] 이미 적재된 버전입니다. (hash: {file_hash[:16]}...)")
        return

    # 엑셀 구조: row0=색상 메모, row1=컬럼명, row2~=데이터
    df = pd.read_excel(file_path, header=1)
    df.columns = ["원본텍스트", "후보표준명", "생성방법", "신뢰도", "비고", "확정", "최종표준명"]

    # 확정=Y인 행만 필터링
    df = df[df["확정"].astype(str).str.strip().str.upper() == "Y"]
    df = df.dropna(subset=["원본텍스트", "최종표준명"])
    print(f"  확정(Y) 행 수: {len(df)}")

    inserted         = 0
    skipped          = 0
    ingredient_cache = {}  # {이름: ingredient_id}

    chunk_size = 500

    with engine.begin() as conn:
        for i in range(0, len(df), chunk_size):
            chunk = df.iloc[i:i + chunk_size]

            for _, row in chunk.iterrows():
                synonym_name = str(row["원본텍스트"]).strip()
                std_name     = str(row["최종표준명"]).strip()

                if not synonym_name or not std_name:
                    continue

                # 표준명으로 ingredient 조회/생성
                ing_id = get_or_create_ingredient(conn, std_name, ingredient_cache)

                # match_type 변환
                raw_method = str(row.get("생성방법", "")).strip()
                match_type = MATCH_TYPE_MAP.get(raw_method, "수동매핑")

                # 신뢰도 파싱 ('100%' → 1.00)
                raw_conf = str(row.get("신뢰도", "100%")).strip()
                try:
                    confidence = float(raw_conf.replace("%", "")) / 100.0
                    confidence = min(max(confidence, 0.0), 1.0)
                except ValueError:
                    confidence = 1.00

                result = conn.execute(
                    text("""
                        INSERT INTO ingredient_synonym (
                            ingredient_id, synonym_name,
                            match_type, confidence, created_by
                        ) VALUES (
                            :ing_id, :syn, :mtype, :conf, :creator
                        )
                        ON CONFLICT (synonym_name) DO NOTHING
                    """),
                    {
                        "ing_id":  ing_id,
                        "syn":     synonym_name,
                        "mtype":   match_type,
                        "conf":    round(confidence, 2),
                        "creator": "권성민",
                    }
                )
                if result.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1

    # ── 적재 이력 기록 ──
    record_load_log(engine, file_path.name, file_hash, len(df))
    print(f"  신규 삽입: {inserted}건 / 중복 스킵: {skipped}건")


# ─────────────────────────────────────────────────────────────────
# 적재 완료 후 각 테이블 행 수 출력
# ─────────────────────────────────────────────────────────────────
def print_row_counts(engine):
    """주요 테이블 행 수를 출력."""
    tables = [
        "recipe",
        "recipe_similarity",
        "ml_training_dataset",
        "ingredient_synonym",
        "ingredient",
    ]
    print("\n" + "=" * 45)
    print("  적재 완료 — 테이블 행 수")
    print("=" * 45)
    with engine.connect() as conn:
        for tbl in tables:
            cnt = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
            print(f"  {tbl:<25} {cnt:>8,} 행")
    print("=" * 45)


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────
def main():
    print("=" * 45)
    print("  OptiMeal 레시피 데이터 적재 스크립트")
    print("=" * 45)
    print(f"  데이터 경로: {DATA_DIR}")

    engine = get_engine()

    # ── data_load_log 테이블 보장 ──
    ensure_data_load_log_table(engine)

    # ── 현재 DB 스키마 버전 출력 ──
    db_schema_ver = get_schema_version(engine)
    print(f"  DB 스키마 버전: {db_schema_ver}")

    # ── 1. 소규모 레시피 xlsx 적재 ──
    small_map = load_recipes(
        engine,
        DATA_DIR / FILE_SMALL_RECIPE,
        serving_category="소규모",
    )

    # ── 2. 대규모 레시피 xlsx 적재 ──
    large_map = load_recipes(
        engine,
        DATA_DIR / FILE_LARGE_RECIPE,
        serving_category="대규모",
    )

    # 소규모 + 대규모 ID 매핑 통합
    recipe_id_map = {**small_map, **large_map}

    # ── 3. 매칭쌍 CSV → recipe_similarity ──
    load_recipe_similarity(
        engine,
        DATA_DIR / FILE_MATCHING,
        recipe_id_map,
    )

    # ── 4. df_B.csv → ml_training_dataset ──
    load_ml_training_dataset(
        engine,
        DATA_DIR / FILE_ML_DATASET,
        recipe_id_map,
    )

    # ── 5. 재료명 정규화 테이블 → ingredient_synonym ──
    load_ingredient_synonym(
        engine,
        DATA_DIR / FILE_INGREDIENT_NORM,
    )

    # ── 완료 후 행 수 출력 ──
    print_row_counts(engine)


if __name__ == "__main__":
    main()