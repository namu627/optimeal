#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_ingredients_from_recipe_db.py
목적: 로컬 소규모 레시피 DB(xlsx)의 재료 슬롯(ingredient_1~28)을 `ingredient` +
      `recipe_ingredient_map` 에 적재한다. **원가(cost_won) 계산 경로를 연결**하는 것이 목적.

배경 (Q-2)
---------
모듈 3 CSP 의 `MenuItem.cost_won` 은
    SUM(ingredient_price.price_per_g × recipe_ingredient_map.per_serving_grams)
로 계산되는데, `ingredient`·`recipe_ingredient_map`·`ingredient_price` 세 테이블이 모두
비어 있어 전 후보의 원가가 0원이었다 → 예산 Hard 제약이 항상 만족(무의미)했다.

이 스크립트는 그중 **로컬에서 채울 수 있는 두 테이블**을 채운다.
⚠ `ingredient_price` 는 가락시장 API(GARAK_ID/GARAK_PASSWD)가 필요하며 키가 없어 **미해결**이다.
   따라서 이 스크립트만으로는 cost_won 이 여전히 0 이다. 단가가 확보되는 순간
   조인이 성립하도록 배선을 미리 완성해 두는 것이 이 스크립트의 역할이다.

실행:
  docker exec optimeal_app python scripts/load_ingredients_from_recipe_db.py --dry-run
  docker exec optimeal_app python scripts/load_ingredients_from_recipe_db.py

멱등성: 이미 매핑이 있는 recipe_id 는 건너뛴다. `ingredient` 는 이름 기준 upsert.
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_nutrition_from_recipe_db import XLSX_PATH, get_engine  # noqa: E402

DATA_SOURCE = "식품안전나라"
# xlsx 의 role 표기는 recipe_ingredient_map.ingredient_role CHECK 와 이미 일치한다.
VALID_ROLES = {"주재료", "부재료", "조미료", "양념"}
SEASONING_ROLES = {"조미료", "양념"}


def ingredient_slots(columns) -> list[int]:
    """xlsx 컬럼에서 재료 슬롯 인덱스를 뽑는다."""
    return sorted({int(m.group(1)) for c in columns
                   if (m := re.match(r"ingredient_(\d+)_name", str(c)))})


def load_rows() -> pd.DataFrame:
    """식품안전나라 레시피 행을 읽는다.

    Raises:
        FileNotFoundError: 원본 xlsx 부재.
    """
    if not XLSX_PATH.exists():
        raise FileNotFoundError(f"원본 파일 없음: {XLSX_PATH}")
    df = pd.read_excel(XLSX_PATH, sheet_name=0, header=1)
    df = df[df["recipe_id"].notna() & (df["recipe_id"].astype(str) != "레시피 ID(임시)")]
    return df[df["data_source"] == DATA_SOURCE].copy()


def flatten(df: pd.DataFrame) -> pd.DataFrame:
    """가로형 재료 슬롯(28개)을 세로형 (orig_recipe_id, 재료명, 양, 단위, 역할) 로 펼친다."""
    slots = ingredient_slots(df.columns)
    recs = []
    for _, row in df.iterrows():
        for i in slots:
            name = row.get(f"ingredient_{i}_name")
            if pd.isna(name) or not str(name).strip():
                continue
            amount = pd.to_numeric(row.get(f"ingredient_{i}_amount"), errors="coerce")
            role = str(row.get(f"ingredient_{i}_role") or "부재료").strip()
            recs.append({
                "orig_recipe_id": str(row["recipe_id"]),
                "name": str(name).strip()[:100],
                "amount": None if pd.isna(amount) else float(amount),
                "unit": str(row.get(f"ingredient_{i}_unit") or "g").strip()[:20],
                "role": role if role in VALID_ROLES else "부재료",
                "step": i,
            })
    return pd.DataFrame(recs)


def upsert_ingredients(conn, names_roles: dict) -> dict:
    """재료명 → ingredient_id 매핑을 만든다(없으면 INSERT).

    Args:
        names_roles: {재료명: 대표 role}.

    Returns:
        {재료명: ingredient_id}.
    """
    existing = {r[0]: r[1] for r in conn.execute(
        text("SELECT ingredient_name, ingredient_id FROM ingredient")).all()}
    for name, role in names_roles.items():
        if name in existing:
            continue
        iid = conn.execute(text("""
            INSERT INTO ingredient (ingredient_name, standard_unit, is_seasoning, data_source)
            VALUES (:n, 'g', :s, :src) RETURNING ingredient_id
        """), {"n": name, "s": role in SEASONING_ROLES, "src": DATA_SOURCE}).scalar_one()
        existing[name] = iid
    return existing


def recipe_id_map(conn) -> dict:
    """`recipe.notes` 의 `[orig:{id}]` 마커 → recipe_id 매핑."""
    rows = conn.execute(text(
        "SELECT recipe_id, notes FROM recipe WHERE notes LIKE '[orig:%'")).all()
    out = {}
    for rid, notes in rows:
        m = re.search(r"\[orig:([^\]]+)\]", notes or "")
        if m:
            out.setdefault(m.group(1), rid)
    return out


def main() -> int:
    """적재 진입점."""
    ap = argparse.ArgumentParser(description="로컬 xlsx → ingredient / recipe_ingredient_map")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    long_df = flatten(load_rows())
    print(f"[원본] {DATA_SOURCE} 재료 행 {len(long_df)}건 · 고유 재료명 "
          f"{long_df['name'].nunique()}종 · 양(g) 보유 {long_df['amount'].notna().sum()}건")
    if args.dry_run:
        print("[dry-run] 적재하지 않고 종료")
        return 0

    engine = get_engine()
    with engine.begin() as conn:
        rid_map = recipe_id_map(conn)
        rep_role = long_df.groupby("name")["role"].agg(lambda s: s.mode().iat[0]).to_dict()
        ing_map = upsert_ingredients(conn, rep_role)
        done = {r[0] for r in conn.execute(text(
            "SELECT DISTINCT recipe_id FROM recipe_ingredient_map")).all()}

        inserted = skipped_recipe = missing_recipe = skipped_amount = 0
        for orig_id, grp in long_df.groupby("orig_recipe_id"):
            rid = rid_map.get(orig_id)
            if rid is None:
                missing_recipe += 1
                continue
            if rid in done:
                skipped_recipe += 1
                continue
            for _, r in grp.iterrows():
                # ⚠ `is None` 으로 검사하면 안 된다 — DataFrame 의 float 컬럼에 담긴 None 은
                #   NaN 으로 변환되고, NaN 은 PostgreSQL numeric 이 그대로 받아들여
                #   `amount > 0` 조차 참이 되는 오염 행을 만든다(2026-08-10 실제 334행 발생).
                if pd.isna(r["amount"]) or float(r["amount"]) <= 0:
                    skipped_amount += 1
                    continue
                conn.execute(text("""
                    INSERT INTO recipe_ingredient_map
                        (recipe_id, ingredient_id, amount, unit, amount_in_grams,
                         per_serving_grams, ingredient_role, cooking_step_order)
                    VALUES (:rid, :iid, :amt, :unit, :amt, :amt, :role, :step)
                """), {"rid": rid, "iid": ing_map[r["name"]], "amt": r["amount"],
                       "unit": r["unit"], "role": r["role"], "step": int(r["step"])})
                inserted += 1

    print(f"[완료] ingredient {len(ing_map)}종 · 매핑 {inserted}행 적재")
    print(f"        recipe 기존매핑 skip {skipped_recipe} · recipe 미발견 {missing_recipe} "
          f"· 양 미기재 skip {skipped_amount}")
    print("⚠ cost_won 은 여전히 0원이다 — ingredient_price(가락시장 API 키 필요)가 비어 있다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
