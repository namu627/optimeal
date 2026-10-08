#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_ingredient_nutrient.py
목적: 재료(ingredient_id) → 100g당 열량·당류·인·칼륨을 ingredient_nutrient 에 적재한다(기저질환 대체식용).

입력: data/manual/ingredient_nutrient_map.csv (저장소에 커밋, 사람이 검수하는 원본)
  ingredient_id,ingredient_name,energy_kcal,sugar_g,phosphorus_mg,potassium_mg,basis,source_name,food_cd,review,spec,note
  · 값은 식약처 식품영양성분DB(FoodNtrCpntDbInfo02 — AMT_NUM1 열량 · AMT_NUM7 당류 · AMT_NUM11 인 · AMT_NUM12 칼륨,
    100g당). 열량은 메뉴 계산 때 1인분 양을 메뉴 공식 열량에 맞추는 데 쓴다(module_3/src/disease_diet.py).
  · basis: 원재료(원재료성 식품) / 음식(음식 DB — 김치·육수류) / 가공품중앙값(같은 이름 가공식품들의 중앙값 —
    간장·고추장 등 양념) / 영양없음(물·얼음 — 모든 값 0)
  · review: 검수(사람이 식품을 지정 — spec 에 지정 내용) / 자동(이름 토큰이 일치한 원재료·생것 우선)
  · 빈 칸 = 원본에 값 없음. 0 으로 채우지 않는다(메뉴 계산 시 그 재료는 '모름'으로 커버리지에서 빠진다).
    원본이 비었을 때 같은 품목(예: 전복류) 생것 행 중앙값으로 보충한 경우는 note 에 적었다.
  · CSV 에 없는 재료 = 미매핑. 2026-10-07 기준 후보 메뉴 재료 사용량(g)의 칼륨 95.7% · 인 95.5% · 당류 93.2% 가
    값을 가진다(물 등 영양없음 포함, 열량은 96.7%).

적재 규칙:
  · CSV 의 (ingredient_id, ingredient_name) 이 DB 와 하나라도 다르면 아무것도 쓰지 않고 멈춘다(부분 적재 금지).
  · 멱등: --apply 마다 테이블을 비우고 CSV 대로 다시 만든다.
  · 사전 조건: migrations/v6_ingredient_nutrient.sql 적용.

실행:
  docker exec optimeal_app python scripts/load_ingredient_nutrient.py           # 검증·요약만(DB 미반영)
  docker exec optimeal_app python scripts/load_ingredient_nutrient.py --apply   # 적재
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_nutrition_from_recipe_db import get_engine  # noqa: E402

MAP_CSV = REPO_ROOT / "data" / "manual" / "ingredient_nutrient_map.csv"
BASIS = {"원재료", "음식", "가공품중앙값", "영양없음"}
NUTRIENTS = ("energy_kcal", "sugar_g", "phosphorus_mg", "potassium_mg")

DELETE_SQL = text("DELETE FROM ingredient_nutrient")
INSERT_SQL = text("""
    INSERT INTO ingredient_nutrient
        (ingredient_id, energy_kcal, sugar_g, phosphorus_mg, potassium_mg, basis, source_name, food_cd)
    VALUES (:iid, :energy_kcal, :sugar_g, :phosphorus_mg, :potassium_mg, :basis, :source_name, :food_cd)
""")
# 식단 후보 메뉴의 영양소별 커버리지: 1인분 재료 g 중 값이 있는 재료 g 비율이 0.8 이상인 메뉴 수.
#   (module_3/src/disease_diet.py 의 메뉴 계산과 같은 기준 — 0.8 미만이면 그 영양소는 '모름')
COVERAGE_SQL = text("""
    WITH g AS (
        SELECT nr.nutrition_id,
               SUM(rim.per_serving_grams) AS total,
               SUM(rim.per_serving_grams) FILTER (WHERE n.sugar_g IS NOT NULL)       AS s,
               SUM(rim.per_serving_grams) FILTER (WHERE n.phosphorus_mg IS NOT NULL) AS p,
               SUM(rim.per_serving_grams) FILTER (WHERE n.potassium_mg IS NOT NULL)  AS k
        FROM nutrition_recipe nr
        JOIN recipe r ON r.nutrition_recipe_id = nr.nutrition_id
        JOIN recipe_ingredient_map rim ON rim.recipe_id = r.recipe_id AND rim.per_serving_grams > 0
        LEFT JOIN ingredient_nutrient n ON n.ingredient_id = rim.ingredient_id
        WHERE nr.menu_category IN ('주식','국','찌개','주찬','부찬','김치','반찬')
        GROUP BY nr.nutrition_id
    )
    SELECT count(*) AS menus,
           count(*) FILTER (WHERE COALESCE(s, 0) >= 0.8 * total) AS sugar,
           count(*) FILTER (WHERE COALESCE(p, 0) >= 0.8 * total) AS phosphorus,
           count(*) FILTER (WHERE COALESCE(k, 0) >= 0.8 * total) AS potassium
    FROM g
""")


def _num(raw: str, line: int, col: str, errors: list) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        errors.append(f"{line}행: {col} '{raw}' 가 숫자가 아님")
        return None
    if v < 0:
        errors.append(f"{line}행: {col} 가 음수({v})")
    return v


def read_map(path: Path) -> list[dict]:
    """CSV 를 읽고 형식만 검증한다(DB 대조는 check_against_db)."""
    rows, errors, seen = [], [], set()
    with path.open(encoding="utf-8", newline="") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            try:
                iid = int(r["ingredient_id"])
            except ValueError:
                errors.append(f"{n}행: ingredient_id '{r['ingredient_id']}' 가 정수가 아님")
                continue
            if iid in seen:
                errors.append(f"{n}행: ingredient_id {iid} 중복")
            seen.add(iid)
            basis = r["basis"].strip()
            if basis not in BASIS:
                errors.append(f"{n}행: basis '{basis}' 는 {sorted(BASIS)} 중 하나여야 함")
            vals = {c: _num(r[c], n, c, errors) for c in NUTRIENTS}
            if all(v is None for v in vals.values()):
                errors.append(f"{n}행: 값이 모두 비어 있음 — 매핑할 수 없으면 행을 빼 둘 것")
            rows.append({"line": n, "iid": iid, "name": r["ingredient_name"].strip(), "basis": basis,
                         "source_name": r["source_name"].strip() or None,
                         "food_cd": r["food_cd"].strip() or None, **vals})
    if errors:
        raise SystemExit("[중단] 매핑 CSV 형식 오류\n  " + "\n  ".join(errors))
    return rows


def check_against_db(conn, rows: list[dict]) -> None:
    """CSV 의 id·이름이 DB ingredient 와 정확히 같은지 확인한다. 하나라도 다르면 중단."""
    names = dict(conn.execute(text("SELECT ingredient_id, ingredient_name FROM ingredient")).all())
    bad = [f"{r['line']}행 id={r['iid']}: CSV '{r['name']}' ↔ DB '{names.get(r['iid'], '(없음)')}'"
           for r in rows if names.get(r["iid"]) != r["name"]]
    if bad:
        raise SystemExit("[중단] DB 재료와 맞지 않는 행 — CSV 를 고친 뒤 다시 실행\n  " + "\n  ".join(bad))


def print_summary(rows: list[dict]) -> None:
    basis = Counter(r["basis"] for r in rows)
    print(f"[매핑] 재료 {len(rows)}종 — " + " · ".join(f"{k} {v}" for k, v in basis.most_common()))
    for c in NUTRIENTS:
        print(f"  {c:<14} 값 있음 {sum(r[c] is not None for r in rows)}종")


def main() -> int:
    ap = argparse.ArgumentParser(description="재료 영양성분(당류·인·칼륨)을 ingredient_nutrient 에 적재")
    ap.add_argument("--apply", action="store_true", help="실제 적재(기본은 검증·요약만)")
    args = ap.parse_args()

    rows = read_map(MAP_CSV)
    engine = get_engine()
    with engine.connect() as conn:
        check_against_db(conn, rows)
    print_summary(rows)

    if not args.apply:
        print("\n[dry-run] DB 대조 통과. 반영하지 않고 종료 — 실제 적재는 --apply")
        return 0

    with engine.begin() as conn:
        deleted = conn.execute(DELETE_SQL).rowcount
        for r in rows:
            conn.execute(INSERT_SQL, {"iid": r["iid"], "basis": r["basis"], "source_name": r["source_name"],
                                      "food_cd": r["food_cd"], **{c: r[c] for c in NUTRIENTS}})
    print(f"\n[완료] 기존 {deleted}행 삭제 → {len(rows)}행 적재")

    with engine.connect() as conn:
        cov = conn.execute(COVERAGE_SQL).mappings().one()
    print(f"[후보 메뉴 {cov['menus']}개 중 값 계산 가능(재료 g 80% 이상)] "
          f"당류 {cov['sugar']} · 인 {cov['phosphorus']} · 칼륨 {cov['potassium']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
