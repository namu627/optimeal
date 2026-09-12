# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · R92 조리 지시서 데이터 조립 (식단표 → 전 메뉴 레시피)
간트 R92 / FR-14. 순수 함수 — recipe_of·scale 주입, DB 없이 테스트 가능.
스케일 기본 = 선형(1인분×인원수), ADR-008 기준.
"""
from __future__ import annotations


def linear_scale(base_amount_g, servings):
    """cold-start 선형 스케일: 1인분 투입량 × 인원수."""
    if base_amount_g is None:
        return None
    return round(float(base_amount_g) * servings, 1)


def build_cooking_sheet(plan, recipe_of, servings, *, scale=linear_scale, track="공통식"):
    """확정 식단표를 훑어 전 메뉴의 조리 지시 데이터를 만든다."""
    if servings is None or servings <= 0:
        raise ValueError("servings(인원수)는 1 이상이어야 합니다.")
    rows = []
    for day in sorted(plan):
        for meal, menus in plan[day].items():
            for menu in menus:
                rec = recipe_of(menu)
                if not rec:
                    rows.append(_row(day, meal, menu, track, None, servings, [],
                                     note="레시피 없음 - 영양사 확인 필요"))
                    continue
                ings = []
                for ing in rec.get("ingredients", []):
                    ings.append({
                        "step": ing.get("step"),
                        "name": ing.get("name"),
                        "amount": scale(ing.get("base_amount_g"), servings),
                        "unit": ing.get("unit", "g"),
                        "role": ing.get("role"),
                    })
                ings.sort(key=lambda x: (x["step"] is None, x["step"] or 0))
                rows.append(_row(day, meal, menu, track,
                                 rec.get("cooking_method"), servings, ings, note=None))
    return rows


def _row(day, meal, menu, track, method, servings, ingredients, *, note):
    return {"day": day, "meal": meal, "menu": menu, "track": track,
            "cooking_method": method, "servings": servings,
            "ingredients": ingredients, "note": note}


def to_json(rows, *, indent=2):
    import json
    return json.dumps(rows, ensure_ascii=False, indent=indent)


def to_csv(rows):
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["일자", "끼니", "메뉴", "트랙", "조리방법", "인원수",
                "순서", "재료", "투입량", "단위", "역할", "비고"])
    for r in rows:
        base = [r["day"], r["meal"], r["menu"], r["track"],
                r["cooking_method"] or "", r["servings"]]
        if not r["ingredients"]:
            w.writerow(base + ["", "", "", "", "", r["note"] or ""])
            continue
        for ing in r["ingredients"]:
            w.writerow(base + [ing["step"] if ing["step"] is not None else "",
                               ing["name"], ing["amount"], ing["unit"],
                               ing["role"] or "", ""])
    return buf.getvalue()


def make_db_recipe_of(engine):
    """실 DB에서 메뉴명으로 레시피 조회하는 recipe_of 생성(운영용, DB 필요)."""
    from sqlalchemy import text
    query = text("""
        SELECT cm.method_name AS cooking_method,
               ing.ingredient_name AS name,
               rim.per_serving_grams AS base_amount_g,
               rim.unit AS unit,
               rim.ingredient_role AS role,
               rim.cooking_step_order AS step
        FROM nutrition_recipe nr
        JOIN recipe r                ON r.nutrition_recipe_id = nr.nutrition_id
        JOIN recipe_ingredient_map rim ON rim.recipe_id = r.recipe_id
        JOIN ingredient ing          ON ing.ingredient_id = rim.ingredient_id
        LEFT JOIN cooking_method cm   ON cm.method_id = r.primary_method_id
        WHERE nr.recipe_name = :menu
        ORDER BY rim.cooking_step_order NULLS LAST
    """)
    def recipe_of(menu_name):
        with engine.connect() as conn:
            rows = conn.execute(query, {"menu": menu_name}).mappings().all()
        if not rows:
            return None
        return {
            "cooking_method": rows[0]["cooking_method"],
            "ingredients": [{
                "name": r["name"],
                "base_amount_g": float(r["base_amount_g"]) if r["base_amount_g"] is not None else None,
                "unit": r["unit"] or "g",
                "role": r["role"],
                "step": r["step"],
            } for r in rows],
        }
    return recipe_of
