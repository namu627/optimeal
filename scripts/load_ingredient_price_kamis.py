#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_ingredient_price_kamis.py
목적: KAMIS(농산물유통정보) OpenAPI '일별 부류별 가격'으로 축산·곡류·청과·수산 원가를 받아
      ingredient_price 에 적재한다. 도매(02)를 우선하고 도매가 없는 재료만 소매(01)로 채운다.
      source 는 'KAMIS_W'(도매) / 'KAMIS_R'(소매) 로 구분한다.
      가락시장 로더(load_ingredient_price.py)는 청과·수산 도매만 있어 축산·곡류가 비어 있다.

실행 (preview → load 2단계, 가락 로더와 동일):
  1) docker exec optimeal_app python scripts/load_ingredient_price_kamis.py --mode preview
     → DB 미반영. data/external/kamis_price_match_preview_{날짜}.csv 로 매칭 결과 검수.
     → 오매칭 품목은 data/external/kamis_price_match_exclude.csv 에 kamis_name 으로 기록.
  2) docker exec optimeal_app python scripts/load_ingredient_price_kamis.py --mode load
  (--regday 2026-09-29 로 시작일 지정 가능. 생략 시 오늘부터, 부류별로 당일 가격이 있는 직전 영업일까지 폴백)

규칙:
  - 매칭(정규화·동의어·신뢰도 등급)·kg/g 환산·preview/제외목록 방식은 가락 로더 함수를 그대로 import 해 쓴다.
    KAMIS 는 품목(item)|품종(kind) 두 단계라 축산·곡류는 명시 별칭(KAMIS_ALIASES)을 먼저 적용한다.
  - p_convert_kg_yn=Y 면 kind_name 끝 괄호 '(1kg)'가 가격 기준 단위다(unit 컬럼은 조사 포장 단위일 뿐).
    '(1포기)', '(10개)', '(1마리)' 같은 개수 단위는 가락 로더처럼 건너뛰되,
    중량이 사실상 규격화된 품목만 PIECE_WEIGHT_G 로 환산한다(계란·우유·김).
  - 도매 우선: 재료 단위로, 도매 행이 하나라도 매칭되면 도매만 쓰고 소매는 버린다.
    축산물(500)은 API 에 도매 표가 없다(02 로 불러도 소매와 같은 값이 온다) → 소매만 조회.
    도매에는 같은 품목에 수입·중국산 품종이 섞여 오므로 국산 품종이 있으면 수입 품종은 버린다.
  - 가락 가격이 있는 재료에는 소매(KAMIS_R)를 넣지 않는다(도매끼리는 최신 조사일이 이기게 둔다).
    적재 시 이 규칙에 걸리는 기존 KAMIS_R 행과 1차 적재의 구분 없는 source='KAMIS' 행을 지운다.
  - ingredient_price UNIQUE(ingredient_id, price_date, source)(migrations/v5) — 출처별로 행을 따로 둔다.
    같은 조사일 재실행은 자기 출처 행만 갱신되어 멱등. 어느 출처를 쓸지는 출처 우선순위
    (KAMIS_W > 가락 > KAMIS_R > …, scripts/price_priority.py · csp_solver.PRICE_SOURCE_PRIORITY)가 정한다.
  - 응답 JSON 의 condition 에 인증키가 그대로 되돌아오므로 원문 응답은 절대 출력/저장하지 않는다.
"""

import argparse
import csv
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests
from sqlalchemy import text

from load_ingredient_price import SOURCE_NAME as GARAK_SOURCE
from load_ingredient_price import (
    CONFIDENCE_SORT_ORDER,
    UNMATCHED_LOG_DIR,
    _lookup_exact_name,
    get_engine,
    match_ingredient_id,
    normalize_strip_modifiers,
    normalize_strip_parens,
    parse_unit_to_grams,
)

# ─────────────────────────────────────────────────────────────────
# KAMIS API 설정
# ─────────────────────────────────────────────────────────────────
KAMIS_API_URL = "http://www.kamis.or.kr/service/price/xml.do"
CATEGORY_CODES = [
    ("100", "식량작물"), ("200", "채소류"), ("300", "특용작물"),
    ("400", "과일류"), ("500", "축산물"), ("600", "수산물"),
]
# 도매(02) → 소매(01) 순. 값은 (p_product_cls_code, source, 표시명).
PRODUCT_CLASSES = [("02", "KAMIS_W", "도매"), ("01", "KAMIS_R", "소매")]
SOURCE_BY_CLS = {code: source for code, source, _ in PRODUCT_CLASSES}
LABEL_BY_CLS = {code: label for code, _, label in PRODUCT_CLASSES}
LEGACY_SOURCE = "KAMIS"      # 2026-09-30 1차 적재(소매, 도매/소매 구분 없음) — load 시 정리 대상
# 도매 표가 없는 부류. 축산물은 02 로 호출해도 소매와 똑같은 값이 온다(2026-09-30 전 행 일치 확인).
WHOLESALE_UNAVAILABLE = {"500"}
MAX_FALLBACK_DAYS = 10       # 주말·공휴일(연휴) 폴백 한도
REQUEST_DELAY_SEC = 0.4
EXCLUDE_LIST_PATH = UNMATCHED_LOG_DIR / "kamis_price_match_exclude.csv"

# 같은 품목·품종에 등급이 여러 개면 이 순서로 하나를 고른다(가락 로더의 '상' 등급과 같은 취지).
#   소 1++/1+/1 → '1등급'(급식 납품 현실), 수산 大/中/小 → '中'. 목록에 없으면 모든 등급 평균.
RANK_PREFERENCE = ["상품", "1등급", "中", "L과", "M과"]

# 품종명에 이 표시가 있으면 수입 품종. 같은 품목에 국산 품종이 있으면 버린다(도매 '콩|흰 콩(수입)' 등).
IMPORT_MARKERS = ("수입", "중국", "페루", "인도")

# 개수·부피 단위 → 1단위 g. 규격이 사실상 고정된 품목만 둔다(나머지 개수 단위는 가락 로더처럼 skip).
#   계란: 특란 60g 이상(축산물 등급 기준 특란 60~68g) → 하한 60g
#   우유: 1L ≈ 1030g(비중 1.03)
#   김  : 1속(100장) ≈ 250g → 2.5g/장 (도매는 '1속' 단위)
PIECE_WEIGHT_G = {
    ("계란", "구"): 60,
    ("우유", "L"): 1030,
    ("김", "장"): 2.5,
    ("김", "속"): 250,
}

# (item_name, kind_name) → 가격을 붙일 우리 재료명들(존재하는 것 모두에 적용).
#   kind_name 을 None 으로 두면 그 품목의 모든 품종에 적용.
#   축산물은 부위가 곧 가격이라 이름 규칙으로 맞추면 '갈비'가 소/돼지 사이에서 섞이므로 전부 여기서 명시한다.
#   일반명 '소고기'는 급식 주 사용 부위(국거리 양지·불고기 설도), '돼지고기'는 앞다리(전지) 기준.
KAMIS_ALIASES = {
    ("소", "안심"): ["소고기(안심)"],
    ("소", "등심"): ["소고기(등심)"],
    ("소", "설도"): ["소고기", "소고기(불고기용)", "소불고기", "소고기(다짐육)"],
    ("소", "양지"): ["소고기", "소고기(양지)"],
    ("소", "갈비"): ["소고기(갈비)", "찜갈비"],
    ("수입 소고기", "갈비"): ["LA갈비"],
    ("돼지", "앞다리"): ["돼지고기", "돼지고기(앞다리)", "돼지고기(전지)",
                         "돼지고기(다짐육)", "돼지고기(불고기용)"],
    ("돼지", "삼겹살"): ["돼지고기(삼겹살)", "삼겹살"],
    ("돼지", "갈비"): ["돼지고기(갈비)", "돼지고기(뼈갈비)"],
    ("돼지", "목심"): ["돼지고기(목심)", "돼지고기(목살)"],
    ("닭", "육계(kg)"): ["닭고기"],
    # 계란은 급식 대량구매에 가까운 30구 가격만 쓴다(10구는 개당 약 1.8배라 과대).
    ("계란", "특란30구"): ["달걀", "계란", "삶은달걀"],
    ("우유", "흰우유"): ["우유", "따뜻한 우유"],
    ("쌀", None): ["쌀", "쌀(백미)", "백미", "멥쌀"],
    ("찹쌀", None): ["찹쌀", "쌀(찹쌀)"],
    ("현미", None): ["현미", "쌀(현미)"],
    ("보리쌀", None): ["보리쌀", "찰보리", "보리"],
    ("콩", "흰 콩(국산)(1kg)"): ["콩", "흰콩", "대두", "백태"],
    ("고구마", "밤(1kg)"): ["고구마", "밤고구마"],
    ("상추", "적(1kg)"): ["적상추", "상추"],
    ("상추", "청(1kg)"): ["청상추", "상추"],
    ("피망", "청(1kg)"): ["피망", "청피망"],
    ("풋고추", "풋고추(녹광 등)(1kg)"): ["풋고추", "청고추"],
    ("붉은고추", None): ["홍고추", "붉은고추"],
    ("건고추", None): ["건고추", "마른고추"],
    ("고춧가루", "국산(1kg)"): ["고춧가루"],
    ("깐마늘(국산)", None): ["마늘", "깐마늘", "다진마늘"],
    ("참깨", "백색(국산)(1kg)"): ["참깨", "통깨", "깨"],
    ("땅콩", "국산(1kg)"): ["땅콩"],
    ("마른멸치", None): ["멸치", "마른멸치", "국물용멸치"],
    ("마른미역", None): ["미역", "마른미역", "건미역"],
    ("건다시마", None): ["다시마", "건다시마"],
    ("김", "마른김(10장)"): ["김", "마른김", "김밥용 김"],
    ("김", "마른김(1속)"): ["김", "마른김", "김밥용 김"],
    ("김", "구운김(10장)"): ["구운김"],
    ("홍합", "깐홍합(냉장)(1kg)"): ["홍합살", "깐홍합"],
    ("홍합", "안깐홍합(냉장)(1kg)"): ["홍합"],
    ("명태", "냉동가공(1kg)"): ["명태", "동태"],
    ("명태", "냉동(1kg)"): ["명태", "동태"],
    ("물오징어", None): ["오징어", "물오징어"],
    ("알배기배추", None): ["알배추", "알배기배추"],
    ("고등어필렛", "국산(1kg)"): ["고등어필렛", "손질된 고등어"],
}
# 이 품목들은 별칭에 없는 품종이면 이름 규칙 매칭도 하지 않는다
#   ('소'·'닭' 자체가 엉뚱한 재료로 잡히거나, 수입 축산물이 국산 재료 가격에 섞이는 것 방지).
ALIAS_ONLY_ITEMS = {"소", "돼지", "수입 소고기", "수입 돼지고기", "닭", "계란", "우유", "혼식곡"}
# 품종별 별칭이 있는 품목의 나머지 품종은 품목명으로 폴백하지 않는다
#   ('고춧가루|중국'이 국산 기준 '고춧가루'에, '풋고추|오이맛고추'가 '풋고추'에 섞이는 것 방지).
ITEMS_WITH_KIND_ALIASES = {item for item, kind in KAMIS_ALIASES if kind is not None}

_TRAILING_UNIT_RE = re.compile(r"\((\d+(?:\.\d+)?)\s*([^\d()]+)\)\s*$")


def get_kamis_credentials():
    """.env 의 KAMIS_CERT_ID / KAMIS_CERT_KEY. 값은 어디에도 출력하지 않는다."""
    return os.getenv("KAMIS_CERT_ID"), os.getenv("KAMIS_CERT_KEY")


# ─────────────────────────────────────────────────────────────────
# API 호출
# ─────────────────────────────────────────────────────────────────
def fetch_category(session, cls_code, category_code, regday, cert_id, cert_key):
    """한 구분(도매/소매)·부류·조사일의 item 리스트. 실패/데이터 없음이면 []."""
    params = {
        "action": "dailyPriceByCategoryList",
        "p_product_cls_code": cls_code,
        "p_item_category_code": category_code,
        "p_regday": regday.strftime("%Y-%m-%d"),
        "p_convert_kg_yn": "Y",
        "p_returntype": "json",
        "p_cert_key": cert_key,
        "p_cert_id": cert_id,
    }
    try:
        resp = session.get(KAMIS_API_URL, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError):
        # 예외 메시지에 인증키가 담긴 URL 이 들어갈 수 있어 str(e)를 출력하지 않는다.
        print(f"  [경고] API 호출 실패 ({LABEL_BY_CLS[cls_code]}, 부류={category_code}, 조사일={regday}) — 건너뜀")
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    # 데이터가 없으면 data 가 ["001"] 같은 리스트로 오기도 한다.
    if not isinstance(data, dict) or data.get("error_code") != "000":
        return []
    items = data.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items


def parse_price(raw):
    """'3,149' → 3149.0, '-'·'0'·빈값 → None."""
    try:
        value = float(str(raw or "").replace(",", "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def collect_rows(session, cert_id, cert_key, start):
    """
    도매·소매 × 부류별로 start 부터 하루씩 거슬러 올라가 당일(dpr1) 가격이 있는 첫 영업일을 찾는다
    (주말은 호출 생략). 부류마다 따로 폴백하는 이유: KAMIS 는 당일 오후에 부류별로 순차 게시해
    같은 날 축산만 먼저 올라와 있기도 하다.
    반환: [(cls_code, regday, category_label, item), ...]
    """
    rows = []
    for cls_code, _, cls_label in PRODUCT_CLASSES:
        for code, label in CATEGORY_CODES:
            if cls_code == "02" and code in WHOLESALE_UNAVAILABLE:
                print(f"  [{cls_label}·{label}] 도매 표 없음 — 소매만 사용")
                continue
            for offset in range(MAX_FALLBACK_DAYS + 1):
                day = start - timedelta(days=offset)
                if day.weekday() >= 5:
                    continue
                items = [it for it in fetch_category(session, cls_code, code, day, cert_id, cert_key)
                         if parse_price(it.get("dpr1")) is not None]
                time.sleep(REQUEST_DELAY_SEC)
                if items:
                    print(f"  [{cls_label}·{label}] 조사일 {day}: {len(items)}건")
                    rows.extend((cls_code, day, label, it) for it in items)
                    break
            else:
                print(f"  [경고] [{cls_label}·{label}] 최근 {MAX_FALLBACK_DAYS}일 안에 당일 가격 없음 — 건너뜀")
    return rows


# ─────────────────────────────────────────────────────────────────
# 단위 환산
# ─────────────────────────────────────────────────────────────────
def unit_to_grams(item_name, kind_name, unit):
    """
    가격 기준 단위 → 그램. kind_name 끝 괄호(환산 후 단위)를 우선, 없으면 unit 컬럼(축산물).
    kg/g 는 가락 로더 parse_unit_to_grams 로, 개수·부피 단위는 PIECE_WEIGHT_G 로. 실패 시 None.
    """
    m = _TRAILING_UNIT_RE.search(kind_name or "")
    if m:
        qty, word = float(m.group(1)), m.group(2).strip()
    else:
        m2 = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(.+?)\s*$", unit or "")
        if not m2:
            return None
        qty, word = float(m2.group(1)), m2.group(2)

    grams = parse_unit_to_grams(word, qty)
    if grams:
        return grams
    piece = PIECE_WEIGHT_G.get((item_name, word))
    return qty * piece if piece else None


# ─────────────────────────────────────────────────────────────────
# 등급 선택 → (품목|품종) 대표 단가
# ─────────────────────────────────────────────────────────────────
def build_price_samples(rows):
    """
    rows → ({(cls, category, item, kind): {"price_per_g", "rank", "regday"}}, unit_parse_failures, dropped_imports)
    같은 품목·품종의 여러 등급 중 RANK_PREFERENCE 순으로 하나, 없으면 평균.
    같은 구분·품목에 국산 품종이 있으면 수입 품종(IMPORT_MARKERS)은 버린다.
    """
    def is_import(kind_name):
        return any(m in kind_name for m in IMPORT_MARKERS)

    has_domestic = {(cls, str(it.get("item_name") or "").strip())
                    for cls, _, _, it in rows if not is_import(str(it.get("kind_name") or ""))}
    by_kind = defaultdict(list)
    unit_parse_failures = []
    dropped_imports = set()
    regday_of = {}
    for cls, regday, category, it in rows:
        item_name = str(it.get("item_name") or "").strip()
        kind_name = str(it.get("kind_name") or "").strip()
        rank = str(it.get("rank") or "").strip()
        if is_import(kind_name) and (cls, item_name) in has_domestic:
            dropped_imports.add((LABEL_BY_CLS[cls], item_name, kind_name))
            continue
        grams = unit_to_grams(item_name, kind_name, it.get("unit"))
        if not grams:
            unit_parse_failures.append((LABEL_BY_CLS[cls], item_name, kind_name, rank, it.get("unit")))
            continue
        price_per_g = parse_price(it.get("dpr1")) / grams
        key = (cls, category, item_name, kind_name)
        by_kind[key].append((rank, price_per_g))
        regday_of[key] = regday

    samples = {}
    for key, ranked in by_kind.items():
        chosen = None
        for pref in RANK_PREFERENCE:
            hits = [p for r, p in ranked if r == pref]
            if hits:
                chosen = (pref, sum(hits) / len(hits))
                break
        if chosen is None:
            chosen = ("/".join(sorted({r for r, _ in ranked})), sum(p for _, p in ranked) / len(ranked))
        samples[key] = {"rank": chosen[0], "price_per_g": round(chosen[1], 4), "regday": regday_of[key]}
    return samples, unit_parse_failures, sorted(dropped_imports)


# ─────────────────────────────────────────────────────────────────
# 매칭: 별칭 → 품종명 → 품목명 (각 후보는 가락 로더 match_ingredient_id 로)
# ─────────────────────────────────────────────────────────────────
def _alias_names(item_name, kind_name):
    return KAMIS_ALIASES.get((item_name, kind_name)) or KAMIS_ALIASES.get((item_name, None))


def _clean(name):
    return normalize_strip_modifiers(normalize_strip_parens(name))


def name_candidates(item_name, kind_name):
    """
    별칭이 없을 때의 이름 후보. 품종명은 품목명과 끝 두 글자가 같을 때만 쓴다
    ('청양고추'←풋고추, '대파'←파 는 허용 / '밤'←고구마, '국산'←고춧가루 는 차단).
    ITEMS_WITH_KIND_ALIASES 품목은 품목명 후보를 넣지 않는다.
    """
    item = _clean(item_name) or item_name
    kind = _clean(kind_name)
    tail = item[-2:]
    candidates = []
    if kind and kind != item and kind.endswith(tail):
        candidates.append(kind)
    if item_name not in ITEMS_WITH_KIND_ALIASES:
        candidates.append(item)
    return candidates


def build_match_results(engine, samples):
    """
    samples → (matched_results, unmatched)
    matched_results: 가락 로더와 같은 키 + kamis_name/category/rank/matched_via.
    별칭은 목록 중 DB 에 있는 재료 전부에, 이름 규칙은 처음 잡힌 하나에 붙인다.
    """
    matched, unmatched = [], []
    match_cache, lookup_cache = {}, {}

    with engine.connect() as conn:
        for (cls, category, item_name, kind_name), s in sorted(samples.items()):
            kamis_name = f"{item_name}|{kind_name}"
            base = {"cls": cls, "kamis_name": kamis_name, "category": category, "rank": s["rank"],
                    "avg_price_per_g": s["price_per_g"], "regday": s["regday"]}

            aliases = _alias_names(item_name, kind_name)
            hits = []
            if aliases:
                for alias in aliases:
                    found = _lookup_exact_name(conn, alias, lookup_cache)
                    if found and found[0] not in {h[0] for h in hits}:
                        hits.append((found[0], found[1], "alias", "alias"))
            elif item_name not in ALIAS_ONLY_ITEMS:
                for cand in name_candidates(item_name, kind_name):
                    ing_id, ing_name, conf = match_ingredient_id(conn, cand, match_cache, lookup_cache)
                    if ing_id is not None:
                        via = "kind" if cand != (_clean(item_name) or item_name) else "item"
                        hits.append((ing_id, ing_name, conf, via))
                        break

            if not hits:
                unmatched.append({**base, "reason": "alias_only" if item_name in ALIAS_ONLY_ITEMS else "no_match"})
                continue
            for ing_id, ing_name, conf, via in hits:
                matched.append({**base, "matched_ingredient_id": ing_id, "matched_ingredient_name": ing_name,
                                "confidence": conf, "matched_via": via})
    return matched, unmatched


# ─────────────────────────────────────────────────────────────────
# 도매 우선 · 가락 보존 결정
# ─────────────────────────────────────────────────────────────────
def garak_ingredient_ids(engine):
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(
            text("SELECT DISTINCT ingredient_id FROM ingredient_price WHERE source = :s"), {"s": GARAK_SOURCE})}


def resolve_sources(matched, garak_ids):
    """
    각 매칭 행에 decision 을 단다: 'load' / 'skip:wholesale_preferred' / 'skip:garak_kept'.
    재료에 도매 행이 하나라도 있으면 도매만, 없으면 소매 — 단 가락 가격이 있는 재료는 소매를 넣지 않는다.
    """
    has_wholesale = {r["matched_ingredient_id"] for r in matched if r["cls"] == "02"}
    for r in matched:
        ing = r["matched_ingredient_id"]
        if r["cls"] == "01" and ing in has_wholesale:
            r["decision"] = "skip:wholesale_preferred"
        elif r["cls"] == "01" and ing in garak_ids:
            r["decision"] = "skip:garak_kept"
        else:
            r["decision"] = "load"
    return matched


# ─────────────────────────────────────────────────────────────────
# CSV 산출물 (가락 로더와 같은 위치·형식, 파일명 접두어 kamis_)
# ─────────────────────────────────────────────────────────────────
def write_preview_csv(matched):
    UNMATCHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = UNMATCHED_LOG_DIR / f"kamis_price_match_preview_{date.today():%Y%m%d}.csv"
    rows = sorted(matched, key=lambda r: (CONFIDENCE_SORT_ORDER.get(r["confidence"], 99), r["kamis_name"], r["cls"]))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kamis_name", "price_class", "category", "rank", "matched_ingredient_id",
                    "matched_ingredient_name", "confidence", "matched_via", "price_per_g", "regday", "decision"])
        for r in rows:
            w.writerow([r["kamis_name"], LABEL_BY_CLS[r["cls"]], r["category"], r["rank"], r["matched_ingredient_id"],
                        r["matched_ingredient_name"], r["confidence"], r["matched_via"], r["avg_price_per_g"],
                        r["regday"], r.get("decision", "")])
    return path


def write_failure_logs(unmatched, unit_parse_failures, dropped_imports):
    UNMATCHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    unmatched_path = UNMATCHED_LOG_DIR / f"kamis_price_unmatched_{stamp}.csv"
    with open(unmatched_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kamis_name", "price_class", "category", "rank", "price_per_g", "regday", "reason"])
        for r in unmatched:
            w.writerow([r["kamis_name"], LABEL_BY_CLS[r["cls"]], r["category"], r["rank"], r["avg_price_per_g"],
                        r["regday"], r["reason"]])
        for cls_label, item_name, kind_name in dropped_imports:
            w.writerow([f"{item_name}|{kind_name}", cls_label, "", "", "", "", "import_kind_dropped"])

    unit_fail_path = UNMATCHED_LOG_DIR / f"kamis_price_unit_parse_fail_{stamp}.csv"
    with open(unit_fail_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["price_class", "item_name", "kind_name", "rank", "unit"])
        w.writerows(unit_parse_failures)
    return unmatched_path, unit_fail_path


def read_exclude_list():
    """kamis_price_match_exclude.csv 의 kamis_name('품목|품종') 집합. 파일이 없으면 빈 집합."""
    if not EXCLUDE_LIST_PATH.exists():
        return set()
    with open(EXCLUDE_LIST_PATH, newline="", encoding="utf-8-sig") as f:
        return {(row.get("kamis_name") or "").strip() for row in csv.DictReader(f)} - {""}


# ─────────────────────────────────────────────────────────────────
# 적재
# ─────────────────────────────────────────────────────────────────
def load_ingredient_price(engine, matched):
    """
    decision='load' 행을 ingredient_id 별로 모아 평균(여러 품종이 한 재료로 모이면 단순평균) 후 upsert.
    한 재료의 행은 resolve_sources 덕에 전부 도매이거나 전부 소매다.
    price_date 는 KAMIS 조사일(여러 부류에서 모이면 그중 최신).
    같은 날짜에 다른 source(가락 등) 행이 있으면 건드리지 않고 skipped 로 센다.

    같은 트랜잭션에서 먼저 정리한다:
      - source='KAMIS'(1차 적재, 도매/소매 구분 없음) 행 전부
      - 가락 가격이 있는 재료의 KAMIS_R 행 (가락 보존 규칙)
    반환: {inserted, updated, skipped, deleted_legacy, deleted_retail_on_garak, loaded_KAMIS_W, loaded_KAMIS_R}
    """
    grouped = defaultdict(list)
    for r in matched:
        if r.get("decision", "load") == "load":
            grouped[r["matched_ingredient_id"]].append(r)

    stats = defaultdict(int)
    with engine.begin() as conn:
        stats["deleted_legacy"] = conn.execute(
            text("DELETE FROM ingredient_price WHERE source = :s"), {"s": LEGACY_SOURCE}).rowcount
        stats["deleted_retail_on_garak"] = conn.execute(
            text("""
                DELETE FROM ingredient_price p
                WHERE p.source = :retail
                  AND EXISTS (SELECT 1 FROM ingredient_price g
                              WHERE g.ingredient_id = p.ingredient_id AND g.source = :garak)
            """), {"retail": SOURCE_BY_CLS["01"], "garak": GARAK_SOURCE}).rowcount

        for ing_id, rows in grouped.items():
            cls = rows[0]["cls"]
            price = round(sum(r["avg_price_per_g"] for r in rows) / len(rows), 4)
            regday = max(r["regday"] for r in rows)
            names = ", ".join(sorted({f"{r['kamis_name']}({r['rank']})" for r in rows}))
            confs = "/".join(sorted({r["confidence"] for r in rows}))
            notes = f"KAMIS {LABEL_BY_CLS[cls]} {regday} 당일가 (품목|품종={names}, 신뢰도={confs})"
            row = conn.execute(
                text("""
                    INSERT INTO ingredient_price (ingredient_id, price_per_g, price_date, source, notes)
                    VALUES (:ing_id, :price, :pdate, :source, :notes)
                    ON CONFLICT (ingredient_id, price_date, source) DO UPDATE
                        SET price_per_g = EXCLUDED.price_per_g,
                            notes       = EXCLUDED.notes
                    RETURNING (xmax = 0) AS is_insert
                """),
                {"ing_id": ing_id, "price": price, "pdate": regday, "source": SOURCE_BY_CLS[cls], "notes": notes},
            ).fetchone()
            if row is None:
                stats["skipped"] += 1
            elif row.is_insert:
                stats["inserted"] += 1
            else:
                stats["updated"] += 1
            stats[f"loaded_{SOURCE_BY_CLS[cls]}"] += 1
    return stats


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="KAMIS 도매·소매가격 재료 원가 적재 (preview/load 2단계)")
    parser.add_argument("--mode", choices=["preview", "load"], default="preview",
                        help="preview: CSV 만 출력(기본) / load: 제외목록 반영 후 DB upsert")
    parser.add_argument("--regday", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(), default=None,
                        help="조사 시작일 YYYY-MM-DD (생략 시 오늘). 부류별로 직전 영업일까지 폴백")
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 45)
    print("  OptiMeal KAMIS 도매·소매가격 원가 적재 스크립트")
    print(f"  모드: {args.mode}")
    print("=" * 45)

    cert_id, cert_key = get_kamis_credentials()
    if not cert_id or not cert_key:
        print("  [오류] .env 에 KAMIS_CERT_ID / KAMIS_CERT_KEY 값이 비어 있습니다.")
        sys.exit(1)

    session = requests.Session()
    rows = collect_rows(session, cert_id, cert_key, args.regday or date.today())
    if not rows:
        print(f"  [오류] 최근 {MAX_FALLBACK_DAYS}일 안에 당일 가격이 있는 조사일을 찾지 못했습니다.")
        sys.exit(1)
    print(f"  당일 가격 행: {len(rows)}건")

    samples, unit_parse_failures, dropped_imports = build_price_samples(rows)
    print(f"  품목|품종 대표가: {len(samples)}개 / 단위 환산 실패(개수 단위 등): {len(unit_parse_failures)}건"
          f" / 국산 있어 버린 수입 품종: {len(dropped_imports)}건")

    engine = get_engine()
    matched, unmatched = build_match_results(engine, samples)
    excluded = read_exclude_list()
    if excluded:
        before = len(matched)
        matched = [r for r in matched if r["kamis_name"] not in excluded]
        print(f"  제외 목록 반영: {before - len(matched)}건 제외")
    matched = resolve_sources(matched, garak_ingredient_ids(engine))

    counts = defaultdict(int)
    for r in matched:
        counts[r["confidence"]] += 1
    print("\n  매칭 결과 요약 (품목|품종 → 재료, confidence 별):")
    for tier in ("alias", "exact", "synonym", "high", "low"):
        print(f"    - {tier:<8}: {counts.get(tier, 0)}건")
    print(f"    - 매칭 실패 : {len(unmatched)}건")
    decided = defaultdict(set)
    for r in matched:
        key = r["decision"] if r["decision"] != "load" else f"load:{SOURCE_BY_CLS[r['cls']]}"
        decided[key].add(r["matched_ingredient_id"])
    print("\n  재료 단위 결정:")
    print(f"    - 도매 적재(KAMIS_W)       : {len(decided['load:KAMIS_W'])}개")
    print(f"    - 소매 적재(KAMIS_R)       : {len(decided['load:KAMIS_R'])}개")
    print(f"    - 소매 버림(도매 있음)     : {len(decided['skip:wholesale_preferred'])}개")
    print(f"    - 소매 버림(가락 가격 보존): {len(decided['skip:garak_kept'])}개")

    preview_path = write_preview_csv(matched)
    unmatched_path, unit_fail_path = write_failure_logs(unmatched, unit_parse_failures, dropped_imports)
    print(f"\n  매칭 미리보기 CSV: {preview_path}")
    print(f"  매칭 실패 CSV: {unmatched_path}")
    print(f"  단위 환산 실패 CSV: {unit_fail_path}")

    if args.mode == "preview":
        print("\n  [preview 모드] DB에는 적재하지 않았습니다.")
        print(f"  오매칭은 {EXCLUDE_LIST_PATH.name}(kamis_name 컬럼)에 적고 --mode load 로 다시 실행하세요.")
        return

    stats = load_ingredient_price(engine, matched)
    print("\n" + "=" * 45)
    print("  적재 완료")
    print("=" * 45)
    print(f"  정리: 1차 적재(source='KAMIS') {stats['deleted_legacy']}행 삭제 / "
          f"가락 재료의 소매 {stats['deleted_retail_on_garak']}행 삭제")
    print(f"  적재: 도매 {stats['loaded_KAMIS_W']}개 · 소매 {stats['loaded_KAMIS_R']}개 → "
          f"신규 {stats['inserted']}건 / 갱신 {stats['updated']}건 / 같은 날 타 소스 행 있어 건너뜀 {stats['skipped']}건")
    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM ingredient_price")).scalar()
    print(f"  ingredient_price 총 행 수: {cnt:,}행")


if __name__ == "__main__":
    main()
