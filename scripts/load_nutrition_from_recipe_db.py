#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_nutrition_from_recipe_db.py
목적: **API 호출 없이** 로컬 소규모 레시피 DB(xlsx)의 식품안전나라 행을
      `nutrition_recipe` 에 적재한다. 모듈 3 CSP 의 메뉴 후보 공급원을 만드는 것이 목적.

왜 이 스크립트가 필요한가
------------------------
`scripts/load_recipe_foodsafety.py` 는 식품안전나라 COOKRCP01 API 를 호출하므로
`FOODSAFETY_RECIPE_KEY` 가 필요하다. 그 키가 없어 **로컬 보유분으로 대체**하기로 했다
(2026-08-10 팀 결정). 다행히 `data/raw/소규모_레시피_DB_남유찬_v0_10.xlsx` 의
`data_source='식품안전나라'` 1,146행은 열량·탄수화물·단백질·지방·나트륨이 **100% 채워져 있어**
`nutrition_recipe` 가 요구하는 값을 그대로 공급할 수 있다.

`scripts/load_nutrition_mfds.py`(식약처 30만건)를 쓰면 안 되는 이유
      → 그 로더는 `menu_category` 를 `'기타'` 로 고정하는데, CSP 는
        `["주식","국","찌개","반찬"]` 만 후보로 조회한다(결정변수 폭발 방지) → 후보 0건.

실행:
  docker exec optimeal_app python scripts/load_nutrition_from_recipe_db.py --dry-run
  docker exec optimeal_app python scripts/load_nutrition_from_recipe_db.py

동작:
  · 멱등 — 이미 적재된 (recipe_name, data_source) 조합은 건너뛴다.
  · `recipe.notes` 의 `[orig:{xlsx recipe_id}]` 마커로 `recipe.nutrition_recipe_id` 를 연결한다.
  · `data_load_log` 에 파일 해시와 함께 기록한다(재현성).

⚠ 합성 데이터가 아니다 — 원본 xlsx의 실측 영양값을 테이블만 옮긴 것이다.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
XLSX_PATH = REPO_ROOT / "data" / "raw" / "소규모_레시피_DB_남유찬_v0_10.xlsx"

DATA_SOURCE = "식품안전나라"      # nutrition_recipe.data_source CHECK 허용값
SCHEMA_VERSION = "v1"

# grouping_type(xlsx) → nutrition_recipe.menu_category CHECK 7종
# CSP 가 실제로 쓰는 값은 주식/국/찌개/반찬 뿐이다.
MENU_CATEGORY_MAP = {
    "밥": "주식",
    "일품요리": "주식",
    "국": "국",
    "찌개": "찌개",
    "반찬": "반찬",
    "주찬": "반찬",
    "부찬": "반찬",
    "김치": "반찬",
    "후식": "후식",
    "음료": "음료",
}
MENU_CATEGORY_DEFAULT = "기타"

# xlsx 컬럼 → nutrition_recipe 컬럼
NUTRIENT_MAP = {
    "calories": "calories_kcal",
    "protein": "protein_g",
    "fat": "fat_g",
    "carbs": "carbohydrate_g",
    "sodium": "sodium_mg",
}

INSERT_SQL = text("""
    INSERT INTO nutrition_recipe
        (recipe_name, serving_size_g, calories, protein, fat, carbs, sodium,
         data_source, menu_category, original_data)
    VALUES
        (:recipe_name, :serving_size_g, :calories, :protein, :fat, :carbs, :sodium,
         :data_source, :menu_category, CAST(:original_data AS jsonb))
    RETURNING nutrition_id
""")


def get_engine():
    """DB 엔진. 접속 정보는 환경변수(POSTGRES_*)에서 읽는다 (다른 로더와 동일 패턴)."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "optimeal")
    user = os.getenv("POSTGRES_USER", "optimeal_user")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}")


def load_source_rows() -> pd.DataFrame:
    """xlsx 에서 식품안전나라 + 열량 보유 행만 추린다.

    Returns:
        정제된 DataFrame (recipe_id·recipe_name·grouping_type·영양 컬럼 포함).

    Raises:
        FileNotFoundError: 원본 xlsx 부재.
    """
    if not XLSX_PATH.exists():
        raise FileNotFoundError(f"원본 파일 없음: {XLSX_PATH}")
    df = pd.read_excel(XLSX_PATH, sheet_name=0, header=1)
    df = df[df["recipe_id"].notna() & (df["recipe_id"].astype(str) != "레시피 ID(임시)")]
    df = df[df["data_source"] == DATA_SOURCE].copy()
    df["calories_kcal"] = pd.to_numeric(df["calories_kcal"], errors="coerce")
    return df[df["calories_kcal"].notna() & (df["calories_kcal"] > 0)]


def to_params(row) -> dict:
    """xlsx 한 행 → INSERT 파라미터."""
    def num(col):
        v = pd.to_numeric(row.get(col), errors="coerce")
        return None if pd.isna(v) else float(v)

    grouping = str(row.get("grouping_type") or "").strip()
    return {
        "recipe_name": str(row["recipe_name"]).strip()[:200],
        "serving_size_g": num("person_serving_amount"),
        "calories": num("calories_kcal"),
        "protein": num("protein_g"),
        "fat": num("fat_g"),
        "carbs": num("carbohydrate_g"),
        "sodium": num("sodium_mg"),
        "data_source": DATA_SOURCE,
        "menu_category": MENU_CATEGORY_MAP.get(grouping, MENU_CATEGORY_DEFAULT),
        "original_data": json.dumps(
            {"orig_recipe_id": str(row["recipe_id"]), "grouping_type": grouping,
             "source_file": XLSX_PATH.name},
            ensure_ascii=False,
        ),
    }


def existing_names(conn) -> set:
    """이미 적재된 (recipe_name) 집합 — 멱등 실행용."""
    rows = conn.execute(
        text("SELECT recipe_name FROM nutrition_recipe WHERE data_source = :s"),
        {"s": DATA_SOURCE},
    ).scalars().all()
    return set(rows)


def link_recipe(conn, orig_id: str, nutrition_id: int) -> int:
    """`recipe.notes` 의 `[orig:{id}]` 마커로 recipe 행에 영양 id 를 연결한다.

    Returns:
        연결된 recipe 행 수.
    """
    res = conn.execute(text("""
        UPDATE recipe SET nutrition_recipe_id = :nid
        WHERE notes LIKE :marker AND nutrition_recipe_id IS NULL
    """), {"nid": nutrition_id, "marker": f"%[orig:{orig_id}]%"})
    return res.rowcount or 0


def write_load_log(conn, row_count: int) -> None:
    """data_load_log 에 기록(재현성 추적).

    `file_hash` 에 UNIQUE 제약이 있고 이 xlsx 는 이미 `recipe` 적재(2026-04-11)로 기록돼 있다.
    같은 파일을 **다른 대상 테이블로** 적재하는 별개 사건이므로, 파일 내용 + 대상 라벨을
    함께 해싱해 구분한다(내용 기반 추적성은 유지).
    """
    digest = hashlib.sha256(
        XLSX_PATH.read_bytes() + b"|target=nutrition_recipe"
    ).hexdigest()
    conn.execute(text("""
        INSERT INTO data_load_log (file_name, file_hash, row_count, schema_version)
        VALUES (:f, :h, :n, :v)
        ON CONFLICT (file_hash) DO NOTHING
    """), {"f": f"{XLSX_PATH.name} → nutrition_recipe({DATA_SOURCE})",
           "h": digest, "n": row_count, "v": SCHEMA_VERSION})


def main() -> int:
    """적재 진입점."""
    ap = argparse.ArgumentParser(description="로컬 xlsx → nutrition_recipe 적재 (API 미사용)")
    ap.add_argument("--dry-run", action="store_true", help="집계만 출력하고 적재하지 않음")
    args = ap.parse_args()

    df = load_source_rows()
    print(f"[원본] {XLSX_PATH.name} · {DATA_SOURCE} · 열량 보유 {len(df)}행")
    cats = df["grouping_type"].map(lambda g: MENU_CATEGORY_MAP.get(str(g).strip(),
                                                                  MENU_CATEGORY_DEFAULT))
    print("[분류] " + " · ".join(f"{k}:{v}" for k, v in cats.value_counts().items()))
    csp_usable = int(cats.isin(["주식", "국", "찌개", "반찬"]).sum())
    print(f"[CSP] 후보로 쓸 수 있는 카테고리 합계: {csp_usable}건")

    if args.dry_run:
        print("[dry-run] 적재하지 않고 종료")
        return 0

    engine = get_engine()
    inserted = skipped = linked = 0
    with engine.begin() as conn:
        seen = existing_names(conn)
        for _, row in df.iterrows():
            params = to_params(row)
            if params["recipe_name"] in seen:
                skipped += 1
                continue
            nid = conn.execute(INSERT_SQL, params).scalar_one()
            seen.add(params["recipe_name"])
            inserted += 1
            linked += link_recipe(conn, str(row["recipe_id"]), nid)
        if inserted:
            write_load_log(conn, inserted)

    print(f"[완료] 적재 {inserted}건 · 중복 skip {skipped}건 · recipe 연결 {linked}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
