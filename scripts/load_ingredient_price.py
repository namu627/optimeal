#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_ingredient_price.py
목적: 가락시장(서울시농수산식품공사) 주요품목가격 API에서 식재료 가격을 받아와
      기존 ingredient_price 테이블에 적재 (CREATE 없음, INSERT/upsert만 수행)

실행 (preview → load 2단계):
  1) docker exec optimeal_app python scripts/load_ingredient_price.py --mode preview
     → DB 미반영. data/external/price_match_preview_{날짜}.csv로 매칭 결과 검수.
     → 오매칭 품목은 data/external/price_match_exclude.csv에 pum_nm으로 기록.
  2) docker exec optimeal_app python scripts/load_ingredient_price.py --mode load
     → price_match_exclude.csv 반영 후 ingredient_price에 upsert 적재.

참고:
  - task1_ingredient_price_적재_지시서.md: 등급 '상'만 사용, U_NAME 기반 g당 단가 환산,
    ingredient/ingredient_synonym 매칭 실패 시 자동 생성 금지, upsert 적재.
  - task1_price_수정지시서_v2.md: 정규화 매칭 단계 + 신뢰도 등급, preview/load 분리,
    (pum_nm, 날짜) → pum_nm 2단계 평균으로 정확도 개선.
  - task1_price_수정지시서_v3_수입품목.md: 수입품목전용 API(data65, GARAK_IMPORT_ID)를
    추가 소스로 수집해 국산(data52) 샘플과 합침. 매칭/환산/upsert 로직은 그대로 재사용.
"""

import argparse
import csv
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

# ─────────────────────────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
UNMATCHED_LOG_DIR = BASE_DIR / "data" / "external"
EXCLUDE_LIST_PATH = UNMATCHED_LOG_DIR / "price_match_exclude.csv"

# ─────────────────────────────────────────────────────────────────
# 가락시장 API 설정
# ─────────────────────────────────────────────────────────────────
GARAK_API_URL = "http://www.garak.co.kr/homepage/publicdata/dataJsonOpen.do"
DATA_ID = "data52"          # 주요품목가격 (국산 위주)
DATA_ID_IMPORT = "data65"   # 수입품목전용 (v3 추가) — 응답 구조는 data52와 동일
D_CD_LIST = [("2", "청과"), ("3", "수산")]   # 가락시장 특성상 육류·곡류·가공품은 없을 수 있음
GRADE_FILTER = "상"          # 급식 납품 현실 기준 (특=과대추정, 하=품질미달)
LOOKBACK_DAYS = 30
PAGE_SIZE = 1000
REQUEST_DELAY_SEC = 0.4      # 서버 부하/차단 방지
SOURCE_NAME = "서울시농수산식품공사"

# U_NAME(거래단위) 문자열에서 찾을 중량 단위 → 그램 배수
UNIT_MULTIPLIER = {
    "킬로그램": 1000, "킬로": 1000, "키로그램": 1000, "키로": 1000,
    "kg": 1000, "KG": 1000, "Kg": 1000,
    "그램": 1, "그람": 1, "g": 1, "G": 1,
    "톤": 1_000_000,
}

# 재료명 정규화 매칭용 상태·산지 수식어 (단어 단위/접두어 제거 대상)
MODIFIER_WORDS = ["국산", "수입", "냉동", "활", "선", "냉", "깐", "햇", "건", "저장", "생"]

# confidence 등급별 미리보기 CSV 정렬 우선순위 (낮을수록 검수 우선)
CONFIDENCE_SORT_ORDER = {"low": 0, "high": 1, "synonym": 2, "exact": 3}


# ─────────────────────────────────────────────────────────────────
# DB 연결 (load_recipe_data.py의 get_engine 패턴 재사용)
# ─────────────────────────────────────────────────────────────────
def get_engine():
    """환경변수에서 PostgreSQL 연결 정보를 읽어 SQLAlchemy 엔진 반환."""
    host     = os.getenv("POSTGRES_HOST", "db")
    port     = os.getenv("POSTGRES_PORT", "5432")
    db       = os.getenv("POSTGRES_DB",   "optimeal")
    user     = os.getenv("POSTGRES_USER", "optimeal")
    password = os.getenv("POSTGRES_PASSWORD", "optimeal_dev_pw")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return create_engine(url)


def get_garak_credentials():
    """
    .env에서 가락시장 API 인증정보(GARAK_ID, GARAK_PASSWD)를 읽어 반환.
    값 자체는 어디에도 출력/로그하지 않는다.
    """
    return os.getenv("GARAK_ID"), os.getenv("GARAK_PASSWD")


def get_garak_import_credentials():
    """
    .env에서 수입품목전용 API 인증정보(GARAK_IMPORT_ID, GARAK_IMPORT_PASSWD)를 읽어 반환.
    기존 GARAK_ID/PASSWD(국산)와는 다른 별도 계정이므로 분리해서 읽는다.
    값 자체는 어디에도 출력/로그하지 않는다.
    """
    return os.getenv("GARAK_IMPORT_ID"), os.getenv("GARAK_IMPORT_PASSWD")


# ─────────────────────────────────────────────────────────────────
# 날짜 목록 구성: 최근 LOOKBACK_DAYS일(주말 제외 권장)
# ─────────────────────────────────────────────────────────────────
def build_date_list(lookback_days: int = LOOKBACK_DAYS):
    """오늘 기준 최근 lookback_days일(주말 제외)의 date 객체 리스트 반환 (최신순)."""
    dates = []
    offset = 0
    while len(dates) < lookback_days:
        candidate = date.today() - timedelta(days=offset)
        offset += 1
        if candidate.weekday() < 5:  # 0=월 ... 4=금 (주말 제외)
            dates.append(candidate)
    return dates


# ─────────────────────────────────────────────────────────────────
# (2) U_NAME 기반 price_per_g 단위 환산
# ─────────────────────────────────────────────────────────────────
def parse_unit_to_grams(u_name, unit_qty):
    """
    U_NAME(예: ' 10키로상자')과 UNIT_QTY(예: ' 10')로 총 그램수 계산.
    - U_NAME에서 중량 단위(키로/kg/그램/톤 등)를 찾아 배수를 정하고,
      수량은 UNIT_QTY를 우선 사용, 실패 시 U_NAME 내 숫자를 대신 사용한다.
    - '단', '속' 등 중량 단위가 아닌 개수 단위는 인식 불가 → None 반환 (상위에서 skip).
    """
    name = str(u_name or "").strip()
    if not name:
        return None

    unit_word = None
    for word in sorted(UNIT_MULTIPLIER, key=len, reverse=True):
        if word in name:
            unit_word = word
            break
    if unit_word is None:
        return None
    multiplier = UNIT_MULTIPLIER[unit_word]

    qty = None
    raw_qty = str(unit_qty or "").strip()
    if raw_qty:
        try:
            qty = float(raw_qty)
        except ValueError:
            qty = None
    if qty is None:
        m = re.search(r"(\d+(\.\d+)?)", name)
        if m:
            qty = float(m.group(1))

    if qty is None or qty <= 0:
        return None

    return qty * multiplier


# ─────────────────────────────────────────────────────────────────
# 가락시장 API 호출 (부류 d_cd × 날짜 p_ymd 단위, 페이징 처리)
# ─────────────────────────────────────────────────────────────────
def fetch_grade_items_for_date(session, d_cd, p_ymd_date, garak_id, garak_passwd,
                                dataid=DATA_ID, p_buryu=None, pagesize=PAGE_SIZE):
    """
    특정 dataid(가격 API 종류)·d_cd(부류)·p_ymd(날짜)에 대해 전체 페이지를 순회 호출.

    dataid로 주요품목가격(data52, 국산 위주)과 수입품목전용(data65, v3 추가)을
    같은 함수로 호출할 수 있도록 파라미터화했다. p_buryu는 data65 호출에서만
    필요한 부류 파라미터로 d_cd와 같은 값을 넣는다 (None이면 파라미터에서 제외되어
    기존 data52 호출과 동일하게 동작 — 국산 적재 로직은 변경 없음).

    ── (1) 등급 '상' 필터링 ──
    G_NAME_A(또는 E_NAME)이 GRADE_FILTER('상')인 행만 추려서 반환한다.
    (ingredient_id, price_date) UNIQUE 제약 때문에 재료·날짜당 가격이 하나여야
    하므로, 여러 등급이 섞이지 않도록 여기서 반드시 하나로 좁힌다.
    """
    p_ymd = p_ymd_date.strftime("%Y%m%d")
    p_jymd = (p_ymd_date - timedelta(days=1)).strftime("%Y%m%d")
    try:
        p_jjymd = p_ymd_date.replace(year=p_ymd_date.year - 1).strftime("%Y%m%d")
    except ValueError:
        # 2/29 윤년 보정
        p_jjymd = date(p_ymd_date.year - 1, 2, 28).strftime("%Y%m%d")

    results = []
    pageidx = 1

    while True:
        params = {
            "id": garak_id,
            "passwd": garak_passwd,
            "dataid": dataid,
            "pagesize": pagesize,
            "pageidx": pageidx,
            "p_ymd": p_ymd,
            "p_jymd": p_jymd,
            "p_jjymd": p_jjymd,
            "d_cd": d_cd,
            "p_pos_gubun": "1",
            "pum_nm": "",
            "portal.templet": "false",
        }
        if p_buryu is not None:
            params["p_buryu"] = p_buryu

        try:
            resp = session.get(GARAK_API_URL, params=params, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            # 주의: 예외 메시지에 요청 URL(=id/passwd 쿼리파라미터 포함)이 담길 수 있어
            # str(e)를 그대로 출력하지 않는다.
            print(f"  [경고] API 호출 실패 (dataid={dataid}, d_cd={d_cd}, p_ymd={p_ymd}, page={pageidx}) — 건너뜀")
            break

        # 실측 확인된 응답 구조: {"resultData": [ {...}, ... ]}
        # (혹시 모를 변형에 대비해 resultData를 최우선으로 두고 방어적으로 처리)
        if isinstance(payload, dict):
            items = (payload.get("resultData")
                     or payload.get("item")
                     or payload.get("items")
                     or payload.get("list")
                     or [])
        else:
            items = []
        if isinstance(items, dict):
            items = [items]
        if not items:
            break

        for it in items:
            grade = str(it.get("G_NAME_A") or it.get("E_NAME") or "").strip()
            if grade == GRADE_FILTER:
                results.append(it)

        if len(items) < pagesize:
            break  # 마지막 페이지

        pageidx += 1
        time.sleep(REQUEST_DELAY_SEC)

    return results


def collect_price_samples(session, garak_id, garak_passwd, garak_import_id=None, garak_import_passwd=None):
    """
    두 소스 × 청과(d_cd=2)·수산(d_cd=3) × 최근 30일(주말 제외)을 순회 호출하여
    (품목명, 날짜, price_per_g) 원시 샘플 리스트와 단위 파싱 실패 로그를 수집한다.
      - 소스 A: 주요품목가격(data52, GARAK_ID)        — 국산 위주 (기존 로직 그대로)
      - 소스 B: 수입품목전용(data65, GARAK_IMPORT_ID) — 수입 위주 (v3 추가)
    두 소스 결과는 같은 raw_samples 리스트에 합쳐지며, 이후 매칭·2단계 평균·
    upsert 로직(aggregate_daily_then_average, build_match_results, load_ingredient_price)은
    그대로 재사용한다. data65 응답에는 UNIT_QTY가 없지만 parse_unit_to_grams()가
    이미 U_NAME 숫자 추출로 처리하므로 추가 수정이 필요 없다.
    """
    date_list = build_date_list()
    raw_samples = []          # [(pum_nm, p_ymd_date, price_per_g), ...]
    unit_parse_failures = []  # [(pum_nm, u_name, unit_qty), ...]

    # ── API 호출부 확장: 국산(data52) + 수입(data65) 소스 목록 ──
    sources = [
        {"dataid": DATA_ID, "label": "국산(data52)", "id": garak_id, "passwd": garak_passwd, "use_p_buryu": False},
    ]
    if garak_import_id and garak_import_passwd:
        sources.append({
            "dataid": DATA_ID_IMPORT, "label": "수입(data65)",
            "id": garak_import_id, "passwd": garak_import_passwd, "use_p_buryu": True,
        })
    else:
        print("  [안내] GARAK_IMPORT_ID/PASSWD가 없어 수입품목전용(data65) 조회는 건너뜁니다.")

    for source in sources:
        for d_cd, d_cd_label in D_CD_LIST:
            print(f"  [{source['label']}] 부류: {d_cd_label}(d_cd={d_cd}) — 날짜 {len(date_list)}일 조회 시작")
            p_buryu = d_cd if source["use_p_buryu"] else None

            for p_ymd_date in date_list:
                items = fetch_grade_items_for_date(
                    session, d_cd, p_ymd_date, source["id"], source["passwd"],
                    dataid=source["dataid"], p_buryu=p_buryu,
                )

                for it in items:
                    pum_nm = str(it.get("PUM_NM_A") or "").strip()
                    if not pum_nm:
                        continue

                    raw_price = str(it.get("AV_P_A") or "").replace(",", "").strip()
                    try:
                        av_price = float(raw_price)
                    except ValueError:
                        continue
                    if av_price <= 0:
                        continue

                    total_grams = parse_unit_to_grams(it.get("U_NAME"), it.get("UNIT_QTY"))
                    if not total_grams:
                        unit_parse_failures.append((pum_nm, it.get("U_NAME"), it.get("UNIT_QTY")))
                        continue

                    price_per_g = round(av_price / total_grams, 4)
                    raw_samples.append((pum_nm, p_ymd_date, price_per_g))

                time.sleep(REQUEST_DELAY_SEC)

    return raw_samples, unit_parse_failures


# ─────────────────────────────────────────────────────────────────
# 개선 3: 30일 평균 로직 정리 — (품목,날짜) 대표값 → 품목별 날짜평균 2단계
# ─────────────────────────────────────────────────────────────────
def aggregate_daily_then_average(raw_samples):
    """
    (pum_nm, p_ymd_date, price_per_g) 원시 샘플을 2단계로 평균낸다.
      1) (pum_nm, price_date) 단위로 그 날의 대표 price_per_g를 구한다
         (같은 날 같은 품목이 여러 행이면 그 날 안에서 평균).
      2) pum_nm별로 '날짜별 대표값들'의 평균을 낸다 — 즉 거래일수 기준 평균이
         되어, 품목별 거래일수가 달라도 왜곡되지 않는다.
    반환: {pum_nm: (avg_price_per_g, sample_days)}  sample_days=평균에 쓰인 실제 거래일수
    """
    daily_values = defaultdict(list)  # {(pum_nm, date): [price_per_g, ...]}
    for pum_nm, p_ymd_date, price_per_g in raw_samples:
        daily_values[(pum_nm, p_ymd_date)].append(price_per_g)

    daily_representative = defaultdict(list)  # {pum_nm: [일별 대표가격, ...]}
    for (pum_nm, _p_ymd_date), prices in daily_values.items():
        day_repr = sum(prices) / len(prices)
        daily_representative[pum_nm].append(day_repr)

    daily_avg_by_pum_nm = {}
    for pum_nm, day_reprs in daily_representative.items():
        avg_price = round(sum(day_reprs) / len(day_reprs), 4)
        daily_avg_by_pum_nm[pum_nm] = (avg_price, len(day_reprs))

    return daily_avg_by_pum_nm


# ─────────────────────────────────────────────────────────────────
# 개선 1: 재료명 정규화 매칭 (완전일치 → 동의어 → 정규화 → 첫단어)
# ─────────────────────────────────────────────────────────────────
def normalize_strip_parens(name: str) -> str:
    """괄호와 그 안의 내용 제거. 예: '수박(일반)' → '수박', '(선)갈치' → '갈치'."""
    return re.sub(r"\(.*?\)", "", name).strip()


def normalize_strip_modifiers(name: str) -> str:
    """
    상태·산지 수식어(국산/수입/냉동/활/선/냉/깐/햇/건/저장/생) 제거.
    - 토큰 전체가 수식어와 정확히 일치하면 통째로 제거
      (한 글자 수식어 포함, 예: '브로콜리 국산' → '브로콜리', '저장 양파' → '양파').
    - 접두어 제거는 두 글자 이상 수식어에만 적용 (예: '깐마늘' → '마늘'... 대신
      '냉동'류 2글자 수식어에 한정). 한 글자 수식어(생/냉/선/활/건/깐/햇)는
      접두어로는 제거하지 않는다 — '생강'→'강', '건포도'→'포도'처럼 멀쩡한
      재료명이 깨지는 오매칭을 막기 위함.
    """
    tokens = name.split()
    result_tokens = []
    for tok in tokens:
        if tok in MODIFIER_WORDS:
            continue
        for mod in sorted(MODIFIER_WORDS, key=len, reverse=True):
            if len(mod) >= 2 and tok.startswith(mod) and len(tok) > len(mod):
                tok = tok[len(mod):]
                break
        if tok:
            result_tokens.append(tok)
    return " ".join(result_tokens).strip()


def _lookup_exact_name(conn, name, lookup_cache):
    """
    name으로 ingredient.ingredient_name → ingredient_synonym.synonym_name 순으로
    완전일치 조회. (ingredient_id, ingredient_name) 또는 None 반환.
    """
    if not name:
        return None
    if name in lookup_cache:
        return lookup_cache[name]

    row = conn.execute(
        text("SELECT ingredient_id, ingredient_name FROM ingredient WHERE ingredient_name = :n"),
        {"n": name}
    ).fetchone()
    if row:
        lookup_cache[name] = (row.ingredient_id, row.ingredient_name)
        return lookup_cache[name]

    row = conn.execute(
        text("""
            SELECT i.ingredient_id, i.ingredient_name
            FROM ingredient_synonym s
            JOIN ingredient i ON i.ingredient_id = s.ingredient_id
            WHERE s.synonym_name = :n
        """),
        {"n": name}
    ).fetchone()
    if row:
        lookup_cache[name] = (row.ingredient_id, row.ingredient_name)
        return lookup_cache[name]

    lookup_cache[name] = None
    return None


def match_ingredient_id(conn, pum_nm, match_cache, lookup_cache):
    """
    PUM_NM_A(품목명)을 ingredient_id로 매칭.
    (ingredient_id, ingredient_name, confidence) 반환. 실패 시 (None, None, None).

    순서 (각 단계 ingredient_name/synonym_name 양쪽 완전일치 시도):
      1. exact  : 원본 그대로 완전일치 — 안전
      2. synonym: 동의어 테이블 완전일치 — 안전
      3. high   : 괄호 제거 + 수식어 제거 후 완전일치 — 대체로 안전
      4. low    : 공백 기준 첫 단어로 완전일치 — 오매칭 위험, 검수 필요
                  (예: '오이맛고추' → '오이'로 오매칭될 수 있음)
    3~4차 모두 실패하면 None (자동 추가/억지 매칭 금지 — 잘못된 매칭으로
    데이터가 조용히 오염되는 것을 방지하기 위함).
    """
    name = str(pum_nm or "").strip()
    if not name:
        return None, None, None
    if name in match_cache:
        return match_cache[name]

    # 1차: ingredient_name 완전일치 (exact)
    row = conn.execute(
        text("SELECT ingredient_id, ingredient_name FROM ingredient WHERE ingredient_name = :n"),
        {"n": name}
    ).fetchone()
    if row:
        result = (row.ingredient_id, row.ingredient_name, "exact")
        match_cache[name] = result
        return result

    # 2차: ingredient_synonym 완전일치 (synonym)
    row = conn.execute(
        text("""
            SELECT i.ingredient_id, i.ingredient_name
            FROM ingredient_synonym s
            JOIN ingredient i ON i.ingredient_id = s.ingredient_id
            WHERE s.synonym_name = :n
        """),
        {"n": name}
    ).fetchone()
    if row:
        result = (row.ingredient_id, row.ingredient_name, "synonym")
        match_cache[name] = result
        return result

    # 3차: 괄호 제거 + 수식어 제거 후 완전일치 (high)
    normalized = normalize_strip_modifiers(normalize_strip_parens(name))
    if normalized and normalized != name:
        found = _lookup_exact_name(conn, normalized, lookup_cache)
        if found:
            result = (found[0], found[1], "high")
            match_cache[name] = result
            return result

    # 4차: 공백 기준 첫 단어 매칭 (low — 검수 필요)
    base_for_first_word = normalized if normalized else name
    tokens = base_for_first_word.split()
    first_word = tokens[0] if tokens else None
    if first_word and first_word != name:
        found = _lookup_exact_name(conn, first_word, lookup_cache)
        if found:
            result = (found[0], found[1], "low")
            match_cache[name] = result
            return result

    match_cache[name] = (None, None, None)
    return None, None, None


def build_match_results(engine, daily_avg_by_pum_nm):
    """
    daily_avg_by_pum_nm: {pum_nm: (avg_price_per_g, sample_days)}
    각 pum_nm에 대해 정규화 매칭을 수행하여 preview/load 공용 결과를 만든다.
    반환: (matched_results: list[dict], unmatched_names: {pum_nm: sample_days})
    """
    matched_results = []
    unmatched_names = {}
    match_cache = {}
    lookup_cache = {}

    with engine.connect() as conn:
        for pum_nm, (avg_price, sample_days) in daily_avg_by_pum_nm.items():
            ing_id, ing_name, confidence = match_ingredient_id(conn, pum_nm, match_cache, lookup_cache)
            if ing_id is None:
                unmatched_names[pum_nm] = sample_days
                continue
            matched_results.append({
                "pum_nm": pum_nm,
                "matched_ingredient_id": ing_id,
                "matched_ingredient_name": ing_name,
                "confidence": confidence,
                "avg_price_per_g": avg_price,
                "sample_days": sample_days,
            })

    return matched_results, unmatched_names


# ─────────────────────────────────────────────────────────────────
# 개선 2: preview 모드 — 매칭 결과 미리보기 CSV (DB 미반영)
# ─────────────────────────────────────────────────────────────────
def write_preview_csv(matched_results):
    """
    매칭 미리보기 CSV 출력: data/external/price_match_preview_{날짜}.csv
    confidence가 'low'인 행이 위로 오도록 정렬 (검수 우선순위).
    """
    UNMATCHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    preview_path = UNMATCHED_LOG_DIR / f"price_match_preview_{stamp}.csv"

    sorted_results = sorted(
        matched_results,
        key=lambda r: CONFIDENCE_SORT_ORDER.get(r["confidence"], 99)
    )

    with open(preview_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "pum_nm", "matched_ingredient_id", "matched_ingredient_name",
            "confidence", "avg_price_per_g", "sample_days"
        ])
        for r in sorted_results:
            writer.writerow([
                r["pum_nm"], r["matched_ingredient_id"], r["matched_ingredient_name"],
                r["confidence"], r["avg_price_per_g"], r["sample_days"],
            ])

    return preview_path


def read_exclude_list():
    """
    data/external/price_match_exclude.csv(있으면)의 pum_nm 컬럼을 읽어
    적재 제외 대상 집합을 반환. 파일이 없으면 빈 집합 반환.
    (사용자가 preview CSV 검수 후 오매칭 품목을 이 파일에 pum_nm으로 기록해둔다)
    """
    if not EXCLUDE_LIST_PATH.exists():
        return set()

    excluded = set()
    with open(EXCLUDE_LIST_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("pum_nm") or "").strip()
            if name:
                excluded.add(name)
    return excluded


# ─────────────────────────────────────────────────────────────────
# load 모드 — ingredient_price 적재 (ingredient_id 기준 가중평균 → upsert)
# ─────────────────────────────────────────────────────────────────
def load_ingredient_price(engine, matched_results):
    """
    matched_results(제외 목록 반영 후)를 ingredient_id 기준으로 모아
    거래일수(sample_days) 가중평균을 낸 뒤 적재한다.
    (서로 다른 pum_nm이 동의어/정규화로 같은 재료에 매핑될 수 있으므로
    ingredient_id 기준으로 묶는다.)

    ── (3) ON CONFLICT upsert 처리 ──
    (ingredient_id, price_date) UNIQUE 제약을 이용해 upsert. 재실행해도 안전(idempotent).
    """
    price_date = date.today()
    grouped = defaultdict(list)  # {ingredient_id: [matched_result, ...]}
    for r in matched_results:
        grouped[r["matched_ingredient_id"]].append(r)

    inserted = 0
    updated = 0

    with engine.begin() as conn:
        for ing_id, rows in grouped.items():
            total_days = sum(r["sample_days"] for r in rows)
            if total_days > 0:
                avg_price = round(
                    sum(r["avg_price_per_g"] * r["sample_days"] for r in rows) / total_days, 4
                )
            else:
                avg_price = round(sum(r["avg_price_per_g"] for r in rows) / len(rows), 4)

            confidences = sorted({r["confidence"] for r in rows})
            pum_list = ", ".join(sorted({r["pum_nm"] for r in rows}))
            notes_val = (
                f"최근{LOOKBACK_DAYS}일 '{GRADE_FILTER}'등급 가중평균 "
                f"(거래일수합계={total_days}, 품목명={pum_list}, 신뢰도={'/'.join(confidences)})"
            )

            result = conn.execute(
                text("""
                    INSERT INTO ingredient_price (
                        ingredient_id, price_per_g, price_date, source, notes
                    ) VALUES (
                        :ing_id, :price, :pdate, :source, :notes
                    )
                    ON CONFLICT (ingredient_id, price_date) DO UPDATE
                        SET price_per_g = EXCLUDED.price_per_g,
                            source      = EXCLUDED.source,
                            notes       = EXCLUDED.notes
                    RETURNING (xmax = 0) AS is_insert
                """),
                {
                    "ing_id": ing_id,
                    "price":  avg_price,
                    "pdate":  price_date,
                    "source": SOURCE_NAME,
                    "notes":  notes_val,
                }
            )
            row = result.fetchone()
            if row and row.is_insert:
                inserted += 1
            else:
                updated += 1

    return inserted, updated


# ─────────────────────────────────────────────────────────────────
# 매칭 실패 / 단위 파싱 실패 로그 CSV 출력
# ─────────────────────────────────────────────────────────────────
def write_failure_logs(unmatched_names, unit_parse_failures):
    """매칭 실패 품목·단위 파싱 실패 내역을 CSV로 저장 (추후 동의어 규칙 보강용)."""
    UNMATCHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")

    unmatched_path = UNMATCHED_LOG_DIR / f"ingredient_price_unmatched_{stamp}.csv"
    with open(unmatched_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["pum_nm", "sample_days"])
        for name, days in sorted(unmatched_names.items(), key=lambda x: -x[1]):
            writer.writerow([name, days])

    unit_fail_path = UNMATCHED_LOG_DIR / f"ingredient_price_unit_parse_fail_{stamp}.csv"
    with open(unit_fail_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["pum_nm", "u_name", "unit_qty"])
        for row in unit_parse_failures:
            writer.writerow(row)

    return unmatched_path, unit_fail_path


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="가락시장 재료 가격 적재 스크립트 (preview/load 2단계)")
    parser.add_argument(
        "--mode", choices=["preview", "load"], default="preview",
        help="preview: 매칭 결과만 CSV로 출력, DB 미반영 (기본값) / "
             "load: price_match_exclude.csv 제외 목록 반영 후 DB에 upsert 적재",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 45)
    print("  OptiMeal 가락시장 재료 가격 적재 스크립트")
    print(f"  모드: {args.mode}")
    print("=" * 45)

    garak_id, garak_passwd = get_garak_credentials()
    if not garak_id or not garak_passwd:
        print("  [오류] .env에 GARAK_ID / GARAK_PASSWD 값이 비어 있습니다.")
        print("         .env 파일에 값을 채운 후 다시 실행하세요.")
        sys.exit(1)

    # 수입품목전용(data65) 자격증명 — 없어도 국산(data52) 수집은 그대로 진행 (collect_price_samples에서 안내)
    garak_import_id, garak_import_passwd = get_garak_import_credentials()

    print(f"  조회 기간: 최근 {LOOKBACK_DAYS}일(주말 제외) / 등급: '{GRADE_FILTER}' / 부류: 청과·수산")

    engine = get_engine()
    session = requests.Session()

    raw_samples, unit_parse_failures = collect_price_samples(
        session, garak_id, garak_passwd, garak_import_id, garak_import_passwd
    )
    print(f"\n  등급 '{GRADE_FILTER}' & 단위 환산 성공 원시 샘플 수: {len(raw_samples)}건")
    print(f"  단위 파싱 실패: {len(unit_parse_failures)}건")

    daily_avg_by_pum_nm = aggregate_daily_then_average(raw_samples)
    print(f"  품목(고유 pum_nm) 수: {len(daily_avg_by_pum_nm)}개 (일별 대표값 → 품목별 평균 완료)")

    matched_results, unmatched_names = build_match_results(engine, daily_avg_by_pum_nm)

    confidence_counts = defaultdict(int)
    for r in matched_results:
        confidence_counts[r["confidence"]] += 1

    print("\n  매칭 결과 요약 (confidence 등급별):")
    for tier in ("exact", "synonym", "high", "low"):
        print(f"    - {tier:<8}: {confidence_counts.get(tier, 0)}건")
    print(f"    - 매칭 실패 : {len(unmatched_names)}건")

    preview_path = write_preview_csv(matched_results)
    unmatched_path, unit_fail_path = write_failure_logs(unmatched_names, unit_parse_failures)

    print(f"\n  매칭 미리보기 CSV: {preview_path}")
    print(f"  매칭 실패 CSV: {unmatched_path}")
    print(f"  단위 파싱 실패 CSV: {unit_fail_path}")

    if args.mode == "preview":
        print("\n  [preview 모드] DB에는 적재하지 않았습니다.")
        print(f"  CSV를 검수한 뒤, 오매칭 품목은 {EXCLUDE_LIST_PATH.name}(pum_nm 컬럼)에 적고")
        print("  --mode load로 다시 실행하세요.")
        return

    # ── load 모드: 제외 목록 반영 후 upsert ──
    excluded = read_exclude_list()
    if excluded:
        before = len(matched_results)
        matched_results = [r for r in matched_results if r["pum_nm"] not in excluded]
        print(f"\n  제외 목록 반영: {before - len(matched_results)}건 제외 ({EXCLUDE_LIST_PATH.name} 기준)")

    inserted, updated = load_ingredient_price(engine, matched_results)

    print("\n" + "=" * 45)
    print("  적재 완료")
    print("=" * 45)
    print(f"  신규 삽입: {inserted}건 / 갱신: {updated}건")

    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM ingredient_price")).scalar()
    print(f"  ingredient_price 총 행 수: {cnt:,}행")


if __name__ == "__main__":
    main()
