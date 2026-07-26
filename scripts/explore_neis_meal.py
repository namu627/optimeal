#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: explore_neis_meal.py
목적: NEIS 급식식단정보 API(mealServiceDietInfo)를 소량(학교 1곳, 1개월치) 호출해
      요리명(DDISH_NM)에서 재료를 추출할 수 있는지 관찰한다. DB 적재는 하지 않는다
      (seasonal_ingredient/ingredient 등 어떤 테이블에도 INSERT하지 않음 — 오직 SELECT로
      ingredient/ingredient_synonym을 조회해 매칭 여부만 확인).

실행:
  docker exec optimeal_app python scripts/explore_neis_meal.py

수집 대상: 서울(ATPT_OFCDC_SC_CODE=B10) 초등학교 1곳, 2026-03-01~2026-03-31.
학교코드는 schoolInfo API로 먼저 조회해서 얻는다 (하드코딩 금지).

경로 A/B/C 검증 로직:
  - 경로 A: load_ingredient_price.py의 normalize_strip_parens/normalize_strip_modifiers를
    재사용해 DDISH_NM을 정규화한 뒤, ingredient/ingredient_synonym 테이블 이름이
    부분 문자열로 포함되는지 검사 (품목명 완전일치 매칭과 달리 요리명은 여러 재료가
    공백 없이 붙어 있어 포함 매칭이 필요함).
  - 경로 B: ORPLC_INFO의 "품목명:원산지" 콤마 구분 항목에서 품목명만 추출.
  - 경로 C: DDISH_NM 괄호 안 알레르기 번호(1~19)를 19종 표로 역매핑.
"""

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests
from sqlalchemy import text

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from load_ingredient_price import get_engine, normalize_strip_parens, normalize_strip_modifiers  # noqa: E402

OUT_DIR = BASE_DIR / "data" / "external"
OUT_PATH = OUT_DIR / "neis_sample.json"

API_HOST = "https://open.neis.go.kr/hub"
SCHOOL_INFO_SERVICE = "schoolInfo"
MEAL_SERVICE = "mealServiceDietInfo"

ATPT_OFCDC_SC_CODE = "B10"   # 서울특별시교육청 (공개된 표준 시도교육청 코드)
SCHUL_NM_QUERY = "초등학교"   # 이름에 '초등학교'가 포함된 학교 검색
MLSV_FROM_YMD = "20260301"
MLSV_TO_YMD = "20260331"
REQUEST_DELAY_SEC = 0.3

ALLERGY_MAP = {
    "1": "난류", "2": "우유", "3": "메밀", "4": "땅콩", "5": "대두",
    "6": "밀", "7": "고등어", "8": "게", "9": "새우", "10": "돼지고기",
    "11": "복숭아", "12": "토마토", "13": "아황산류", "14": "호두", "15": "닭고기",
    "16": "쇠고기", "17": "오징어", "18": "조개류", "19": "잣",
}

ALLERGY_CODE_PATTERN = re.compile(r"\(([\d.]+)\)")


def get_neis_key():
    """.env에서 NEIS_KEY를 읽어 반환. 값 자체는 어디에도 출력/로그하지 않는다."""
    return os.getenv("NEIS_KEY")


def call_neis_api(session, service, key, params, max_retries=3):
    """
    NEIS Open API 공통 호출. service(schoolInfo/mealServiceDietInfo) 루트 키 아래
    RESULT.CODE와 row를 확인해 (rows: list, code: str|None, success: bool) 반환.
    """
    url = f"{API_HOST}/{service}"
    query = {"KEY": key, "Type": "json", **params}

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, params=query, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError):
            print(f"  [경고] API 호출 실패 (service={service}, 시도 {attempt}/{max_retries})")
            time.sleep(REQUEST_DELAY_SEC)
            continue

        root = payload.get(service) if isinstance(payload, dict) else None
        if not isinstance(root, list) or not root:
            # 인증/파라미터 오류 시 {"RESULT": {"CODE": "...", "MESSAGE": "..."}} 형태로 옴
            result = payload.get("RESULT") if isinstance(payload, dict) else None
            code = result.get("CODE") if isinstance(result, dict) else None
            msg = result.get("MESSAGE") if isinstance(result, dict) else None
            print(f"  [안내] service={service} 응답에 데이터 없음 (code={code}, message={msg})")
            return [], code, True

        head = root[0].get("head") if isinstance(root[0], dict) else None
        code = None
        if isinstance(head, list):
            for h in head:
                if isinstance(h, dict) and "RESULT" in h:
                    code = h["RESULT"].get("CODE")

        rows = root[1].get("row") if len(root) > 1 and isinstance(root[1], dict) else []
        return rows, code, True

    return [], None, False


def find_school(session, key):
    """schoolInfo API로 서울 소재 '초등학교' 1곳을 조회해 학교코드를 얻는다 (하드코딩 금지)."""
    rows, code, ok = call_neis_api(session, SCHOOL_INFO_SERVICE, key, {
        "ATPT_OFCDC_SC_CODE": ATPT_OFCDC_SC_CODE,
        "SCHUL_NM": SCHUL_NM_QUERY,
        "pIndex": 1,
        "pSize": 20,
    })
    if not ok or not rows:
        print(f"  [오류] schoolInfo 조회 실패 (code={code})")
        return None

    for row in rows:
        if row.get("SCHUL_KND_SC_NM") == "초등학교":
            return row
    return rows[0]  # 초등학교 필터에 걸리는 게 없으면 첫 결과라도 사용


def fetch_meals(session, key, atpt_code, schul_code, from_ymd=MLSV_FROM_YMD, to_ymd=MLSV_TO_YMD):
    """mealServiceDietInfo API로 1개월치 급식 정보를 조회."""
    rows, code, ok = call_neis_api(session, MEAL_SERVICE, key, {
        "ATPT_OFCDC_SC_CODE": atpt_code,
        "SD_SCHUL_CODE": schul_code,
        "MLSV_FROM_YMD": from_ymd,
        "MLSV_TO_YMD": to_ymd,
        "pIndex": 1,
        "pSize": 1000,
    })
    if not ok:
        print(f"  [오류] mealServiceDietInfo 조회 실패 (code={code})")
    return rows, code


def check_historical_availability(session, key, atpt_code, schul_code):
    """
    과거 연도 데이터 제공 여부 확인 — 같은 학교·같은 월(3월)을 여러 과거 연도로 조회해
    실제로 급식 레코드가 나오는지, 몇 년 전까지 나오는지 확인한다.
    """
    print("\n" + "=" * 50)
    print("  과거 연도 데이터 제공 여부 확인")
    print("=" * 50)
    results = {}
    for year in (2026, 2025, 2023, 2021, 2019):
        from_ymd = f"{year}0301"
        to_ymd = f"{year}0331"
        rows, code = fetch_meals(session, key, atpt_code, schul_code, from_ymd, to_ymd)
        results[year] = (len(rows), code)
        print(f"  {year}년 3월: {len(rows)}건 (code={code})")
        time.sleep(REQUEST_DELAY_SEC)
    return results


# ─────────────────────────────────────────────────────────────────
# 요리명 → 개별 요리 리스트 파싱 (DDISH_NM은 <br/> 구분 + 괄호 알레르기번호 포함)
# ─────────────────────────────────────────────────────────────────
def split_dishes(ddish_nm):
    raw_dishes = [d.strip() for d in str(ddish_nm or "").split("<br/>") if d.strip()]
    return raw_dishes


def strip_allergy_and_clean(dish_raw):
    """알레르기 번호 괄호 제거 후 정규화 (정규화 함수는 load_ingredient_price.py 재사용)."""
    no_parens = normalize_strip_parens(dish_raw)
    return normalize_strip_modifiers(no_parens)


# ─────────────────────────────────────────────────────────────────
# 경로 A: 요리명 부분 문자열 매칭 (ingredient/ingredient_synonym 테이블 대상)
# ─────────────────────────────────────────────────────────────────
def load_ingredient_names(conn):
    """길이 내림차순으로 정렬된 (ingredient_id, name) 목록 — ingredient + synonym 합침.
    2글자 미만 이름은 오매칭이 심해 제외."""
    names = []
    for row in conn.execute(text("SELECT ingredient_id, ingredient_name FROM ingredient")):
        if len(row.ingredient_name) >= 2:
            names.append((row.ingredient_id, row.ingredient_name))
    for row in conn.execute(text(
        "SELECT i.ingredient_id, s.synonym_name FROM ingredient_synonym s "
        "JOIN ingredient i ON i.ingredient_id = s.ingredient_id"
    )):
        if len(row.synonym_name) >= 2:
            names.append((row.ingredient_id, row.synonym_name))
    names.sort(key=lambda x: -len(x[1]))
    return names


def extract_ingredients_path_a(dish_clean, sorted_names):
    """
    정규화된 요리명 문자열 안에 ingredient_name(길이 내림차순)이 부분 문자열로
    등장하는지 검사. 이미 매칭된(더 긴) 이름에 완전히 포함되는 짧은 이름은
    중복 카운트하지 않는다 (예: '돼지고기' 매칭 후 그 안의 '고기' 재카운트 방지).
    반환: [(ingredient_id, matched_name), ...]
    """
    matched = []
    covered_spans = []
    for ing_id, name in sorted_names:
        idx = dish_clean.find(name)
        if idx == -1:
            continue
        span = (idx, idx + len(name))
        if any(span[0] >= c[0] and span[1] <= c[1] for c in covered_spans):
            continue
        matched.append((ing_id, name))
        covered_spans.append(span)
    return matched


# ─────────────────────────────────────────────────────────────────
# 경로 B: ORPLC_INFO 품목명 추출
# ─────────────────────────────────────────────────────────────────
def extract_orplc_items(orplc_info):
    """'품목명 : 원산지' <br/> 구분 항목에서 품목명만 추출 (실측 확인: 구분자는 콤마가 아니라 <br/>)."""
    items = []
    for chunk in str(orplc_info or "").split("<br/>"):
        chunk = chunk.strip()
        if ":" in chunk:
            item = chunk.split(":", 1)[0].strip()
            if item:
                items.append(item)
    return items


# ─────────────────────────────────────────────────────────────────
# 경로 C: 알레르기 번호 역매핑
# ─────────────────────────────────────────────────────────────────
def extract_allergy_codes(dish_raw):
    codes = []
    for m in ALLERGY_CODE_PATTERN.finditer(dish_raw):
        for code in m.group(1).split("."):
            code = code.strip()
            if code in ALLERGY_MAP:
                codes.append(code)
    return codes


def main():
    print("=" * 50)
    print("  NEIS 급식식단정보 탐색 스크립트 (관찰 전용, DB 미반영)")
    print("=" * 50)

    key = get_neis_key()
    if not key:
        print("  [오류] .env에 NEIS_KEY 값이 비어 있습니다.")
        sys.exit(1)

    session = requests.Session()

    print(f"\n  1) 학교 조회: ATPT_OFCDC_SC_CODE={ATPT_OFCDC_SC_CODE}, SCHUL_NM 포함='{SCHUL_NM_QUERY}'")
    school = find_school(session, key)
    if not school:
        print("  [오류] 학교를 찾지 못했습니다. 종료.")
        sys.exit(1)

    schul_nm = school.get("SCHUL_NM")
    schul_code = school.get("SD_SCHUL_CODE")
    atpt_code = school.get("ATPT_OFCDC_SC_CODE")
    print(f"     선택된 학교: {schul_nm} (SD_SCHUL_CODE={schul_code}, 종류={school.get('SCHUL_KND_SC_NM')})")

    time.sleep(REQUEST_DELAY_SEC)

    print(f"\n  2) 급식 조회: {MLSV_FROM_YMD}~{MLSV_TO_YMD}")
    meals, _ = fetch_meals(session, key, atpt_code, schul_code)
    print(f"     급식 레코드(급식일×식사구분) 수: {len(meals)}건")

    time.sleep(REQUEST_DELAY_SEC)
    check_historical_availability(session, key, atpt_code, schul_code)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"school": school, "meals": meals}, f, ensure_ascii=False, indent=2)
    print(f"     원본 저장: {OUT_PATH}")

    if not meals:
        print("\n  [안내] 급식 데이터가 0건입니다. 기간/학교를 조정해야 합니다. 종료.")
        return

    all_dishes_raw = []       # 알레르기번호 포함 원문
    all_dishes_clean = []     # 정규화 후 (경로 A용)
    all_orplc = []
    meal_days = set()

    for meal in meals:
        meal_days.add(meal.get("MLSV_YMD"))
        for dish_raw in split_dishes(meal.get("DDISH_NM")):
            all_dishes_raw.append(dish_raw)
            all_dishes_clean.append(strip_allergy_and_clean(dish_raw))
        orplc = meal.get("ORPLC_INFO")
        if orplc:
            all_orplc.append(orplc)

    print(f"\n  급식일 수: {len(meal_days)}일")
    print(f"  요리 총 개수: {len(all_dishes_raw)}개")

    print("\n  DDISH_NM 원문 샘플 10개:")
    for d in all_dishes_raw[:10]:
        print(f"    - {d}")

    print("\n  ORPLC_INFO 원문 샘플 3개:")
    for o in all_orplc[:3]:
        print(f"    - {o}")

    # ── 경로 A ──
    engine = get_engine()
    with engine.connect() as conn:
        sorted_names = load_ingredient_names(conn)

    dishes_with_match = 0
    total_extracted = 0
    path_a_examples = []
    path_a_fail_examples = []
    for raw, clean in zip(all_dishes_raw, all_dishes_clean):
        matched = extract_ingredients_path_a(clean, sorted_names)
        if matched:
            dishes_with_match += 1
            total_extracted += len(matched)
            if len(path_a_examples) < 10:
                path_a_examples.append((raw, [m[1] for m in matched]))
        else:
            if len(path_a_fail_examples) < 10:
                path_a_fail_examples.append(raw)

    n_dishes = len(all_dishes_raw)
    coverage_a = round(dishes_with_match / n_dishes * 100, 1) if n_dishes else 0.0
    avg_extracted = round(total_extracted / n_dishes, 2) if n_dishes else 0.0

    print("\n" + "=" * 50)
    print("  경로 A — 요리명 직접 파싱 (부분 문자열 매칭)")
    print("=" * 50)
    print(f"  재료 1개 이상 추출된 요리 비율: {dishes_with_match}/{n_dishes} ({coverage_a}%)")
    print(f"  요리당 평균 추출 재료 수: {avg_extracted}개")
    print("  성공 예시:")
    for raw, names in path_a_examples:
        print(f"    - {raw} → {names}")
    print("  실패 예시:")
    for raw in path_a_fail_examples:
        print(f"    - {raw}")

    # ── 경로 B ──
    orplc_item_counter = Counter()
    for o in all_orplc:
        for item in extract_orplc_items(o):
            orplc_item_counter[item] += 1

    print("\n" + "=" * 50)
    print("  경로 B — 원산지 정보(ORPLC_INFO) 품목 추출")
    print("=" * 50)
    print(f"  ORPLC_INFO 보유 급식 레코드 수: {len(all_orplc)}건")
    print(f"  추출된 고유 품목 종수: {len(orplc_item_counter)}종")
    print("  품목별 등장 빈도:")
    for item, cnt in orplc_item_counter.most_common(20):
        print(f"    - {item}: {cnt}회")

    # ── 경로 C ──
    allergy_counter = Counter()
    for raw in all_dishes_raw:
        for code in extract_allergy_codes(raw):
            allergy_counter[code] += 1

    print("\n" + "=" * 50)
    print("  경로 C — 알레르기 번호 역매핑")
    print("=" * 50)
    print(f"  등장한 알레르기 코드 종수: {len(allergy_counter)}/19종")
    for code, cnt in allergy_counter.most_common():
        print(f"    - {code}.{ALLERGY_MAP[code]}: {cnt}회")

    print("\n" + "=" * 50)
    print("  탐색 완료 (DB에는 아무것도 적재하지 않았습니다)")
    print("=" * 50)


if __name__ == "__main__":
    main()
