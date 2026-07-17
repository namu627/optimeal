#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_recipe_foodsafety.py
목적: 식품안전나라 조리식품 레시피 API(COOKRCP01)에서 레시피를 받아와
      영양정보는 nutrition_recipe에, 레시피 본문은 recipe에 적재하고 서로 연결
      (두 테이블 모두 이미 존재 — CREATE 없음, INSERT만 수행)

실행:
  테스트(10건만):    docker exec optimeal_app python scripts/load_recipe_foodsafety.py --limit 10
  전체 적재:         docker exec optimeal_app python scripts/load_recipe_foodsafety.py
  이어받기:          docker exec optimeal_app python scripts/load_recipe_foodsafety.py --start 501

적재 순서 (레시피 1건당, 같은 트랜잭션):
  1. nutrition_recipe INSERT ... RETURNING nutrition_id
  2. recipe INSERT (nutrition_recipe_id = 위 nutrition_id로 연결)
  둘 중 하나라도 실패하면 해당 레시피 전체 롤백 (영양정보만 붕 뜨는 것 방지).

참고: task2_식품안전나라_레시피_적재_지시서.md의 확정된 매핑 반영.
  - get_engine 패턴은 scripts/load_recipe_data.py,
    숫자 파싱/오버플로 로그 규칙은 scripts/load_nutrition_mfds.py를 재사용.
"""

import argparse
import csv
import json
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
# 식품안전나라 조리식품 레시피 API 설정
# ─────────────────────────────────────────────────────────────────
API_HOST = "http://openapi.foodsafetykorea.go.kr/api"
API_SERVICE = "COOKRCP01"
PAGE_SIZE = 1000              # 한 번에 최대 1000건
REQUEST_DELAY_SEC = 0.3       # 배치 호출 사이 딜레이 (서버 부하 방지)
MAX_RETRIES = 3               # 배치당 재시도 횟수

NUTRITION_DATA_SOURCE = "식품안전나라"   # nutrition_recipe.data_source CHECK 허용값
RECIPE_DATA_SOURCE = "식약처API"         # recipe.data_source CHECK 허용값 ('식품안전나라'는 recipe엔 없음)

# numeric(6,2)/numeric(8,2)/numeric(10,2) 컬럼 오버플로 방지용 절댓값 한도
NUTRIENT_MAX_ABS = 9999.99          # protein/fat/carbs: numeric(6,2)
CAL_SODIUM_MAX_ABS = 999999.99      # calories/sodium: numeric(8,2)
SERVING_SIZE_MAX_ABS = 99999999.99  # serving_size_g: numeric(10,2)

# ── nutrition_recipe 숫자 필드 매핑: db_column: (API 필드, 오버플로 한도) ──
NUTRIENT_FIELD_MAP = {
    "protein": ("INFO_PRO", NUTRIENT_MAX_ABS),
    "fat":     ("INFO_FAT", NUTRIENT_MAX_ABS),
    "carbs":   ("INFO_CAR", NUTRIENT_MAX_ABS),
    "sodium":  ("INFO_NA",  CAL_SODIUM_MAX_ABS),
}

# ── 조리법 매핑표 (RCP_WAY2 → cooking_method.method_name) ──
COOKING_METHOD_MAP = {
    "찌기":   "찜",
    "끓이기": "끓이기",
    "삶기":   "삶기",
    "볶기":   "볶음",
    "굽기":   "구이",
    "튀기기": "튀김",
    "조리기": "조림",
    "무치기": "무침",
}

# ── menu_category 매핑표 (RCP_PAT2 → nutrition_recipe.menu_category CHECK 7종) ──
MENU_CATEGORY_MAP = {
    "반찬":     "반찬",
    "국&찌개":  "국",   # 표기가 뭉뚱그려진 경우 애매하므로 '국'으로 판단
    "국":       "국",
    "찌개":     "찌개",
    "후식":     "후식",
    "밥":       "주식",
    "일품":     "주식",
    "음료":     "음료",
}
MENU_CATEGORY_DEFAULT = "기타"

NUTRITION_INSERT_SQL = text("""
    INSERT INTO nutrition_recipe (
        recipe_name, serving_size_g, calories, protein, fat, carbs, sodium,
        data_source, menu_category, original_data
    ) VALUES (
        :recipe_name, :serving_size_g, :calories, :protein, :fat, :carbs, :sodium,
        :data_source, :menu_category, CAST(:original_data AS jsonb)
    )
    RETURNING nutrition_id
""")

RECIPE_INSERT_SQL = text("""
    INSERT INTO recipe (
        recipe_name, primary_method_id, serving_size, serving_category,
        description, notes, nutrition_recipe_id, data_source
    ) VALUES (
        :recipe_name, :primary_method_id, :serving_size, :serving_category,
        :description, :notes, :nutrition_recipe_id, :data_source
    )
    RETURNING recipe_id
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


def get_foodsafety_recipe_key():
    """
    .env에서 식품안전나라 레시피 API 인증키(FOODSAFETY_RECIPE_KEY)를 읽어 반환.
    값 자체는 어디에도 출력/로그하지 않는다.
    """
    return os.getenv("FOODSAFETY_RECIPE_KEY")


# ─────────────────────────────────────────────────────────────────
# 조리방법 ID 룩업 테이블 구축
# ─────────────────────────────────────────────────────────────────
def build_cooking_method_lookup(conn):
    """DB에서 cooking_method 테이블을 읽어 {method_name: method_id} 딕셔너리 반환."""
    rows = conn.execute(
        text("SELECT method_id, method_name FROM cooking_method")
    ).fetchall()
    return {row.method_name: row.method_id for row in rows}


def resolve_method_id(rcp_way2, method_lookup):
    """
    RCP_WAY2 → cooking_method.method_id 변환.
    COOKING_METHOD_MAP에 없는 값(또는 매핑된 이름이 DB에 없는 경우)은 None 반환
    (해당 레시피는 skip 처리).
    """
    val = str(rcp_way2 or "").strip()
    std_name = COOKING_METHOD_MAP.get(val)
    if std_name is None:
        return None
    return method_lookup.get(std_name)


# ─────────────────────────────────────────────────────────────────
# 값 파싱 헬퍼 (load_nutrition_mfds.py의 parse_amt와 동일 규칙)
# ─────────────────────────────────────────────────────────────────
def parse_amt(raw, max_abs=None):
    """
    숫자 문자열을 float으로 파싱.
    - 빈 문자열/None → (None, False)
    - 콤마 제거 후 float 변환
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


# ─────────────────────────────────────────────────────────────────
# 중복 방지: 기존 적재된 RCP_SEQ 조회 (recipe.notes 기준)
# ─────────────────────────────────────────────────────────────────
RCP_SEQ_PATTERN = re.compile(r"RCP_SEQ=(\S+)")


def load_existing_rcp_seqs(engine):
    """
    recipe에는 UNIQUE 제약이 없어(DB가 안 막아줌) notes에 박아둔 RCP_SEQ를
    애플리케이션 레벨 중복 판별 기준으로 사용한다. 재실행해도 중복이 쌓이지
    않도록 하기 위함.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT notes FROM recipe WHERE notes LIKE '%RCP_SEQ=%'")
        ).fetchall()
    seen = set()
    for r in rows:
        m = RCP_SEQ_PATTERN.search(r.notes or "")
        if m:
            seen.add(m.group(1))
    return seen


# ─────────────────────────────────────────────────────────────────
# API 호출 (재시도 포함)
# ─────────────────────────────────────────────────────────────────
def fetch_batch(session, service_key, start, end, max_retries=MAX_RETRIES):
    """
    COOKRCP01 API를 [start, end] 범위로 호출. 실패 시 최대 max_retries회 재시도.
    반환: (items: list, total_count: int|None, result_code: str|None, success: bool)
    """
    url = f"{API_HOST}/{service_key}/{API_SERVICE}/json/{start}/{end}"

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            # 주의: 인증키가 URL 경로 자체에 포함되므로 예외 메시지(URL 포함 가능)를
            # 그대로 출력하지 않는다.
            print(f"  [경고] API 호출 실패 (range={start}-{end}, 시도 {attempt}/{max_retries})")
            time.sleep(REQUEST_DELAY_SEC)
            continue

        root = payload.get(API_SERVICE) if isinstance(payload, dict) else None
        if not isinstance(root, dict):
            print(f"  [경고] 응답 구조 이상 (range={start}-{end}, 시도 {attempt}/{max_retries})")
            time.sleep(REQUEST_DELAY_SEC)
            continue

        result = root.get("RESULT") or {}
        code = result.get("CODE")

        # INFO-200: 해당 범위에 더 이상 데이터 없음 → 정상 종료 신호
        if code == "INFO-200":
            return [], None, code, True

        rows = root.get("row") or []
        if isinstance(rows, dict):
            rows = [rows]

        try:
            total_count = int(root.get("total_count")) if root.get("total_count") is not None else None
        except (TypeError, ValueError):
            total_count = None

        if not rows and code not in (None, "INFO-000"):
            # 정상 코드가 아니고 데이터도 없음 → 실패로 간주, 재시도
            print(f"  [경고] API 오류 응답 code={code} (range={start}-{end}, 시도 {attempt}/{max_retries})")
            time.sleep(REQUEST_DELAY_SEC)
            continue

        return rows, total_count, code, True

    return [], None, None, False


# ─────────────────────────────────────────────────────────────────
# 응답 item 1건 → (nutrition INSERT용 dict, recipe INSERT용 dict) 변환
# ─────────────────────────────────────────────────────────────────
def process_item(item, method_lookup, seen_rcp_seqs, skip_log, overflow_log, way2_fail_counts):
    """
    API item 1건을 검증·변환해 (nutrition_params, recipe_params) 튜플로 반환.
    실패(스킵) 시 (None, None)을 반환하고 skip_log/way2_fail_counts에 사유를 남긴다.
    """
    rcp_seq = str(item.get("RCP_SEQ") or "").strip()
    rcp_nm = str(item.get("RCP_NM") or "").strip()

    if rcp_seq and rcp_seq in seen_rcp_seqs:
        skip_log.append({"rcp_seq": rcp_seq, "rcp_nm": rcp_nm, "reason": "중복 RCP_SEQ"})
        return None, None

    if not rcp_nm:
        skip_log.append({"rcp_seq": rcp_seq, "rcp_nm": rcp_nm, "reason": "RCP_NM 없음"})
        return None, None

    # calories(INFO_ENG) 필수 — 빈 값/파싱실패/자릿수초과 시 skip (NOT NULL 제약 위반 방지)
    calories, cal_overflow = parse_amt(item.get("INFO_ENG"), max_abs=CAL_SODIUM_MAX_ABS)
    if calories is None:
        reason = "calories 자릿수초과" if cal_overflow else "calories 빈값/파싱실패"
        skip_log.append({"rcp_seq": rcp_seq, "rcp_nm": rcp_nm, "reason": reason})
        return None, None

    # 조리법 매핑 — 실패 시 skip + RCP_WAY2 값별 집계
    raw_way2 = str(item.get("RCP_WAY2") or "").strip()
    primary_method_id = resolve_method_id(raw_way2, method_lookup)
    if primary_method_id is None:
        skip_log.append({"rcp_seq": rcp_seq, "rcp_nm": rcp_nm, "reason": f"조리법매칭실패:{raw_way2}"})
        way2_fail_counts[raw_way2] = way2_fail_counts.get(raw_way2, 0) + 1
        return None, None

    # ── nutrition_recipe 파라미터 ──
    serving_size_g, wgt_overflow = parse_amt(item.get("INFO_WGT"), max_abs=SERVING_SIZE_MAX_ABS)
    if wgt_overflow:
        overflow_log.append({"rcp_seq": rcp_seq, "rcp_nm": rcp_nm, "field": "serving_size_g"})

    raw_pat2 = str(item.get("RCP_PAT2") or "").strip()
    menu_category = MENU_CATEGORY_MAP.get(raw_pat2, MENU_CATEGORY_DEFAULT)

    nutrition_params = {
        "recipe_name": rcp_nm[:200],
        "serving_size_g": serving_size_g,
        "calories": calories,
        "data_source": NUTRITION_DATA_SOURCE,
        "menu_category": menu_category,
        "original_data": json.dumps(item, ensure_ascii=False),
    }
    for db_col, (api_key, max_abs) in NUTRIENT_FIELD_MAP.items():
        value, overflowed = parse_amt(item.get(api_key), max_abs=max_abs)
        nutrition_params[db_col] = value
        if overflowed:
            overflow_log.append({"rcp_seq": rcp_seq, "rcp_nm": rcp_nm, "field": db_col})

    # ── recipe 파라미터 ──
    manual_steps = []
    for i in range(1, 21):
        step = str(item.get(f"MANUAL{i:02d}") or "").strip()
        if step:
            manual_steps.append(step)
    description = "\n".join(manual_steps) if manual_steps else None

    notes = f"출처=식품안전나라 RCP_SEQ={rcp_seq}"

    recipe_params = {
        "recipe_name": rcp_nm[:200],
        "primary_method_id": primary_method_id,
        "serving_size": 1,
        "serving_category": "소규모",
        "description": description,
        "notes": notes,
        "data_source": RECIPE_DATA_SOURCE,
    }

    if rcp_seq:
        seen_rcp_seqs.add(rcp_seq)

    return nutrition_params, recipe_params


# ─────────────────────────────────────────────────────────────────
# 레시피 1건 적재: nutrition_recipe → recipe (같은 트랜잭션)
# ─────────────────────────────────────────────────────────────────
def insert_recipe_pair(engine, nutrition_params, recipe_params, rcp_seq, rcp_nm, skip_log):
    """
    nutrition_recipe INSERT 후 nutrition_id를 recipe.nutrition_recipe_id에 연결해 INSERT.
    같은 트랜잭션 내에서 처리 — 하나라도 실패하면 둘 다 롤백.
    성공 시 True, 실패(DB 오류) 시 False 반환하고 skip_log에 사유 기록.
    """
    try:
        with engine.begin() as conn:
            nutrition_id = conn.execute(NUTRITION_INSERT_SQL, nutrition_params).scalar_one()
            conn.execute(
                RECIPE_INSERT_SQL,
                {**recipe_params, "nutrition_recipe_id": nutrition_id},
            )
        return True
    except Exception as e:
        skip_log.append({
            "rcp_seq": rcp_seq, "rcp_nm": rcp_nm,
            "reason": f"DB삽입실패:{type(e).__name__}",
        })
        return False


# ─────────────────────────────────────────────────────────────────
# 로그 CSV 출력
# ─────────────────────────────────────────────────────────────────
def write_logs(skip_log, overflow_log, way2_fail_counts):
    """skip/overflow/조리법매칭실패 집계를 CSV로 저장."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")

    skip_path = LOG_DIR / f"recipe_foodsafety_skip_{stamp}.csv"
    with open(skip_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["rcp_seq", "rcp_nm", "reason"])
        for r in skip_log:
            writer.writerow([r["rcp_seq"], r["rcp_nm"], r["reason"]])

    overflow_path = LOG_DIR / f"recipe_foodsafety_overflow_{stamp}.csv"
    with open(overflow_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["rcp_seq", "rcp_nm", "field"])
        for r in overflow_log:
            writer.writerow([r["rcp_seq"], r["rcp_nm"], r["field"]])

    way2_path = LOG_DIR / f"recipe_foodsafety_way2_matchfail_{stamp}.csv"
    with open(way2_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["rcp_way2", "count"])
        for val, cnt in sorted(way2_fail_counts.items(), key=lambda x: -x[1]):
            writer.writerow([val, cnt])

    return skip_path, overflow_path, way2_path


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="식품안전나라 레시피 적재 스크립트")
    parser.add_argument(
        "--start", type=int, default=1,
        help="이어받기: 이 인덱스부터 시작 (기본 1). 레시피별 트랜잭션이라 이전 실행분은 이미 DB에 반영돼 있음.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="테스트용: --start부터 이 건수만큼만 조회하고 종료 (기본: 전체 처리). 예: --limit 10",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 45)
    print("  OptiMeal 식품안전나라 레시피 적재 스크립트")
    print("=" * 45)

    service_key = get_foodsafety_recipe_key()
    if not service_key:
        print("  [오류] .env에 FOODSAFETY_RECIPE_KEY 값이 비어 있습니다.")
        print("         .env 파일에 값을 채운 후 다시 실행하세요.")
        sys.exit(1)

    engine = get_engine()
    session = requests.Session()

    with engine.connect() as conn:
        method_lookup = build_cooking_method_lookup(conn)
    if not method_lookup:
        print("  [오류] cooking_method 테이블이 비어 있습니다. 먼저 확인하세요.")
        sys.exit(1)

    seen_rcp_seqs = load_existing_rcp_seqs(engine)
    print(f"  기존 적재된 RCP_SEQ 수: {len(seen_rcp_seqs):,}건")
    print(f"  시작 인덱스: {args.start}" + (f" / 최대 {args.limit}건(테스트)" if args.limit else ""))

    skip_log = []
    overflow_log = []
    way2_fail_counts = {}
    total_attempted = 0
    recipe_inserted = 0
    nutrition_inserted = 0
    total_count = None

    end_of_run = (args.start + args.limit - 1) if args.limit else None
    current = args.start

    while True:
        if end_of_run is not None and current > end_of_run:
            break
        if total_count is not None and current > total_count:
            break

        batch_end = current + PAGE_SIZE - 1
        if end_of_run is not None:
            batch_end = min(batch_end, end_of_run)
        if total_count is not None:
            batch_end = min(batch_end, total_count)

        items, tc, code, ok = fetch_batch(session, service_key, current, batch_end)

        if not ok:
            print(f"  [실패] range {current}-{batch_end} — {MAX_RETRIES}회 재시도 후에도 실패, 건너뜀")
            current = batch_end + 1
            time.sleep(REQUEST_DELAY_SEC)
            continue

        if code == "INFO-200":
            print(f"  [안내] range {current}-{batch_end} — 더 이상 데이터 없음, 종료")
            break

        if total_count is None and tc is not None:
            total_count = tc
            print(f"  전체 건수: {total_count:,}건")

        # ── 레시피별 원자적 처리: nutrition INSERT + recipe INSERT를 같은 트랜잭션으로 ──
        for item in items:
            total_attempted += 1
            nutrition_params, recipe_params = process_item(
                item, method_lookup, seen_rcp_seqs, skip_log, overflow_log, way2_fail_counts
            )
            if nutrition_params is None:
                continue

            rcp_seq = str(item.get("RCP_SEQ") or "").strip()
            rcp_nm = str(item.get("RCP_NM") or "").strip()
            success = insert_recipe_pair(
                engine, nutrition_params, recipe_params, rcp_seq, rcp_nm, skip_log
            )
            if success:
                nutrition_inserted += 1
                recipe_inserted += 1

        pct = round(total_attempted / total_count * 100, 1) if total_count else 0.0
        total_label = f"{total_count:,}" if total_count else "?"
        print(f"  진행 {total_attempted:,}/{total_label} ({pct}%) — range {current}-{batch_end}, "
              f"신규삽입 {recipe_inserted:,}건")

        current = batch_end + 1
        time.sleep(REQUEST_DELAY_SEC)

    skip_path, overflow_path, way2_path = write_logs(skip_log, overflow_log, way2_fail_counts)

    skip_reason_counts = {}
    for r in skip_log:
        # "조리법매칭실패:굽는법" 같은 값은 사유 집계에서 앞부분만 묶는다
        reason_key = r["reason"].split(":")[0]
        skip_reason_counts[reason_key] = skip_reason_counts.get(reason_key, 0) + 1

    print("\n" + "=" * 45)
    print("  적재 완료")
    print("=" * 45)
    print(f"  총 시도: {total_attempted:,}건")
    print(f"  recipe 신규: {recipe_inserted:,}건")
    print(f"  nutrition_recipe 신규: {nutrition_inserted:,}건")
    print(f"  skip: {len(skip_log):,}건")
    for reason, cnt in sorted(skip_reason_counts.items(), key=lambda x: -x[1]):
        print(f"    - {reason}: {cnt:,}건")
    print(f"  필드 오버플로(NULL 처리): {len(overflow_log):,}건")
    if way2_fail_counts:
        print(f"  RCP_WAY2 매칭 실패 값별 집계:")
        for val, cnt in sorted(way2_fail_counts.items(), key=lambda x: -x[1]):
            print(f"    - '{val}': {cnt:,}건")
    print(f"\n  skip 로그: {skip_path}")
    print(f"  오버플로 로그: {overflow_path}")
    print(f"  조리법매칭실패 집계 로그: {way2_path}")

    with engine.connect() as conn:
        cnt = conn.execute(
            text("SELECT COUNT(*) FROM recipe WHERE data_source = :ds AND notes LIKE '%RCP_SEQ=%'"),
            {"ds": RECIPE_DATA_SOURCE},
        ).scalar()
    print(f"\n  recipe(식품안전나라 원본, notes에 RCP_SEQ 있음) 총 행 수: {cnt:,}행")


if __name__ == "__main__":
    main()
