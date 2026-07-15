#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_nutrition_mfds.py
목적: 식약처 영양성분 DB API(FoodNtrCpntDbInfo02)에서 약 30만 건을 받아와
      기존 nutrition_recipe 테이블에 적재 (CREATE 없음, INSERT만 수행)

실행:
  테스트(2페이지만): docker exec optimeal_app python scripts/load_nutrition_mfds.py --max-pages 2
  전체 적재:         docker exec optimeal_app python scripts/load_nutrition_mfds.py
  이어받기:          docker exec optimeal_app python scripts/load_nutrition_mfds.py --start-page 150

참고: task2_식약처_영양성분_적재_지시서.md의 확정된 컬럼 매핑 반영.
  - AMT_NUM13 → sodium (13번! 81 아님 — 지시서에서 특히 강조된 부분)
  - nutrition_recipe에는 UNIQUE 제약이 없어 FOOD_CD 기준으로 애플리케이션에서 중복 방지.
  - get_engine 패턴은 scripts/load_recipe_data.py, 자격증명 읽기 패턴은
    scripts/load_ingredient_price.py를 참고해 재사용.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

# ─────────────────────────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / "data" / "external"

# ─────────────────────────────────────────────────────────────────
# 식약처 영양성분 DB API 설정
# ─────────────────────────────────────────────────────────────────
API_URL = "https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo02/getFoodNtrCpntDbInq02"
PAGE_SIZE = 500               # numOfRows 최대값 (500까지 정상, 700+는 빈 응답 — 실측 확인)
REQUEST_DELAY_SEC = 0.3       # 페이지 호출 사이 딜레이 (서버 부하 방지)
MAX_RETRIES = 3               # 페이지당 재시도 횟수

DATA_SOURCE_FIXED = "식약처"     # nutrition_recipe.data_source 고정값
MENU_CATEGORY_FIXED = "기타"     # nutrition_recipe.menu_category 고정값(잠정)

# numeric(6,2)/numeric(8,2) 컬럼 오버플로 방지용 절댓값 한도
NUTRIENT_MAX_ABS = 9999.99       # protein/fat/carbs/sugar/cholesterol/saturated_fat/trans_fat: numeric(6,2)
CAL_SODIUM_MAX_ABS = 999999.99   # calories/sodium: numeric(8,2)

# ── 컬럼 매핑 (지시서로 확정) ──
# db_column: (API 응답 필드, 오버플로 한도)
# ⚠️ 나트륨은 AMT_NUM13 (81번 아님) — 지시서에서 특히 강조된 부분
AMT_FIELD_MAP = {
    "protein":       ("AMT_NUM3",  NUTRIENT_MAX_ABS),
    "fat":           ("AMT_NUM4",  NUTRIENT_MAX_ABS),
    "carbs":         ("AMT_NUM6",  NUTRIENT_MAX_ABS),
    "sodium":        ("AMT_NUM13", CAL_SODIUM_MAX_ABS),
    "sugar":         ("AMT_NUM7",  NUTRIENT_MAX_ABS),
    "cholesterol":   ("AMT_NUM23", NUTRIENT_MAX_ABS),
    "saturated_fat": ("AMT_NUM24", NUTRIENT_MAX_ABS),
    "trans_fat":     ("AMT_NUM25", NUTRIENT_MAX_ABS),
}

INSERT_SQL = text("""
    INSERT INTO nutrition_recipe (
        recipe_name, serving_size_g, calories, protein, fat, carbs, sodium, sugar,
        cholesterol, saturated_fat, trans_fat, data_source, menu_category, original_data
    ) VALUES (
        :recipe_name, :serving_size_g, :calories, :protein, :fat, :carbs, :sodium, :sugar,
        :cholesterol, :saturated_fat, :trans_fat, :data_source, :menu_category,
        CAST(:original_data AS jsonb)
    )
""")


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


def get_foodsafety_nutrition_key():
    """
    .env에서 공공데이터포털 식약처 영양성분 API 인증키(FOODSAFETY_NUTRITION_KEY)를 읽어 반환.
    값 자체는 어디에도 출력/로그하지 않는다.
    """
    return os.getenv("FOODSAFETY_NUTRITION_KEY")


# ─────────────────────────────────────────────────────────────────
# 값 파싱 헬퍼 (빈 문자열/콤마/단위 텍스트 처리)
# ─────────────────────────────────────────────────────────────────
def parse_amt(raw, max_abs=None):
    """
    AMT_NUMx 값을 float으로 파싱.
    - 빈 문자열/None → (None, False)
    - 콤마 제거 후 float 변환 (예: "1,670.000" → 1670.0)
    - max_abs 지정 시 절댓값 초과하면 (None, True) 반환 (자릿수 초과 → overflow)
    반환: (value_or_None, overflowed: bool)
    """
    if raw is None:
        return None, False
    s = str(raw).strip()
    if not s:
        return None, False
    s = s.replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return None, False
    if max_abs is not None and abs(value) > max_abs:
        return None, True
    return round(value, 2), False


def parse_serving_size(raw):
    """SERVING_SIZE(예: '100g')에서 숫자만 추출. 실패 시 None."""
    s = str(raw or "").strip()
    if not s:
        return None
    m = re.search(r"(\d+(\.\d+)?)", s)
    if not m:
        return None
    return round(float(m.group(1)), 2)


# ─────────────────────────────────────────────────────────────────
# 중복 방지: 기존 적재된 FOOD_CD 조회
# ─────────────────────────────────────────────────────────────────
def load_existing_food_cds(engine):
    """
    nutrition_recipe에는 UNIQUE 제약이 없어(DB가 안 막아줌) FOOD_CD를
    애플리케이션 레벨 중복 판별 기준으로 사용한다. original_data(jsonb)에
    보존된 FOOD_CD를 조회해 이미 적재된 집합을 만든다. 재실행해도 중복이
    쌓이지 않도록 하기 위함.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT DISTINCT original_data ->> 'FOOD_CD' AS food_cd
                FROM nutrition_recipe
                WHERE data_source = :ds AND original_data ->> 'FOOD_CD' IS NOT NULL
            """),
            {"ds": DATA_SOURCE_FIXED}
        ).fetchall()
    return {r.food_cd for r in rows if r.food_cd}


# ─────────────────────────────────────────────────────────────────
# API 호출 (재시도 포함)
# ─────────────────────────────────────────────────────────────────
def fetch_page(session, service_key, page_no, num_of_rows=PAGE_SIZE, max_retries=MAX_RETRIES):
    """
    식약처 영양성분 API 한 페이지 호출. 실패 시 최대 max_retries회 재시도.
    반환: (items: list, total_count: int|None, success: bool)
    """
    params = {
        "serviceKey": service_key,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "type": "json",
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(API_URL, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            # 주의: 예외 메시지에 요청 URL(=serviceKey 쿼리파라미터 포함)이 담길 수 있어
            # str(e)를 그대로 출력하지 않는다.
            print(f"  [경고] API 호출 실패 (page={page_no}, 시도 {attempt}/{max_retries})")
            time.sleep(REQUEST_DELAY_SEC)
            continue

        body = payload.get("body") if isinstance(payload, dict) else None
        if not isinstance(body, dict):
            print(f"  [경고] 응답 구조 이상 (page={page_no}, 시도 {attempt}/{max_retries})")
            time.sleep(REQUEST_DELAY_SEC)
            continue

        items = body.get("items") or []
        if isinstance(items, dict):
            items = [items]
        total_count = body.get("totalCount")
        return items, total_count, True

    return [], None, False


# ─────────────────────────────────────────────────────────────────
# 응답 item 1건 → nutrition_recipe INSERT용 dict 변환
# ─────────────────────────────────────────────────────────────────
def process_item(item, seen_food_cds, skip_log, overflow_log):
    """
    API item 1건을 검증·변환해 INSERT 파라미터 dict로 반환.
    실패(스킵) 시 None을 반환하고 skip_log에 사유를 남긴다.
    """
    food_cd = str(item.get("FOOD_CD") or "").strip()
    food_nm = str(item.get("FOOD_NM_KR") or "").strip()

    if food_cd and food_cd in seen_food_cds:
        skip_log.append({"food_cd": food_cd, "food_nm": food_nm, "reason": "중복 FOOD_CD"})
        return None

    if not food_nm:
        skip_log.append({"food_cd": food_cd, "food_nm": food_nm, "reason": "FOOD_NM_KR 없음"})
        return None

    # calories(AMT_NUM1) 필수 — 빈 값/파싱실패/자릿수초과 시 skip (NOT NULL 제약 위반 방지)
    calories, cal_overflow = parse_amt(item.get("AMT_NUM1"), max_abs=CAL_SODIUM_MAX_ABS)
    if calories is None:
        reason = "calories 자릿수초과" if cal_overflow else "calories 빈값/파싱실패"
        skip_log.append({"food_cd": food_cd, "food_nm": food_nm, "reason": reason})
        return None

    row = {
        "recipe_name": food_nm[:200],
        "serving_size_g": parse_serving_size(item.get("SERVING_SIZE")),
        "calories": calories,
        "data_source": DATA_SOURCE_FIXED,
        "menu_category": MENU_CATEGORY_FIXED,
        "original_data": json.dumps(item, ensure_ascii=False),
    }

    for db_col, (amt_key, max_abs) in AMT_FIELD_MAP.items():
        value, overflowed = parse_amt(item.get(amt_key), max_abs=max_abs)
        row[db_col] = value
        if overflowed:
            overflow_log.append({"food_cd": food_cd, "food_nm": food_nm, "field": db_col})

    if food_cd:
        seen_food_cds.add(food_cd)

    return row


# ─────────────────────────────────────────────────────────────────
# 로그 CSV 출력
# ─────────────────────────────────────────────────────────────────
def write_logs(skip_log, overflow_log, failed_pages):
    """skip/overflow/실패페이지 내역을 CSV로 저장."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")

    skip_path = LOG_DIR / f"nutrition_mfds_skip_{stamp}.csv"
    with open(skip_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["food_cd", "food_nm", "reason"])
        for r in skip_log:
            writer.writerow([r["food_cd"], r["food_nm"], r["reason"]])

    overflow_path = LOG_DIR / f"nutrition_mfds_overflow_{stamp}.csv"
    with open(overflow_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["food_cd", "food_nm", "field"])
        for r in overflow_log:
            writer.writerow([r["food_cd"], r["food_nm"], r["field"]])

    failed_pages_path = LOG_DIR / f"nutrition_mfds_failed_pages_{stamp}.csv"
    with open(failed_pages_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["page_no"])
        for p in failed_pages:
            writer.writerow([p])

    return skip_path, overflow_path, failed_pages_path


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="식약처 영양성분 DB 적재 스크립트")
    parser.add_argument(
        "--start-page", type=int, default=1,
        help="이어받기: 이 페이지 번호부터 시작 (기본 1). 배치 커밋이라 이전 실행분은 이미 DB에 반영돼 있음.",
    )
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="테스트용: --start-page부터 이 페이지 수만큼만 처리하고 종료 (기본: 전체 처리)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 45)
    print("  OptiMeal 식약처 영양성분 DB 적재 스크립트")
    print("=" * 45)

    service_key = get_foodsafety_nutrition_key()
    if not service_key:
        print("  [오류] .env에 FOODSAFETY_NUTRITION_KEY 값이 비어 있습니다.")
        print("         .env 파일에 값을 채운 후 다시 실행하세요.")
        sys.exit(1)

    engine = get_engine()
    session = requests.Session()

    seen_food_cds = load_existing_food_cds(engine)
    print(f"  기존 적재된 FOOD_CD 수: {len(seen_food_cds):,}건")
    print(f"  시작 페이지: {args.start_page}" + (f" / 최대 {args.max_pages}페이지(테스트)" if args.max_pages else ""))

    skip_log = []
    overflow_log = []
    failed_pages = []
    total_attempted = 0
    total_inserted = 0
    total_count = None
    total_pages = None

    page_no = args.start_page
    pages_processed = 0
    consecutive_unknown_failures = 0

    while True:
        if total_pages is not None and page_no > total_pages:
            break
        if args.max_pages is not None and pages_processed >= args.max_pages:
            print(f"  [안내] --max-pages={args.max_pages} 도달, 테스트 종료")
            break

        items, tc, ok = fetch_page(session, service_key, page_no)

        if not ok:
            failed_pages.append(page_no)
            print(f"  [실패] page {page_no} — {MAX_RETRIES}회 재시도 후에도 실패, 건너뜀")
            if total_pages is None:
                consecutive_unknown_failures += 1
                if consecutive_unknown_failures > 5:
                    print("  [오류] 전체 건수를 알기 전에 연속 실패가 반복돼 중단합니다.")
                    break
            page_no += 1
            pages_processed += 1
            time.sleep(REQUEST_DELAY_SEC)
            continue

        consecutive_unknown_failures = 0

        if total_count is None and tc is not None:
            total_count = tc
            total_pages = math.ceil(total_count / PAGE_SIZE) if total_count > 0 else 0
            print(f"  전체 건수: {total_count:,}건 / 전체 페이지: {total_pages:,}페이지 (페이지당 {PAGE_SIZE}건)")
            if total_pages == 0:
                break

        # ── 배치 커밋: 한 페이지(최대 1000건)를 모아 한 트랜잭션으로 INSERT ──
        rows = []
        for item in items:
            total_attempted += 1
            row = process_item(item, seen_food_cds, skip_log, overflow_log)
            if row is not None:
                rows.append(row)

        if rows:
            with engine.begin() as conn:
                for row in rows:
                    conn.execute(INSERT_SQL, row)
            total_inserted += len(rows)

        # ── 진행 상황 출력 ──
        # total_count가 None일 수 있어(빈 응답 등) 콤마 포맷에서 터지지 않도록 방어.
        pct = round(total_attempted / total_count * 100, 1) if total_count else 0.0
        total_label = f"{total_count:,}" if total_count else "?"
        page_label = f"{page_no}/{total_pages}" if total_pages else f"{page_no}"
        print(f"  진행 {total_attempted:,}/{total_label} ({pct}%) — page {page_label}, 신규삽입 {total_inserted:,}건")

        page_no += 1
        pages_processed += 1
        time.sleep(REQUEST_DELAY_SEC)

    skip_path, overflow_path, failed_pages_path = write_logs(skip_log, overflow_log, failed_pages)

    skip_reason_counts = {}
    for r in skip_log:
        skip_reason_counts[r["reason"]] = skip_reason_counts.get(r["reason"], 0) + 1

    print("\n" + "=" * 45)
    print("  적재 완료")
    print("=" * 45)
    print(f"  총 시도: {total_attempted:,}건")
    print(f"  신규 삽입: {total_inserted:,}건")
    print(f"  skip: {len(skip_log):,}건")
    for reason, cnt in sorted(skip_reason_counts.items(), key=lambda x: -x[1]):
        print(f"    - {reason}: {cnt:,}건")
    print(f"  필드 오버플로(NULL 처리): {len(overflow_log):,}건")
    print(f"  실패 페이지 수: {len(failed_pages)}건" + (f" → {failed_pages}" if failed_pages else ""))
    print(f"\n  skip 로그: {skip_path}")
    print(f"  오버플로 로그: {overflow_path}")
    print(f"  실패 페이지 로그: {failed_pages_path}")
    if failed_pages:
        print(f"\n  실패한 페이지는 --start-page {min(failed_pages)} 등으로 재실행해 보완하세요.")

    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM nutrition_recipe WHERE data_source = :ds"),
                            {"ds": DATA_SOURCE_FIXED}).scalar()
    print(f"\n  nutrition_recipe(data_source='식약처') 총 행 수: {cnt:,}행")


if __name__ == "__main__":
    main()