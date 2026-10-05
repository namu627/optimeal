#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_allergen_constraints.py
목적: 알레르기 표시 대상(NEIS 급식 코드 19종) → 재료(ingredient_id) 매핑을 constraints 에 적재한다.

입력: data/manual/allergen_ingredient_map.csv (저장소에 커밋, 사람이 검수하는 원본)
  code,allergen,ingredient_id,ingredient_name,basis,note
  · allergen 은 19종 표준 이름(난류·우유·메밀·땅콩·대두·밀·고등어·게·새우·돼지고기·복숭아·토마토·
    아황산류·호두·닭고기·쇠고기·오징어·조개류·잣). 식단 생성 화면의 선택값과 같은 이름이다.
  · basis: 직접(재료 자체) / 가공품(원재료로 들어감 — 간장→대두·밀 등) / 보수적(종류 불명이라 위험 쪽으로 포함)

적재 규칙:
  · constraints(constraint_type='알레르기', group_id=NULL, severity='절대금지', description=알레르겐 이름).
    group_id=NULL 은 '특정 그룹이 아닌 재료 자체의 알레르겐 표시'라는 뜻이다(그룹별 제약은 group_id 를 채운다).
  · 식단 생성(module_3 csp_solver.load_menus)이 description 을 메뉴의 알레르겐으로 읽는다.
  · 카테고리 코드(조개류·난류·밀 등)는 묶인 재료를 **전부** 매핑한다. 제철 분석(load_seasonal_ingredient)의
    "카테고리 코드는 제외" 기준과 반대다 — 그쪽은 빈도 왜곡을 막는 것이고, 여기는 안전(누락=사고)이 우선이다.
  · CSV 의 (ingredient_id, ingredient_name) 이 DB 와 하나라도 다르면 아무것도 쓰지 않고 멈춘다(부분 적재 금지).
  · 멱등: --apply 마다 group_id=NULL 알레르기 행을 지우고 CSV 대로 다시 만든다.

실행:
  docker exec optimeal_app python scripts/load_allergen_constraints.py           # 검증·요약만(DB 미반영)
  docker exec optimeal_app python scripts/load_allergen_constraints.py --apply   # 적재
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_nutrition_from_recipe_db import get_engine  # noqa: E402

MAP_CSV = REPO_ROOT / "data" / "manual" / "allergen_ingredient_map.csv"

# NEIS 급식 알레르기 코드 19종(scripts/explore_neis_meal.py ALLERGY_MAP 과 같은 표).
ALLERGEN_CODES = {
    "01": "난류", "02": "우유", "03": "메밀", "04": "땅콩", "05": "대두",
    "06": "밀", "07": "고등어", "08": "게", "09": "새우", "10": "돼지고기",
    "11": "복숭아", "12": "토마토", "13": "아황산류", "14": "호두", "15": "닭고기",
    "16": "쇠고기", "17": "오징어", "18": "조개류", "19": "잣",
}
BASIS = {"직접", "가공품", "보수적"}

DELETE_SQL = text("DELETE FROM constraints WHERE constraint_type = '알레르기' AND group_id IS NULL")
INSERT_SQL = text("""
    INSERT INTO constraints (group_id, constraint_type, ingredient_id, severity, description)
    VALUES (NULL, '알레르기', :iid, '절대금지', :allergen)
""")
# 알레르겐별로 영향받는 메뉴 수(식단 후보 nutrition_recipe 기준) — 적재 후 확인용
AFFECTED_SQL = text("""
    SELECT c.description AS allergen, count(DISTINCT r.nutrition_recipe_id) AS menus
    FROM constraints c
    JOIN recipe_ingredient_map rim ON rim.ingredient_id = c.ingredient_id
    JOIN recipe r ON r.recipe_id = rim.recipe_id
    WHERE c.constraint_type = '알레르기' AND c.group_id IS NULL AND r.nutrition_recipe_id IS NOT NULL
    GROUP BY 1 ORDER BY 2 DESC
""")


def read_map(path: Path) -> list[dict]:
    """CSV 를 읽고 형식만 검증한다(DB 대조는 check_against_db)."""
    rows, errors = [], []
    with path.open(encoding="utf-8", newline="") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            code, allergen = r["code"].strip(), r["allergen"].strip()
            if ALLERGEN_CODES.get(code) != allergen:
                errors.append(f"{n}행: 코드 {code} 와 알레르겐 '{allergen}' 이 표준표와 다름")
            if r["basis"].strip() not in BASIS:
                errors.append(f"{n}행: basis '{r['basis']}' 는 {sorted(BASIS)} 중 하나여야 함")
            try:
                iid = int(r["ingredient_id"])
            except ValueError:
                errors.append(f"{n}행: ingredient_id '{r['ingredient_id']}' 가 정수가 아님")
                continue
            rows.append({"line": n, "code": code, "allergen": allergen, "iid": iid,
                         "name": r["ingredient_name"].strip(), "basis": r["basis"].strip()})
    seen = set()
    for r in rows:
        key = (r["allergen"], r["iid"])
        if key in seen:
            errors.append(f"{r['line']}행: ({r['allergen']}, {r['iid']}) 중복")
        seen.add(key)
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
    by = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by[r["allergen"]][r["basis"]] += 1
    print(f"[매핑] {len(rows)}행 · 재료 {len({r['iid'] for r in rows})}종 · 알레르겐 {len(by)}/19종")
    for code, allergen in ALLERGEN_CODES.items():
        b = by.get(allergen)
        detail = " · ".join(f"{k} {v}" for k, v in sorted(b.items())) if b else "없음"
        print(f"  {code} {allergen:<5} {detail}")


def main() -> int:
    ap = argparse.ArgumentParser(description="알레르겐→재료 매핑을 constraints 에 적재")
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
            conn.execute(INSERT_SQL, {"iid": r["iid"], "allergen": r["allergen"]})
    print(f"\n[완료] 기존 {deleted}행 삭제 → {len(rows)}행 적재")

    with engine.connect() as conn:
        affected = conn.execute(AFFECTED_SQL).all()
    print("[영향 메뉴 수] " + " · ".join(f"{a}:{n}" for a, n in affected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
