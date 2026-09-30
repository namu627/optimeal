#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: derive_ingredient_price.py
목적: 시세 API(가락·KAMIS)에 없는 재료 중 '이미 있는 가격에서 규칙으로 유도할 수 있는 것'만 채운다.
      값을 새로 지어내지 않는다 — 모든 행은 DB 의 다른 재료 가격 × 고정 규칙이다.

실행 (가격 로더 두 개 다음에):
  docker exec optimeal_app python scripts/derive_ingredient_price.py           # 대상만 출력(DB 미반영)
  docker exec optimeal_app python scripts/derive_ingredient_price.py --apply   # 적재

규칙:
  1) 밥류 = 원곡 g당 가격 ÷ COOKED_RICE_WEIGHT_RATIO(2.3)                      source='쌀 환산'
     밥←쌀, 현미밥←현미, 귀리밥←귀리. 원곡 가격이 없으면 보류(출력에 표시).
  2) '상위품목(부위)' 꼴 재료에 자기 가격이 없으면 상위품목 가격을 그대로 쓴다     source='상위품목 대체'
     예: 닭고기(가슴)·닭고기(다리) ← 닭고기, 돼지고기(등심) ← 돼지고기.
     부위별 단가 차이(가슴살 > 통닭 등)는 반영되지 않는다 — 수기 참조표로 덮어쓸 자리를 표시하는 용도.
  3) 이름만 다른 같은 재료(SYNONYM_SUBSTITUTES)는 원 재료 가격을 쓴다              source='동의어 대체'
     예: 배춧잎←배추, 달걀(흰자)←달걀. 원 재료 가격이 없으면 보류.
     ingredient_synonym 에는 넣지 않는다 — 이 이름들은 이미 각자 별도 ingredient 행이고 synonym_name 이
     자기 자신을 가리키고 있어(UNIQUE), 옮기면 레시피 적재·영양·알레르기 쪽 재료 식별이 바뀐다.
  4) 팀 정책 "물·육수 = 0원"(POLICY_ZERO): 물과 조리장에서 끓이는 육수류는 0원/g      source='정책 0원'
     원가 합계는 가격 없음과 같지만, 커버리지·미가격 목록에서 '빈칸'이 아닌 '정책값'으로 구분된다.

멱등: 실행할 때마다 두 source 의 기존 행을 지우고 현재 상위 가격으로 다시 만든다.
      price_date 는 원천 가격의 price_date 라, 원천이 갱신되면 재실행으로 따라간다.
      자기 시세가 생긴 재료는 다음 실행에서 대체 대상에서 빠진다.
두부 등 가공품은 다루지 않는다(수기 참조표 대상).
"""

import argparse
import re
from datetime import date

from sqlalchemy import text

from load_ingredient_price import get_engine
from price_priority import rank_sql

# 쌀 → 밥 중량 배수. 백미는 취반 시 흡수로 무게가 약 2.2~2.4배가 된다
#   (단체급식 취반 기준 '쌀 1kg → 밥 약 2.3kg'). g당 가격은 그 배수로 나눈다.
COOKED_RICE_WEIGHT_RATIO = 2.3
# {파생 재료명: 원천 재료명}
RICE_DERIVATIONS = {"밥": "쌀", "현미밥": "현미", "귀리밥": "귀리"}
RICE_SOURCE = "쌀 환산"

# '상위품목(부위)' 대체를 허용할 상위품목. 부위 = 괄호 안 전체('등심, 다짐육' 포함).
PARENT_ITEMS = ["닭고기", "돼지고기", "소고기"]
# 이 말이 부위에 들어가면 대체하지 않는다 — 뼈·꼬리는 정육 단가와 크게 달라 대체하면 오히려 왜곡된다.
EXCLUDE_PART_KEYWORDS = ("뼈", "꼬리")
PARENT_SOURCE = "상위품목 대체"

# {이름만 다른 재료: 원 재료}. 같은 물건을 부르는 다른 이름(부위·형태가 달라 단가가 다른 것은 넣지 않는다).
#   달걀(흰자)는 무게당 단가를 달걀 전체와 같게 본다(흰자만 따로 사지 않고 달걀을 깨서 쓰므로).
#   묵은지·백김치 ← 김치(같은 배추김치 계열), 맛간장 ← 간장(참가격에 맛간장 상품 없음 — 2026-09-30).
#   순두부·연두부는 두부로 대체하지 않는다(두부보다 g당 가격이 훨씬 낮아 과대 계상).
SYNONYM_SUBSTITUTES = {"배춧잎": "배추", "미니 단호박": "단호박", "홍시": "감", "달걀(흰자)": "달걀",
                       "묵은지": "김치", "백김치": "김치", "맛간장": "간장"}
SYNONYM_SOURCE = "동의어 대체"

# 팀 정책: 물·조리장에서 끓이는 육수는 0원(수도료·연료비는 식재료비가 아니고, 육수 재료는
#   레시피에 따로 잡히면 그쪽에 원가가 붙는다). 시판 육수·곰탕 제품은 여기에 넣지 않는다.
POLICY_ZERO = ["물", "쌀뜨물", "발효종", "육수", "닭육수", "채소국물", "야채국물", "사골육수",
               "해물육수", "멸치육수", "멸치다시마육수"]
POLICY_ZERO_SOURCE = "정책 0원"

DERIVED_SOURCES = (RICE_SOURCE, PARENT_SOURCE, SYNONYM_SOURCE, POLICY_ZERO_SOURCE)

# 원천(파생이 아닌) 가격 — 식단 원가와 같은 출처 우선순위로 고른다(scripts/price_priority.py).
_LATEST_REAL_PRICE = f"""
    SELECT DISTINCT ON (p.ingredient_id) p.ingredient_id, i.ingredient_name, p.price_per_g, p.price_date, p.source
    FROM ingredient_price p JOIN ingredient i USING (ingredient_id)
    WHERE p.source <> ALL(:derived)
    ORDER BY p.ingredient_id, {rank_sql('p.source')}, p.price_date DESC
"""


def plan(conn):
    """(적재할 파생 행 [{ingredient_id, name, price, price_date, source, notes}], 보류 목록 [(재료, 사유)])."""
    derived = list(DERIVED_SOURCES)
    real = {r.ingredient_name: r for r in conn.execute(text(_LATEST_REAL_PRICE), {"derived": derived})}
    names = {r.ingredient_name: r.ingredient_id
             for r in conn.execute(text("SELECT ingredient_id, ingredient_name FROM ingredient"))}
    rows, held = [], []

    for target, origin in RICE_DERIVATIONS.items():
        src = real.get(origin)
        if target in names and target not in real and src is None:
            held.append((target, f"원곡 '{origin}' 가격 없음"))
        if target in names and target not in real and src:
            rows.append({
                "ingredient_id": names[target], "name": target,
                "price": round(float(src.price_per_g) / COOKED_RICE_WEIGHT_RATIO, 4),
                "price_date": src.price_date, "source": RICE_SOURCE,
                "notes": f"{origin} {src.price_per_g}원/g({src.source} {src.price_date}) ÷ 취반 중량배수 "
                         f"{COOKED_RICE_WEIGHT_RATIO}",
            })

    pattern = re.compile(rf"^({'|'.join(map(re.escape, PARENT_ITEMS))})\((.+)\)$")
    for name, ing_id in sorted(names.items()):
        m = pattern.match(name)
        if not m or name in real:
            continue
        parent, part = m.group(1), m.group(2)
        src = real.get(parent)
        if src is None or any(k in part for k in EXCLUDE_PART_KEYWORDS):
            continue
        rows.append({
            "ingredient_id": ing_id, "name": name, "price": float(src.price_per_g),
            "price_date": src.price_date, "source": PARENT_SOURCE,
            "notes": f"상위품목 대체: {parent} {src.price_per_g}원/g({src.source} {src.price_date}) — 부위 단가 미반영",
        })

    for target, origin in SYNONYM_SUBSTITUTES.items():
        if target not in names or target in real:
            continue
        src = real.get(origin)
        if src is None:
            held.append((target, f"원 재료 '{origin}' 가격 없음"))
            continue
        rows.append({
            "ingredient_id": names[target], "name": target, "price": float(src.price_per_g),
            "price_date": src.price_date, "source": SYNONYM_SOURCE,
            "notes": f"동의어 대체: {origin} {src.price_per_g}원/g({src.source} {src.price_date})",
        })

    for target in POLICY_ZERO:
        if target in names and target not in real:
            rows.append({
                "ingredient_id": names[target], "name": target, "price": 0.0,
                "price_date": date.today(), "source": POLICY_ZERO_SOURCE,
                "notes": "팀 정책: 물·조리장 육수 = 0원",
            })
    return rows, held


def main():
    ap = argparse.ArgumentParser(description="규칙 기반 파생 원가(밥류÷2.3·부위=상위품목·동의어·정책 0원) 적재")
    ap.add_argument("--apply", action="store_true", help="DB 에 적재(없으면 대상만 출력)")
    args = ap.parse_args()

    engine = get_engine()
    with engine.begin() as conn:
        rows, held = plan(conn)
        for r in rows:
            print(f"  [{r['source']}] {r['name']:<20} {r['price']:>9.4f}원/g  ({r['notes']})")
        for name, reason in held:
            print(f"  [보류] {name} — {reason}")
        counts = " · ".join(f"{src} {sum(r['source'] == src for r in rows)}" for src in DERIVED_SOURCES)
        print(f"\n  대상 {len(rows)}개 ({counts}) / 보류 {len(held)}개")
        if not args.apply:
            print("  [dry-run] DB 미반영. --apply 로 적재.")
            return
        deleted = conn.execute(text("DELETE FROM ingredient_price WHERE source = ANY(:d)"),
                               {"d": list(DERIVED_SOURCES)}).rowcount
        inserted = 0
        for r in rows:
            inserted += conn.execute(text("""
                INSERT INTO ingredient_price (ingredient_id, price_per_g, price_date, source, notes)
                VALUES (:ingredient_id, :price, :price_date, :source, :notes)
                ON CONFLICT (ingredient_id, price_date, source) DO NOTHING
            """), r).rowcount
        print(f"  기존 파생 행 {deleted}개 삭제 → {inserted}개 적재")


if __name__ == "__main__":
    main()
