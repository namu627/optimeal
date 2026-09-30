#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_ingredient_price_manual.py
목적: 시세 API(가락·KAMIS)·참가격에 없는 재료를 사람이 조사한 참조 가격표(CSV)로 채운다.
      source='수기_참조'.

입력: data/manual/manual_price_reference_YYYYMMDD.csv (저장소에 커밋, --csv 로 지정 가능 · 생략 시 최신 파일)
  컬럼: ingredient_id, ingredient_name, product_name, package_amount, package_unit(g|ml),
        price_won, density_g_per_ml, source_site, url, surveyed_on, note

실행 (가락 → KAMIS → 참가격 → **수기** → derive 순서):
  docker exec optimeal_app python scripts/load_ingredient_price_manual.py            # 대상만 출력(DB 미반영)
  docker exec optimeal_app python scripts/load_ingredient_price_manual.py --apply    # 적재

규칙:
  - 행마다 g당 가격 = price_won ÷ (package_amount × 비중). 단위 ml 면 density_g_per_ml(비었으면 1.0 + 경고), g 면 1.
  - 재료별로 g당 가격의 중앙값을 넣는다. price_date = 그 재료 행들의 surveyed_on 중 최신.
  - ingredient_id 와 ingredient_name 이 DB 와 다르면 그 행은 건너뛴다(잘못된 id 로 엉뚱한 재료에 들어가는 것 방지).
  - 이미 다른 출처(가락·KAMIS·참가격·파생 등 '수기_참조'가 아닌 source)의 가격이 있는 재료는 건드리지 않는다.
    파생(derive)은 이 스크립트 다음에 돌며, 실제 가격이 생긴 재료는 파생 대상에서 빠진다.
  - 멱등: 같은 조사일 재실행은 수기_참조 행만 갱신(ON CONFLICT ... WHERE source='수기_참조').
"""

import argparse
import csv
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median

from sqlalchemy import text

from load_ingredient_price import get_engine

BASE_DIR = Path(__file__).parent.parent
MANUAL_GLOB = "manual_price_reference_*.csv"
SOURCE_NAME = "수기_참조"


def latest_csv():
    files = sorted((BASE_DIR / "data" / "manual").glob(MANUAL_GLOB))
    return files[-1] if files else None


def read_rows(path):
    """CSV → [(ingredient_id, name, product, price_per_g, surveyed_on, warn)]."""
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            amount = float(r["package_amount"])
            unit = (r["package_unit"] or "").strip().lower()
            warn = ""
            if unit == "ml":
                dens = (r.get("density_g_per_ml") or "").strip()
                if dens:
                    density = float(dens)
                else:
                    density, warn = 1.0, "ml 인데 비중 없음 → 1.0 가정"
            elif unit == "g":
                density = 1.0
            else:
                raise SystemExit(f"[오류] 지원하지 않는 단위 '{unit}': {r['product_name']}")
            grams = amount * density
            out.append((int(r["ingredient_id"]), r["ingredient_name"].strip(), r["product_name"].strip(),
                        float(r["price_won"]) / grams, date.fromisoformat(r["surveyed_on"].strip()), warn))
    return out


def plan(conn, rows):
    """→ (적재 대상 [{ingredient_id, name, price, price_date, notes}], 건너뜀 [(이름, 사유)])"""
    names = {r.ingredient_id: r.ingredient_name
             for r in conn.execute(text("SELECT ingredient_id, ingredient_name FROM ingredient"))}
    other = {r.ingredient_id: r.source for r in conn.execute(text(
        "SELECT DISTINCT ON (ingredient_id) ingredient_id, source FROM ingredient_price "
        "WHERE source <> :s ORDER BY ingredient_id, price_date DESC"), {"s": SOURCE_NAME})}
    by_ing = defaultdict(list)
    skipped = []
    for ing_id, name, product, ppg, surveyed, warn in rows:
        if names.get(ing_id) != name:
            skipped.append((f"{name}({ing_id}) · {product}", f"DB 재료명 불일치: {names.get(ing_id)!r}"))
            continue
        if warn:
            print(f"  [경고] {product}: {warn}")
        by_ing[ing_id].append((product, ppg, surveyed))
    targets = []
    for ing_id, items in by_ing.items():
        if ing_id in other:
            skipped.append((names[ing_id], f"이미 다른 출처 가격 있음({other[ing_id]})"))
            continue
        price = round(median(p for _, p, _ in items), 4)
        products = ", ".join(f"{prod} {p:.2f}원/g" for prod, p, _ in items)
        targets.append({"ingredient_id": ing_id, "name": names[ing_id], "price": price,
                        "price_date": max(d for _, _, d in items),
                        "notes": f"수기 참조 {len(items)}개 상품 g당 중앙값 ({products})"[:1000]})
    return targets, skipped


def main():
    ap = argparse.ArgumentParser(description="수기 참조 가격표(CSV) 적재 — source='수기_참조'")
    ap.add_argument("--csv", type=Path, default=None, help="입력 CSV (생략 시 data/manual 최신 파일)")
    ap.add_argument("--apply", action="store_true", help="DB 에 적재(없으면 대상만 출력)")
    args = ap.parse_args()
    path = args.csv or latest_csv()
    if path is None or not Path(path).exists():
        raise SystemExit(f"[오류] 수기 참조 CSV 가 없습니다: data/manual/{MANUAL_GLOB}")
    rows = read_rows(path)
    print(f"  입력: {Path(path).name} {len(rows)}행")

    engine = get_engine()
    with engine.begin() as conn:
        targets, skipped = plan(conn, rows)
        for t in targets:
            print(f"  [적재 대상] {t['name']:<10} {t['price']:>8.4f}원/g  ({t['price_date']}) {t['notes']}")
        for name, why in skipped:
            print(f"  [건너뜀] {name} — {why}")
        print(f"\n  대상 {len(targets)}개 / 건너뜀 {len(skipped)}개")
        if not args.apply:
            print("  [dry-run] DB 미반영. --apply 로 적재.")
            return
        # 다른 출처가 생긴 재료의 옛 수기 행은 지운다(시세 우선)
        removed = conn.execute(text("""
            DELETE FROM ingredient_price p WHERE p.source = :s
              AND EXISTS (SELECT 1 FROM ingredient_price o WHERE o.ingredient_id = p.ingredient_id AND o.source <> :s)
        """), {"s": SOURCE_NAME}).rowcount
        stats = defaultdict(int)
        for t in targets:
            row = conn.execute(text("""
                INSERT INTO ingredient_price (ingredient_id, price_per_g, price_date, source, notes)
                VALUES (:ingredient_id, :price, :price_date, :src, :notes)
                ON CONFLICT (ingredient_id, price_date) DO UPDATE
                    SET price_per_g = EXCLUDED.price_per_g, notes = EXCLUDED.notes
                    WHERE ingredient_price.source = :src
                RETURNING (xmax = 0) AS is_insert
            """), {**t, "src": SOURCE_NAME}).fetchone()
            stats["skipped" if row is None else ("inserted" if row.is_insert else "updated")] += 1
        print(f"  적재: 신규 {stats['inserted']} / 갱신 {stats['updated']} / 같은 날 타 소스 있어 건너뜀 {stats['skipped']}"
              f" / 다른 출처가 생긴 재료의 수기 행 삭제 {removed}")


if __name__ == "__main__":
    main()
