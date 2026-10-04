#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_ingredients_from_rcp_parts.py
목적: 재료 맵(recipe_ingredient_map)이 없는 식단 후보 메뉴를 식품안전나라 원본 재료 문자열
      (nutrition_recipe.original_data.RCP_PARTS_DTLS)로 채운다. 원본에 재료가 없으면 건드리지 않는다.

배경(2026-10-01): 후보 메뉴 966종 중 10종이 재료 맵이 없어 원가·레시피가 비었다. 대부분 메뉴는 xlsx 레시피와
      이름으로 이어졌는데(connect_menus_to_recipes.py) 이 10종은 같은 이름의 xlsx 레시피가 없다.
      6종은 원본에 재료 문자열이 있고, 4종은 원본에도 없다(빈 문자열·'.').

실행:
  docker exec optimeal_app python scripts/load_ingredients_from_rcp_parts.py           # 미리보기(DB 미반영)
  docker exec optimeal_app python scripts/load_ingredients_from_rcp_parts.py --apply   # 적재

규칙(추정 금지):
  - 양은 g 로 적힌 것만 쓴다: '닭고기살(150g)', '얇게 썬 쇠고기(부채살, 80g)', '가지 70g'.
    '적당량·약간·조금' → amount_type DISCRETIONARY, 단위 없는 숫자('타임다진것 0.5') → UNKNOWN. 둘 다 양은 비운다.
  - 식품안전나라 COOKRCP01 의 재료량은 1인분 기준이라 serving_size=1, per_serving_grams = 원문 g.
  - 재료명: 가락 로더 매처(match_ingredient_id: 완전일치 → 동의어 → 정규화)에 뒤쪽 단어 조합을 넣는다.
    첫 단어만 맞추는 low 등급은 쓰지 않는다. 소제목('재료', '주먹밥', '두부강된장', '허브타르타르드레싱')은 원문에서
    **줄의 첫 재료 앞에만** 붙으므로, 줄 첫 토큰은 짧은 조합부터(→ '두부강된장 참기름' 은 참기름),
    나머지는 긴 조합부터 맞춘다(→ '낙지 다리' 는 낙지다리). 정규화 테이블에 소제목이 섞인 동의어
    ('두부강된장 참기름'→두부강된장, '허브타르타르드레싱 양파')가 있어 긴 조합부터 맞추면 틀린다(2026-10-01 확인).
    매칭 안 된 재료는 data/external/rcp_parts_unmatched_YYYYMMDD.csv 로 남기고 맵에 넣지 않는다.
  - 레시피 행은 notes '[rcp_parts:<nutrition_id>]' 로 표시 — 재실행하면 그 레시피의 맵을 지우고 다시 만든다(멱등).
"""

import argparse
import csv
import re
from datetime import date

from sqlalchemy import text

from load_ingredient_price import UNMATCHED_LOG_DIR, get_engine, match_ingredient_id
from load_recipe_foodsafety import build_cooking_method_lookup, resolve_method_id

CATS = ["주식", "국", "찌개", "주찬", "부찬", "김치", "반찬"]
MARK = "[rcp_parts:{}]"
DISCRETIONARY_WORDS = ("적당량", "약간", "조금", "적량")
# 재료명 앞뒤 손질 표현 — 매칭 후보에서 뗀다(재료가 아님)
PREP_PREFIX = re.compile(r"^(송송 썬|얇게 썬|곱게 다진|다진|데친|으깬|채 썬|잘게 썬)\s*")
PREP_SUFFIX = re.compile(r"(다진것|다진 것|썬것|썬 것)$")

TARGETS_SQL = """
SELECT c.nutrition_id, c.recipe_name, c.original_data->>'RCP_PARTS_DTLS' AS parts, c.original_data->>'RCP_WAY2' AS way
FROM nutrition_recipe c
WHERE c.menu_category = ANY(:cats)
  AND NOT EXISTS (SELECT 1 FROM recipe r JOIN recipe_ingredient_map m ON m.recipe_id = r.recipe_id
                  WHERE r.nutrition_recipe_id = c.nutrition_id
                    AND COALESCE(r.notes, '') NOT LIKE '[rcp_parts:%')
ORDER BY c.nutrition_id
"""


def split_tokens(parts: str) -> list[tuple[str, bool]]:
    """원문 → [(재료 토큰, 줄 첫 토큰 여부)]. 줄바꿈·괄호 밖 쉼표로 자르고, '- 소스 :' 같은 소제목 표지는 뗀다."""
    out = []
    for line in parts.splitlines():
        tokens = []
        line = re.sub(r"^\s*-?\s*[^,()]*?:\s*", "", line) if ":" in line else line
        depth, cur = 0, ""
        for ch in line:
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                tokens.append(cur)
                cur = ""
            else:
                cur += ch
        tokens.append(cur)
        tokens = [t.strip() for t in tokens if t.strip() and t.strip() != "."]
        out += [(t, i == 0) for i, t in enumerate(tokens)]
    return out


def parse_token(tok: str):
    """→ (재료명 원문, g 또는 None, amount_type)."""
    m = re.match(r"^(?P<name>[^()]+?)\s*\((?P<inside>[^()]*)\)\s*$", tok)
    if m:  # 괄호 안의 마지막 수량: '(부채살, 80g)' → 80g
        name, inside = m.group("name"), m.group("inside")
        g = re.search(r"(\d+(?:\.\d+)?)\s*g\s*$", inside.split(",")[-1].strip())
        if g:
            return name.strip(), float(g.group(1)), "MEASURED"
        if any(w in inside for w in DISCRETIONARY_WORDS):
            return name.strip(), None, "DISCRETIONARY"
        return name.strip(), None, "UNKNOWN"
    g = re.match(r"^(?P<name>.+?)\s*(?P<num>\d+(?:\.\d+)?)\s*g\s*$", tok)
    if g:
        return g.group("name").strip(), float(g.group("num")), "MEASURED"
    for w in DISCRETIONARY_WORDS:
        if tok.endswith(w):
            return tok[: -len(w)].strip(), None, "DISCRETIONARY"
    n = re.match(r"^(?P<name>.+?)\s*\d+(?:\.\d+)?\s*$", tok)
    if n:
        return n.group("name").strip(), None, "UNKNOWN"
    return tok, None, "UNKNOWN"


def name_candidates(name: str, line_first: bool = False) -> list[str]:
    """뒤쪽 단어 조합 + 손질 표현을 뗀 형태. 줄 첫 토큰은 짧은 조합부터(소제목 제거), 나머지는 긴 조합부터."""
    words = name.split()
    out = []
    order = range(len(words) - 1, -1, -1) if line_first else range(len(words))
    for i in order:
        cand = " ".join(words[i:])
        for c in (cand, PREP_SUFFIX.sub("", PREP_PREFIX.sub("", cand)).strip()):
            if c and c not in out:
                out.append(c)
    return out


def match(conn, name, match_cache, lookup_cache, line_first=False):
    for cand in name_candidates(name, line_first):
        ing_id, ing_name, conf = match_ingredient_id(conn, cand, match_cache, lookup_cache)
        if ing_id is not None and conf in ("exact", "synonym", "high"):
            return ing_id, ing_name
    return None, None


def main():
    ap = argparse.ArgumentParser(description="원본 RCP_PARTS_DTLS 로 재료 맵이 없는 메뉴 채우기")
    ap.add_argument("--apply", action="store_true", help="DB 에 적재(없으면 미리보기)")
    args = ap.parse_args()
    engine = get_engine()
    match_cache, lookup_cache = {}, {}
    unmatched, no_source, plans = [], [], []
    with engine.begin() as conn:
        methods = build_cooking_method_lookup(conn)
        for t in conn.execute(text(TARGETS_SQL), {"cats": CATS}).mappings():
            parts = (t["parts"] or "").strip()
            toks = split_tokens(parts)
            if not toks:
                no_source.append((t["nutrition_id"], t["recipe_name"], repr(parts[:20])))
                continue
            method_id = resolve_method_id(t["way"], methods)
            rows = []
            for i, (tok, first) in enumerate(toks, start=1):
                raw_name, grams, kind = parse_token(tok)
                ing_id, ing_name = match(conn, raw_name, match_cache, lookup_cache, line_first=first)
                if ing_id is None:
                    unmatched.append((t["nutrition_id"], t["recipe_name"], tok, raw_name))
                    continue
                rows.append({"ing": ing_id, "ing_name": ing_name, "grams": grams, "kind": kind, "order": i, "orig": tok[:200]})
            plans.append((t, method_id, rows, len(toks)))

        for t, method_id, rows, n in plans:
            measured = sum(r["kind"] == "MEASURED" for r in rows)
            print(f"  [{t['nutrition_id']}] {t['recipe_name']} (조리법 {t['way']}→{method_id}): 재료 {n}개 중 매칭 {len(rows)} · 양 확인 {measured}")
            for r in rows:
                amt = f"{r['grams']:g}g" if r["grams"] is not None else r["kind"]
                print(f"      {r['orig'][:34]:<34} → {r['ing_name']} {amt}")
        for nid, name, parts in no_source:
            print(f"  [원본 재료 없음] {nid} {name} (RCP_PARTS_DTLS={parts})")
        print(f"\n  대상 {len(plans)}개 메뉴 / 원본 재료 없음 {len(no_source)}개 / 매칭 안 된 재료 {len(unmatched)}개")

        UNMATCHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = UNMATCHED_LOG_DIR / f"rcp_parts_unmatched_{date.today():%Y%m%d}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["nutrition_id", "menu", "token", "ingredient_text"])
            w.writerows(unmatched)
        print(f"  매칭 안 된 재료 CSV: {path}")
        if not args.apply:
            print("  [미리보기] DB 미반영. --apply 로 적재.")
            conn.rollback()
            return

        made = 0
        for t, method_id, rows, _ in plans:
            if method_id is None:
                print(f"  [건너뜀] {t['recipe_name']}: 조리법 '{t['way']}' 매핑 없음")
                continue
            mark = MARK.format(t["nutrition_id"])
            rid = conn.execute(text("SELECT recipe_id FROM recipe WHERE notes = :m"), {"m": mark}).scalar()
            if rid is None:
                rid = conn.execute(text("""
                    INSERT INTO recipe (recipe_name, primary_method_id, serving_size, serving_category,
                                        nutrition_recipe_id, data_source, notes)
                    VALUES (:n, :mid, 1, '소규모', :nid, '식약처API', :m) RETURNING recipe_id
                """), {"n": t["recipe_name"], "mid": method_id, "nid": t["nutrition_id"], "m": mark}).scalar()
            conn.execute(text("DELETE FROM recipe_ingredient_map WHERE recipe_id = :r"), {"r": rid})
            for r in rows:
                conn.execute(text("""
                    INSERT INTO recipe_ingredient_map (recipe_id, ingredient_id, amount, unit, amount_in_grams,
                        per_serving_grams, ingredient_role, cooking_step_order, original_text, amount_type)
                    VALUES (:rid, :ing, :g, :unit, :g, :g, '부재료', :order, :orig, :kind)
                """), {"rid": rid, "ing": r["ing"], "g": r["grams"], "order": r["order"], "orig": r["orig"],
                       "kind": r["kind"], "unit": "g" if r["grams"] is not None else ("적당량" if r["kind"] == "DISCRETIONARY" else "미상")})
            made += 1
        print(f"  적재: 메뉴 {made}개")


if __name__ == "__main__":
    main()
