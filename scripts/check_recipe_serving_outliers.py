#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: check_recipe_serving_outliers.py
목적: 식단 후보 메뉴의 1인분 재료량(recipe_ingredient_map.per_serving_grams) 이상치를 목록으로 낸다. **DB 는 고치지 않는다.**
      원본(식품안전나라 xlsx·RCP_PARTS_DTLS)에 그 값이 적혀 있으면 원본 이상치이므로 목록으로만 남기고,
      로더 버그로 생긴 값이면 로더를 고쳐 다시 적재한다(2026-10-01: 여러 인분 레시피를 인분으로 나누지 않던
      load_ingredients_from_recipe_db.py 버그 → --reload-multi-serving 으로 재적재).

실행:
  docker exec optimeal_app python scripts/check_recipe_serving_outliers.py
  → data/external/recipe_serving_outliers_YYYYMMDD.csv

규칙(1인분 기준):
  - 곡물 150g 초과(한 재료) / 곡물 여러 개 합계 150g 초과(예: 무 현미밥 현미 140 + 찹쌀 140)
  - 튀김·식용 기름 50g 초과(튀김유를 흡수량이 아닌 투입량으로 적은 레시피)
  - 양념이 가장 많은 비양념 재료보다 많음(10g 이상)
  - 양념이 원본 역할 '주재료' 재료보다 많음(10g 이상, 예: 배추겉절이 고춧가루 25g > 겉절이 15g)
"""

import csv
import re
from collections import Counter
from datetime import date

from sqlalchemy import text

from load_ingredient_price import UNMATCHED_LOG_DIR, get_engine

CATS = ["주식", "국", "찌개", "주찬", "부찬", "김치", "반찬"]
GRAIN = re.compile(r"^(쌀|멥쌀|찹쌀|현미|흑미|보리|보리쌀|찰보리|귀리|잡곡|수수|기장|율무|쌀\(.*\)|불린.*쌀|오트밀)$")
SEASON = re.compile(r"고춧가루|소금|간장|설탕|된장|고추장|쌈장|액젓|젓$|식초|올리고당|물엿|참기름|들기름|식용유|올리브유|"
                    r"카놀라유|굴소스|케첩|마요네즈|후추|깨$|참깨|통깨|청$|매실청|꿀")
OIL = re.compile(r"튀김기름|식용유|카놀라유|포도씨유|콩기름")
QUERY = """
SELECT nr.nutrition_id, nr.recipe_name AS menu, r.serving_size, r.notes, i.ingredient_name AS ing,
       rim.per_serving_grams AS g, rim.ingredient_role AS role
FROM nutrition_recipe nr JOIN recipe r ON r.nutrition_recipe_id = nr.nutrition_id
JOIN recipe_ingredient_map rim ON rim.recipe_id = r.recipe_id JOIN ingredient i ON i.ingredient_id = rim.ingredient_id
WHERE nr.menu_category = ANY(:cats) AND rim.per_serving_grams IS NOT NULL
"""


def find_outliers(rows) -> list[tuple]:
    by: dict = {}
    for r in rows:
        by.setdefault((r["nutrition_id"], r["menu"], r["serving_size"], r["notes"]), []).append(r)
    out = []
    for (nid, menu, sv, notes), items in by.items():
        g = {id(x): float(x["g"]) for x in items}
        base = [x for x in items if not SEASON.search(x["ing"]) and not OIL.search(x["ing"])]
        top = max(base, key=lambda x: g[id(x)], default=None)
        mains = [x for x in items if x["role"] == "주재료" and not SEASON.search(x["ing"])]
        main_top = max(mains, key=lambda x: g[id(x)], default=None)
        grains = [x for x in items if GRAIN.match(x["ing"])]
        gsum = sum(g[id(x)] for x in grains)
        if len(grains) > 1 and gsum > 150 and all(g[id(x)] <= 150 for x in grains):
            out.append((nid, menu, notes, sv, "곡물 합계 150g 초과", "+".join(x["ing"] for x in grains), gsum, ""))
        for x in items:
            v = g[id(x)]
            if GRAIN.match(x["ing"]) and v > 150:
                out.append((nid, menu, notes, sv, "곡물 150g 초과", x["ing"], v, ""))
            if OIL.search(x["ing"]):
                if v > 50:
                    out.append((nid, menu, notes, sv, "기름 50g 초과", x["ing"], v, ""))
                continue
            if not SEASON.search(x["ing"]) or v < 10:
                continue
            if top is not None and v > g[id(top)]:
                out.append((nid, menu, notes, sv, "양념 > 가장 많은 비양념 재료", x["ing"], v, f"{top['ing']} {g[id(top)]:g}g"))
            elif main_top is not None and v > g[id(main_top)]:
                out.append((nid, menu, notes, sv, "양념 > 주재료(원본 역할)", x["ing"], v, f"{main_top['ing']} {g[id(main_top)]:g}g"))
    return sorted(out, key=lambda r: (r[4], -r[6]))


def main():
    with get_engine().connect() as conn:
        rows = conn.execute(text(QUERY), {"cats": CATS}).mappings().all()
    out = find_outliers(rows)
    UNMATCHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = UNMATCHED_LOG_DIR / f"recipe_serving_outliers_{date.today():%Y%m%d}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["nutrition_id", "메뉴", "원본(notes)", "인분", "유형", "재료", "1인분 g", "비교 재료"])
        w.writerows(out)
    print(f"이상치 {len(out)}행 · 메뉴 {len({r[0] for r in out})}개 {dict(Counter(r[4] for r in out))}")
    print(f"목록: {path}")


if __name__ == "__main__":
    main()
