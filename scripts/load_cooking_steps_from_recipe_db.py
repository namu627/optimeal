#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_cooking_steps_from_recipe_db.py
목적: 로컬 소규모 레시피 DB(xlsx)의 **조리 과정(cooking_step_1~11)** 을, 같은 xlsx 에서
      `load_nutrition_from_recipe_db.py` 로 적재한 `nutrition_recipe` 행에 붙인다.

왜 필요한가
-----------
레시피 화면(module_4 `/api/menu/generate`·`/api/menu/recipes`)은 조리 순서를
`nutrition_recipe.original_data` 의 `MANUAL01~20`(식품안전나라 COOKRCP01 원본 키)에서 읽는다.
API 로 적재한 행은 이 키가 있지만, xlsx 로 적재한 식품안전나라 행(약 300개)은
`original_data` 에 `orig_recipe_id` 등 추적 키만 넣어 조리 순서가 비어 있었다.
xlsx 의 cooking_step_N 열이 바로 그 원본 조리 과정이므로, 같은 키 형식(MANUAL01..)으로
옮기면 백엔드 수정 없이 화면에 뜬다.

실행 (load_nutrition_from_recipe_db.py 다음):
  docker exec optimeal_app python scripts/load_cooking_steps_from_recipe_db.py --dry-run
  docker exec optimeal_app python scripts/load_cooking_steps_from_recipe_db.py

동작:
  · 대상: original_data.source_file 이 이 xlsx 인 nutrition_recipe 행만. API 적재 행은 건드리지 않는다.
  · 매칭: original_data.orig_recipe_id ↔ xlsx recipe_id (메뉴명 일치도 함께 확인, 불일치는 건너뜀).
  · cooking_step_N → MANUAL{N:02d}. 원문 문자열을 그대로 넣는다(다듬기·보충 없음). 빈 칸은 넣지 않는다.
  · 출처 표시: original_data.manual_source = "{xlsx}:cooking_step_1..11".
  · 멱등 — 기존 MANUAL* 키를 지우고 다시 쓰므로 여러 번 실행해도 결과가 같다.
  · data_load_log 에 파일 해시(+대상 라벨)로 기록한다.

⚠ 합성 데이터가 아니다 — 원본 xlsx 의 조리 과정 텍스트를 옮긴 것이다.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
XLSX_PATH = REPO_ROOT / "data" / "raw" / "소규모_레시피_DB_남유찬_v0_10.xlsx"
STEP_COLS = [f"cooking_step_{i}" for i in range(1, 12)]   # xlsx 조리과정1~10 + 조리단계11
MANUAL_KEY = re.compile(r"^MANUAL\d+$")
SCHEMA_VERSION = "v1"


def get_engine():
    """DB 엔진. 접속 정보는 환경변수(POSTGRES_*)에서 읽는다 (다른 로더와 동일 패턴)."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "optimeal")
    user = os.getenv("POSTGRES_USER", "optimeal_user")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}")


def load_steps() -> dict[str, tuple[str, dict[str, str]]]:
    """xlsx recipe_id -> (레시피명, {MANUALnn: 원문}).

    Raises:
        FileNotFoundError: 원본 xlsx 부재.
    """
    if not XLSX_PATH.exists():
        raise FileNotFoundError(f"원본 파일 없음: {XLSX_PATH}")
    df = pd.read_excel(XLSX_PATH, sheet_name=0, header=1, dtype={c: object for c in STEP_COLS})
    df = df[df["recipe_id"].notna() & (df["recipe_id"].astype(str) != "레시피 ID(임시)")]
    out = {}
    for _, row in df.iterrows():
        manual = {}
        for n, col in enumerate(STEP_COLS, start=1):
            v = row.get(col)
            if isinstance(v, str) and v.strip():
                manual[f"MANUAL{n:02d}"] = v
        out[str(row["recipe_id"])] = (str(row["recipe_name"]).strip()[:200], manual)
    return out


def target_rows(conn) -> list:
    """이 xlsx 에서 적재된 nutrition_recipe 행."""
    return conn.execute(text("""
        SELECT nutrition_id, recipe_name, original_data
        FROM nutrition_recipe
        WHERE original_data->>'source_file' = :f
        ORDER BY nutrition_id
    """), {"f": XLSX_PATH.name}).mappings().all()


def write_load_log(conn, row_count: int) -> None:
    """data_load_log 기록. 같은 xlsx 의 다른 적재와 구분하려고 대상 라벨을 함께 해싱한다."""
    digest = hashlib.sha256(
        XLSX_PATH.read_bytes() + b"|target=nutrition_recipe.original_data.MANUAL"
    ).hexdigest()
    conn.execute(text("""
        INSERT INTO data_load_log (file_name, file_hash, row_count, schema_version)
        VALUES (:f, :h, :n, :v)
        ON CONFLICT (file_hash) DO NOTHING
    """), {"f": f"{XLSX_PATH.name} → nutrition_recipe.original_data(MANUAL 조리과정)",
           "h": digest, "n": row_count, "v": SCHEMA_VERSION})


def main() -> int:
    """적재 진입점."""
    ap = argparse.ArgumentParser(description="xlsx 조리 과정 → nutrition_recipe.original_data(MANUALnn)")
    ap.add_argument("--dry-run", action="store_true", help="매칭·집계만 출력하고 적재하지 않음")
    args = ap.parse_args()

    steps = load_steps()
    engine = get_engine()
    updated = no_match = name_mismatch = no_steps = 0
    per_count: dict[int, int] = {}
    with engine.begin() as conn:
        rows = target_rows(conn)
        for r in rows:
            data = dict(r["original_data"] or {})
            src = steps.get(str(data.get("orig_recipe_id")))
            if src is None:
                no_match += 1
                continue
            name, manual = src
            if name != r["recipe_name"]:
                name_mismatch += 1
                print(f"[건너뜀] 이름 불일치 nid={r['nutrition_id']} db='{r['recipe_name']}' xlsx='{name}'")
                continue
            if not manual:
                no_steps += 1
                continue
            per_count[len(manual)] = per_count.get(len(manual), 0) + 1
            new = {k: v for k, v in data.items() if not MANUAL_KEY.match(k)}
            new.update(manual)
            new["manual_source"] = f"{XLSX_PATH.name}:cooking_step_1..11"
            if not args.dry_run:
                conn.execute(text("UPDATE nutrition_recipe SET original_data = CAST(:d AS jsonb) WHERE nutrition_id = :nid"),
                             {"d": json.dumps(new, ensure_ascii=False), "nid": r["nutrition_id"]})
            updated += 1
        if not args.dry_run and updated:
            write_load_log(conn, updated)

    print(f"[원본] {XLSX_PATH.name} · 대상 nutrition_recipe {len(rows)}행")
    print(f"[매칭] 조리 과정 {'적재 예정' if args.dry_run else '적재'} {updated}건 · 원본에 단계 없음 {no_steps}건 · "
          f"xlsx 미매칭 {no_match}건 · 이름 불일치 {name_mismatch}건")
    print("[단계 수] " + " · ".join(f"{k}단계:{v}" for k, v in sorted(per_count.items())))
    if args.dry_run:
        print("[dry-run] 적재하지 않고 종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
