#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_ingredient_price_chamgagyeok.py
목적: 한국소비자원 참가격 '생필품 가격 정보'(공공데이터포털 15083256, 월 갱신 CSV)로
      가락·KAMIS 에 없는 가공식품·양념(두부·식용유·설탕·밀가루·간장·된장 등) 원가를 채운다.
      source='참가격_R'(소매).

원본: https://www.data.go.kr/data/15083256/fileData.do 의 최신 월 CSV(cp949)를
      data/raw/한국소비자원_생필품가격_YYYYMM.csv 로 저장. --csv 를 생략하면 가장 최근 파일을 쓴다.
      컬럼: 상품명, 조사일, 판매가격, 판매업소, 제조사, 세일여부, 원플러스원 — 용량은 상품명 끝 괄호에 있다.

실행 (preview → load 2단계, 가락·KAMIS 로더와 동일):
  docker exec optimeal_app python scripts/load_ingredient_price_chamgagyeok.py --mode preview
  docker exec optimeal_app python scripts/load_ingredient_price_chamgagyeok.py --mode load
  이후 derive_ingredient_price.py --apply 를 다시 돌린다(파생 대상이 바뀔 수 있음).

규칙:
  - 세일·1+1 행은 정상가가 아니므로 뺀다.
  - 용량: 상품명 끝 괄호의 마지막 항목(예: '(냉동, 100g)', '(500ml*20개)', '(300~500g)' → 중간값).
    '개·개입·팩·캔·인분' 등 개수 단위는 g 로 못 바꾸므로 환산 실패로 남긴다.
    ml·L 는 재료별 비중(DENSITY_G_PER_ML)으로 g 환산, 표에 없으면 1.0 으로 두고 환산 로그에 남긴다.
  - 매칭: 브랜드를 무시하려고 상품명(용량 괄호 제거)의 뒤쪽 토큰 조합을 길이순으로
    ingredient / ingredient_synonym 완전일치 → 실패 시 KEYWORD_RULES. 완제품·간편식(COMPOSITE_EXCLUDE)은 매칭하지 않는다.
  - 한 재료의 값: 상품별로 [판매점별 g당 가격(점포 내 조사일 중앙값)]의 중앙값을 구한 뒤,
    매칭된 전체 상품의 그 값의 중앙값.
  - 가락·KAMIS 가격이 있는 재료는 건드리지 않는다. 걸리는 기존 참가격_R 행은 load 때 지운다.
  - 멱등: 같은 조사일 재실행은 참가격_R 행만 갱신(ON CONFLICT ... WHERE source='참가격_R').
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import pandas as pd
from sqlalchemy import text

from load_ingredient_price import SOURCE_NAME as GARAK_SOURCE
from load_ingredient_price import UNMATCHED_LOG_DIR, _lookup_exact_name, get_engine

BASE_DIR = Path(__file__).parent.parent
RAW_GLOB = "한국소비자원_생필품가격_*.csv"
SOURCE_NAME = "참가격_R"
# 이 source 들의 가격이 있는 재료에는 참가격을 넣지 않는다(시세 우선).
MARKET_SOURCES = (GARAK_SOURCE, "KAMIS_W", "KAMIS_R")

# ml → g 비중(g/ml). 키는 매칭된 우리 재료명. 없으면 1.0(로그).
#   식물성 유지 0.91~0.92, 간장 1.14~1.16, 양조식초 약 1.01(식품공전·제조사 표기 범위의 대표값).
DENSITY_G_PER_ML = {
    "식용유": 0.92, "튀김기름": 0.92, "콩기름": 0.92, "카놀라유": 0.92, "포도씨유": 0.92,
    "올리브유": 0.91, "참기름": 0.92, "들기름": 0.92,
    "간장": 1.15, "진간장": 1.15, "양조간장": 1.15, "국간장": 1.15,
    "저염간장": 1.15, "저염 간장": 1.15, "저염진간장": 1.15,
    "식초": 1.01, "사과식초": 1.01, "현미식초": 1.01,
}

# 간편식·완제품 — 재료명이 들어 있어도 재료 가격이 아니다(예: '쇠고기죽', '두부진밥', '콩나물국').
COMPOSITE_EXCLUDE = re.compile(
    r"라면|사발|컵|죽$|죽\b|밥$|덮밥|국밥|볶음밥|컵반|이유식|진밥|너겟|돈까스|돈카츠|핫도그|만두|떡볶이|"
    r"짜장|곰탕|육개장|미역국|콩나물국|냉면|우동|초밥|스프|젤리|캔디|크래커|칩|에너지바|드링크|음료|"
    r"커피|라떼|티백|녹차|아메리카노|우유식빵|식빵|호떡|약과|케이크|빵$|롤$|유과|만쥬|분유|"
    r"드레싱|치킨|백숙|카레|막걸리|맥주|소주|탄산수|사이다|콜라|"
    r"불가리스|요플레|카페믹스|버터롤|그라브락스"
)
# 생필품 조사라 식품이 아닌 상품이 절반 이상이다. 동의어 뒤토막 매칭('...영에이지 크림' → '크림')을 막는다.
NON_FOOD_EXCLUDE = re.compile(
    r"선크림|선 페이스|선 세럼|로션|샴푸|트리트먼트|바디|세제|티슈|화장지|물티슈|치약|칫솔|면도|패드|"
    r"기저귀|언더웨어|밴드|마스크|호일|크린랩|지퍼백|장갑|건전지|세정제|락스|섬유유연제|에어로졸|에어졸|"
    r"염색|립|핸드워시|항균폼|비누|뷰티바|청소포|습기|부탄가스|반려견|헤어볼|관절|가그린|리스테린|"
    r"페브리즈|손소독|위생"
)
# 같은 품목의 특수 변형 — 일반 재료 단가를 끌어올려서 뺀다(자일로스 설탕은 일반 설탕의 약 2배).
VARIANT_EXCLUDE = re.compile(r"자일로스")

# 동의어 테이블로 안 잡히는 상품 → 재료명들(DB 에 있는 것 전부에 적용). (패턴, 제외 패턴, 대상)
KEYWORD_RULES = [
    (r"두부", r"유부|두부면", ["두부"]),
    (r"부침", r"가루", ["두부(부침용)", "두부(단단한)"]),
    (r"식용유|콩기름", None, ["식용유", "튀김기름", "콩기름"]),
    (r"카놀라유", None, ["카놀라유"]),
    (r"포도씨유", None, ["포도씨유"]),
    (r"참기름", None, ["참기름"]),
    (r"중력|다목적", r"찰밀가루|우리밀", ["밀가루", "중력분", "덧밀가루"]),
    (r"강력\s*밀가루", None, ["강력분"]),
    (r"박력\s*밀가루", None, ["박력분"]),
    (r"우리밀", None, ["우리밀가루"]),
    (r"하얀설탕|백설탕", None, ["설탕", "백설탕"]),
    (r"갈색설탕|황설탕", None, ["황설탕"]),
    (r"흑설탕", None, ["흑설탕"]),
    (r"사과식초", None, ["식초", "사과식초"]),
    (r"현미식초", None, ["현미식초"]),
    # 저염간장은 참가격에 없어 일반 간장 값을 쓴다(저염 제품 프리미엄 미반영 — 근사).
    (r"진간장", None, ["간장", "진간장", "저염간장", "저염 간장", "저염진간장"]),
    (r"양조간장", None, ["간장", "양조간장", "저염간장", "저염 간장"]),
    (r"된장|토장", None, ["된장"]),
    (r"고추장", None, ["고추장"]),
    (r"쌈장", None, ["쌈장"]),
    (r"올리고당", None, ["올리고당"]),
    (r"물엿", None, ["물엿"]),
    (r"벌꿀", None, ["꿀"]),
    (r"꽃소금", None, ["꽃소금"]),
    (r"맛소금", None, ["맛소금"]),
    (r"마요네즈", None, ["마요네즈"]),
    (r"케찹|케첩", None, ["케첩", "토마토케첩"]),
    (r"딸기잼", None, ["딸기잼"]),
    (r"버터", r"땅콩|허니", ["버터"]),
    (r"무염\s*버터|슬로우버터 무염", None, ["무염버터", "무염 버터"]),
    (r"슬라이스치즈|치즈 오리지널", None, ["슬라이스치즈", "치즈"]),
    (r"체다슬라이스", None, ["슬라이스체더치즈", "체더치즈"]),
    (r"소면", None, ["소면"]),
    (r"당면", None, ["당면"]),
    (r"스파게티$|파스타$", None, ["스파게티", "파스타"]),
    (r"부침가루", None, ["부침가루"]),
    (r"단무지", None, ["단무지"]),
    (r"어묵", None, ["어묵"]),
    (r"맛살|크래미", None, ["맛살"]),
    (r"참치", None, ["참치", "참치통조림"]),
    (r"베이컨", None, ["베이컨"]),
    (r"비엔나|후랑크", r"핫도그", ["소시지", "후랑크소시지"]),
    (r"슬라이스햄|델리햄", None, ["슬라이스햄", "햄"]),
    (r"콩나물", None, ["콩나물"]),
    (r"생연어", None, ["연어", "연어 필레"]),
    (r"훈제오리", None, ["훈제오리고기"]),
    (r"포기.*김치|배추김치", None, ["김치", "배추김치", "포기김치"]),
    (r"까나리액젓", None, ["까나리액젓"]),
    (r"와사비", None, ["와사비", "연와사비"]),
]
_KEYWORD_RULES = [(re.compile(p), re.compile(x) if x else None, t) for p, x, t in KEYWORD_RULES]

_UNIT_G = {"kg": 1000.0, "g": 1.0}
_UNIT_ML = {"l": 1000.0, "ml": 1.0}
_QTY_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)(?:\s*~\s*(?P<b>\d+(?:\.\d+)?))?\s*(?P<u>kg|g|ml|l)\b"
    r"(?:\s*[*x×]\s*(?P<n>\d+)\s*(?:개|입|캔|팩|봉)?)?",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────
# 파싱
# ─────────────────────────────────────────────────────────────────
def split_product(name):
    """'해표 식용유(900ml)' → ('해표 식용유', '900ml'), '갈치(냉동, 100g)' → ('갈치', '냉동, 100g')."""
    name = str(name).strip()
    m = re.search(r"\(([^()]*)\)\s*$", name)
    if not m:
        return name, ""
    return name[:m.start()].strip(), m.group(1).strip()


def parse_quantity(paren):
    """괄호 내용 → (양, 'g'|'ml') 또는 None. 마지막 쉼표 항목에서 찾는다."""
    part = paren.split(",")[-1].strip()
    m = _QTY_RE.search(part)
    if not m:
        return None
    a = float(m.group("a"))
    b = float(m.group("b")) if m.group("b") else a
    qty = (a + b) / 2 * int(m.group("n") or 1)
    unit = m.group("u").lower()
    if unit in _UNIT_G:
        return qty * _UNIT_G[unit], "g"
    return qty * _UNIT_ML[unit], "ml"


def read_csv(path):
    for enc in ("cp949", "utf-8-sig"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"[오류] 인코딩을 읽지 못했습니다: {path}")


def latest_raw_csv():
    files = sorted((BASE_DIR / "data" / "raw").glob(RAW_GLOB))
    return files[-1] if files else None


# ─────────────────────────────────────────────────────────────────
# 매칭
# ─────────────────────────────────────────────────────────────────
def name_suffixes(base, maker):
    """브랜드 무시: 뒤쪽 토큰 조합을 긴 것부터. 제조사명과 같은 토큰은 버린다."""
    tokens = [t for t in base.split() if t and t != maker]
    return [" ".join(tokens[i:]) for i in range(len(tokens)) if len("".join(tokens[i:])) >= 2]


def match_product(conn, base, maker, lookup_cache):
    """→ [(ingredient_id, ingredient_name, via)]. via = 'synonym' | 'keyword'. 동의어가 먼저, 키워드 규칙이 보충."""
    if COMPOSITE_EXCLUDE.search(base) or NON_FOOD_EXCLUDE.search(base) or VARIANT_EXCLUDE.search(base):
        return []
    hits = []
    for cand in name_suffixes(base, maker):
        found = _lookup_exact_name(conn, cand, lookup_cache)
        if found:
            hits.append((found[0], found[1], "synonym"))
            break
    # 동의어로 잡혀도 키워드 규칙의 추가 대상은 더한다(예: 식용유 → 튀김기름·콩기름).
    for pat, excl, targets in _KEYWORD_RULES:
        if pat.search(base) and not (excl and excl.search(base)):
            for t in targets:
                found = _lookup_exact_name(conn, t, lookup_cache)
                if found and found[0] not in {h[0] for h in hits}:
                    hits.append((found[0], found[1], "keyword"))
    return hits


# ─────────────────────────────────────────────────────────────────
# 집계
# ─────────────────────────────────────────────────────────────────
def product_price_per_unit(rows):
    """한 상품의 행들 → 판매점별(점포 내 조사일 중앙값) 가격의 중앙값, 최신 조사일."""
    by_store = defaultdict(list)
    for r in rows:
        by_store[r["판매업소"]].append(r["price"])
    return median(median(v) for v in by_store.values()), max(r["조사일"] for r in rows)


def aggregate(products_by_ing):
    """
    {ing_id: [{product, qty, unit_price_per_g, regday}]} → {ing_id: (price_per_g, regday, used_products)}
    매칭된 전체 상품의 g당 가격 중앙값(상품 값 자체가 판매점 간 중앙값).
    예전 '용량 상위 절반' 규칙은 대용량이 프리미엄 제품일 때 왜곡됐다
    (두부: 380g 국산콩 제품만 남아 13.2원/g — 2026-09-30 폐기).
    """
    return {ing: (round(median(p["price_per_g"] for p in prods), 4), max(p["regday"] for p in prods), prods)
            for ing, prods in products_by_ing.items()}


# ─────────────────────────────────────────────────────────────────
# 적재
# ─────────────────────────────────────────────────────────────────
def market_priced_ids(engine):
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(
            text("SELECT DISTINCT ingredient_id FROM ingredient_price WHERE source = ANY(:s)"),
            {"s": list(MARKET_SOURCES)})}


def load(engine, results, names):
    stats = defaultdict(int)
    with engine.begin() as conn:
        stats["deleted_on_market"] = conn.execute(text("""
            DELETE FROM ingredient_price p WHERE p.source = :src
              AND EXISTS (SELECT 1 FROM ingredient_price m
                          WHERE m.ingredient_id = p.ingredient_id AND m.source = ANY(:market))
        """), {"src": SOURCE_NAME, "market": list(MARKET_SOURCES)}).rowcount
        for ing, (price, regday, top) in results.items():
            used = ", ".join(f"{p['product']}" for p in top)
            notes = (f"참가격 {regday} 소매 — 매칭 {len(top)}개 상품 g당 중앙값 "
                     f"(상품={used})")[:1000]
            row = conn.execute(text("""
                INSERT INTO ingredient_price (ingredient_id, price_per_g, price_date, source, notes)
                VALUES (:ing, :price, :pdate, :src, :notes)
                ON CONFLICT (ingredient_id, price_date) DO UPDATE
                    SET price_per_g = EXCLUDED.price_per_g, notes = EXCLUDED.notes
                    WHERE ingredient_price.source = :src
                RETURNING (xmax = 0) AS is_insert
            """), {"ing": ing, "price": price, "pdate": regday, "src": SOURCE_NAME, "notes": notes}).fetchone()
            stats["skipped" if row is None else ("inserted" if row.is_insert else "updated")] += 1
    return stats


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="참가격(한국소비자원) 가공식품·양념 원가 적재 (preview/load)")
    ap.add_argument("--mode", choices=["preview", "load"], default="preview")
    ap.add_argument("--csv", type=Path, default=None, help="원본 CSV (생략 시 data/raw 최신 파일)")
    args = ap.parse_args()

    path = args.csv or latest_raw_csv()
    if path is None or not Path(path).exists():
        print(f"[오류] 참가격 CSV 가 없습니다. 공공데이터포털 15083256 최신 월 CSV 를 data/raw/{RAW_GLOB} 로 저장하세요.")
        sys.exit(1)
    df = read_csv(path)
    total = len(df)
    df = df[(df["세일여부"].fillna("") != "Y") & (df["원플러스원"].fillna("") != "Y")].copy()
    df["price"] = pd.to_numeric(df["판매가격"], errors="coerce")
    df = df[df["price"] > 0]
    print(f"  원본: {Path(path).name} {total:,}행 → 세일·1+1·가격결측 제외 {len(df):,}행, 상품 {df['상품명'].nunique()}종")

    engine = get_engine()
    market_ids = market_priced_ids(engine)
    lookup_cache = {}
    products_by_ing = defaultdict(list)
    unmatched, unit_fail, density_log, market_skipped = [], [], [], defaultdict(set)
    names = {}

    with engine.connect() as conn:
        for product, g in df.groupby("상품명"):
            base, paren = split_product(product)
            maker = str(g["제조사"].iloc[0] or "")
            hits = match_product(conn, base, maker, lookup_cache)
            if not hits:
                unmatched.append((product, maker, len(g)))
                continue
            qty = parse_quantity(paren)
            if qty is None:
                unit_fail.append((product, paren, "; ".join(h[1] for h in hits)))
                continue
            amount, unit = qty
            price, regday = product_price_per_unit(g.to_dict("records"))
            for ing, ing_name, via in hits:
                names[ing] = ing_name
                if ing in market_ids:
                    market_skipped[ing_name].add(product)
                    continue
                grams = amount
                if unit == "ml":
                    density = DENSITY_G_PER_ML.get(ing_name)
                    if density is None:
                        density = 1.0
                        density_log.append((product, ing_name, amount))
                    grams = amount * density
                products_by_ing[ing].append({"product": product, "grams": grams, "via": via,
                                             "price_per_g": price / grams, "regday": regday})

    results = aggregate(products_by_ing)

    UNMATCHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = Path(path).stem.split("_")[-1]
    preview_path = UNMATCHED_LOG_DIR / f"chamgagyeok_price_match_preview_{stamp}.csv"
    with open(preview_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ingredient_id", "ingredient_name", "price_per_g", "regday", "used_in_price",
                    "product", "grams", "product_price_per_g", "match_via"])
        for ing, prods in sorted(products_by_ing.items(), key=lambda kv: names[kv[0]]):
            price, regday, top = results[ing]
            top_ids = {id(p) for p in top}
            for p in sorted(prods, key=lambda p: -p["grams"]):
                w.writerow([ing, names[ing], price, regday, "Y" if id(p) in top_ids else "",
                            p["product"], round(p["grams"], 1), round(p["price_per_g"], 4), p["via"]])
    unmatched_path = UNMATCHED_LOG_DIR / f"chamgagyeok_price_unmatched_{stamp}.csv"
    with open(unmatched_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["product", "maker", "rows"])
        w.writerows(sorted(unmatched))
    fail_path = UNMATCHED_LOG_DIR / f"chamgagyeok_price_unit_fail_{stamp}.csv"
    with open(fail_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kind", "product", "detail", "ingredient"])
        for product, paren, ing in unit_fail:
            w.writerow(["용량 해석 불가(개수 단위 등)", product, paren, ing])
        for product, ing, ml in density_log:
            w.writerow(["비중표 없음 → 1.0 가정", product, f"{ml}ml", ing])

    print(f"  매칭 상품: {df['상품명'].nunique() - len(unmatched)}종 / 미매칭 {len(unmatched)}종 / "
          f"용량 해석 불가 {len(unit_fail)}종 / 비중 1.0 가정 {len(density_log)}건")
    print(f"  시세(가락·KAMIS) 있어 건너뜀: {len(market_skipped)}개 재료 ({', '.join(sorted(market_skipped))})")
    print(f"  적재 대상 재료: {len(results)}개")
    print(f"\n  매칭 미리보기 CSV: {preview_path}\n  미매칭 CSV: {unmatched_path}\n  환산 실패 CSV: {fail_path}")

    if args.mode == "preview":
        print("\n  [preview 모드] DB 미반영. 검수 후 --mode load")
        return
    stats = load(engine, results, names)
    print(f"\n  적재: 신규 {stats['inserted']} / 갱신 {stats['updated']} / 같은 날 타 소스 있어 건너뜀 {stats['skipped']}"
          f" / 시세 생긴 재료의 기존 참가격 행 삭제 {stats['deleted_on_market']}")


if __name__ == "__main__":
    main()
