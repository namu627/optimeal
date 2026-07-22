#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: load_seasonal_ingredient.py
목적: NEIS 급식식단정보를 대량 수집해 seasonal_ingredient(freq_score)를 적재한다.
      (프롬프트_제철분석_2단계_본적재.md 기준. explore_neis_meal.py는 관찰 전용으로
      그대로 두고 이 스크립트에서 학교조회/API호출/파싱 함수 일부를 재사용(import)한다.)

단계별 하위 명령 (사용자 검토가 필요한 지점에서 각각 멈추도록 분리):
  collect         대량 수집 (30개교×3개년, 이어받기 지원)
  unmatched-top50 수집분 전체에서 A∪C(+사전)로도 미매칭인 요리 상위 50개 출력 (DB 미반영)
  remeasure       경기초 2026-03 표본(1단계와 동일)에 개선된 매칭기를 적용해 전후 비교
  compute         freq_score 방식1/방식2 계산 + 검증 케이스 표 출력 (DB 미반영)
  load            seasonal_ingredient에 UPSERT (compute 결과가 상식과 맞을 때만 수동 실행)

DB에 쓰는 단계는 'load' 하나뿐이다. 나머지는 전부 관찰/집계용.
"""

import argparse
import csv
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from load_ingredient_price import get_engine, normalize_strip_parens, normalize_strip_modifiers  # noqa: E402
from explore_neis_meal import (  # noqa: E402
    get_neis_key, call_neis_api, find_school, fetch_meals,
    split_dishes, strip_allergy_and_clean, extract_allergy_codes,
    load_ingredient_names, extract_ingredients_path_a,
    ALLERGY_MAP, SCHOOL_INFO_SERVICE, REQUEST_DELAY_SEC,
)

RAW_DIR = BASE_DIR / "data" / "external" / "neis_meals_raw"
SCHOOLS_CACHE_PATH = BASE_DIR / "data" / "external" / "neis_schools_selected.json"
DICT_CSV_PATH = BASE_DIR / "data" / "external" / "dish_ingredient_dict.csv"
COLLECT_FAIL_LOG = BASE_DIR / "data" / "external" / "neis_collect_failures.csv"
UNMATCHED_TOP50_PATH = BASE_DIR / "data" / "external" / "dish_unmatched_top50.csv"

# ─────────────────────────────────────────────────────────────────
# 대량 수집 대상 (지시서 확정값)
# ─────────────────────────────────────────────────────────────────
REGIONS = [
    ("B10", "서울"), ("J10", "경기"), ("C10", "부산"),
    ("G10", "대전"), ("P10", "전북"), ("E10", "인천"),
]
SCHOOLS_PER_REGION = 5
YEARS = [2023, 2024, 2025]

# 프리플라이트에서 광주(F10)·전남(Q10)은 3개년 전체 0건(4개교 교차확인)으로 확인돼
# 제외했다. 전북(P10)은 2024/2025는 정상이나 2023년만 구조적으로 비어있음
# (서로 다른 2개교에서 동일 패턴 확인 — 2024.1 전북특별자치도 출범에 따른 데이터
# 이관 누락으로 추정, 원인 규명은 실익이 없어 보류) — compute 단계에서 해당 셀은
# 자동 제외 처리한다 (아래 coverage-matrix 참고).

# 학교 선정 검증에 쓰는 기준월 — 방학이 없는 3월, 가장 최근에 데이터가 확실한 2025년.
VALIDATION_FROM_YMD = "20250301"
VALIDATION_TO_YMD = "20250331"

# ─────────────────────────────────────────────────────────────────
# 1-1. 알레르기 코드 → ingredient_id 매핑 (실제 DB 조회로 확정, 추측 없음)
# ─────────────────────────────────────────────────────────────────
# 확정 근거:
#   - 계란/대두/쇠고기 등은 ingredient_name 완전일치가 안 맞아 ingredient_synonym으로 해결
#     (계란→달걀 id=37, 대두→콩(대두) id=109 [콩 id=1149과 구분], 쇠고기→소고기 id=242)
#   - 03(메밀), 06(밀): ingredient/synonym 어디에도 "메밀"/"밀" 단독 항목 없음
#     (메밀국수/메밀가루/메밀묵, 밀가루/박력분/중력분 같은 가공형만 존재) → 매핑 불가, 제외
#   - 13(아황산류): 재료가 아닌 첨가물 → 제외 (지시서 명시)
#   - 18(조개류): 사용자 확인 후 제외 — 굴(겨울)/전복(여름)/홍합 등 제철이 서로 다른
#     재료를 하나의 카테고리로 묶어 "조개류" 하나의 freq_score로 합치면 계절 신호가
#     상쇄된다. 또한 굴전/홍합미역국/전복죽 등은 경로 A가 이미 요리명에서 직접 잡아내므로
#     경로 C로 또 잡으면 실재하지 않는 "조개"라는 단일 재료가 중복 계상된다.
#     (판단기준: 코드가 특정 재료 하나를 가리키면 매핑, 여러 재료를 묶은 카테고리면 제외.
#      묶인 재료들의 제철이 다르면 무조건 제외.)
#   - 01(난류)는 예외로 채택: 엄밀히는 계란·메추리알을 묶은 카테고리이지만 계란이
#     압도적 비중이고 둘 다 제철성이 없어(연중 상시) 어느 쪽으로 판정해도 왜곡이 없다.
ALLERGY_INGREDIENT_MAP = {
    "01": (37, "달걀"),      # 난류 (예외 채택 — 계란 압도적, 무제철이라 왜곡 없음)
    "02": (996, "우유"),
    "04": (50, "땅콩"),
    "05": (109, "콩(대두)"),  # 대두 — synonym으로 확인, 콩(id=1149)과 다름
    "07": (420, "고등어"),
    "08": (413, "게"),
    "09": (57, "새우"),
    "10": (118, "돼지고기"),
    "11": (747, "복숭아"),
    "12": (88, "토마토"),
    "14": (62, "호두"),
    "15": (100, "닭고기"),
    "16": (242, "소고기"),    # 쇠고기 — synonym으로 확인
    "17": (15, "오징어"),
    "19": (85, "잣"),
}
ALLERGY_EXCLUDED = {
    "03": "메밀 단독 항목 없음 (메밀국수/메밀가루 등 가공형만 존재) — 매핑 불가",
    "06": "밀 단독 항목 없음 (밀가루/박력분/중력분 등 가공형만 존재) — 매핑 불가",
    "13": "아황산류 — 재료가 아닌 첨가물",
    "18": "조개류 — 굴/전복/홍합 등 제철 상이한 재료의 카테고리, A와 중복계상 위험 (사용자 확인 후 제외)",
}


def load_dish_dict():
    """
    data/external/dish_ingredient_dict.csv 로드: dish_pattern,ingredient_name,note
    ingredient_name은 실제 ingredient 마스터에 존재하는 이름이어야 하며, 여기서
    ingredient_id로 변환해 (pattern, ingredient_id, ingredient_name) 리스트를 반환한다.
    마스터에 없는 ingredient_name이 있으면 즉시 에러를 내 조용히 넘어가지 않게 한다.
    """
    if not DICT_CSV_PATH.exists():
        return []

    engine = get_engine()
    entries = []
    with open(DICT_CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        with engine.connect() as conn:
            for row in reader:
                pattern = (row.get("dish_pattern") or "").strip()
                ing_name = (row.get("ingredient_name") or "").strip()
                if not pattern or not ing_name:
                    continue
                found = conn.execute(
                    text("SELECT ingredient_id FROM ingredient WHERE ingredient_name = :n"),
                    {"n": ing_name}
                ).fetchone()
                if not found:
                    found = conn.execute(
                        text("SELECT ingredient_id FROM ingredient_synonym WHERE synonym_name = :n"),
                        {"n": ing_name}
                    ).fetchone()
                if not found:
                    raise ValueError(
                        f"dish_ingredient_dict.csv의 ingredient_name='{ing_name}'이 "
                        f"ingredient/ingredient_synonym 어디에도 없습니다. 임의 생성 금지 — CSV를 고치세요."
                    )
                entries.append((pattern, found[0], ing_name))
    return entries


# ─────────────────────────────────────────────────────────────────
# 1-3. 1글자 재료명 조건부 허용
# ─────────────────────────────────────────────────────────────────
# 조리법 접미사 목록 — cooking_method.method_name(볶음/찜/구이/무침/조림/튀김)에서
# 4개는 그대로, '국'/'탕'/'찌개'는 '끓이기' 계열 요리에서 실제 관찰된(경기초 표본)
# 압도적으로 흔한 명사형 접미사라 추가했다. '밥'/'자반'/'생채'는 top-50 미매칭 검토
# 결과(팥밥/김자반/무생채)를 보고 사용자가 추가 지시한 것.
# '쌈'/'볶이'/'나물'/'채'는 onechar-audit 1차 결과에서 remainder 검증을 추가하며
# 같이 드러난 실제 손실 사례를 보고 추가했다 (오리훈제구이무쌈/모듬떡볶이/구이콩나물무침/
# 구이파채무침 — 전부 명확한 정상 용례라 접미사로 인정).
ONE_CHAR_SUFFIXES = [
    "구이", "무침", "볶음", "조림", "찜", "튀김", "국", "탕", "찌개", "밥", "자반", "생채",
    "쌈", "볶이", "나물", "채",
]

# 어순 반대(재료가 뒤에 오는 경우) — "구이김"/"도시락김"/"돌김자반구이"처럼 아래
# 접두 조리법/수식어 '바로 뒤'에 1글자 재료가 붙는 경우만 조건부 인정. 오탐 위험이
# 커서(예: "모듬무침"이 "모듬"+"무"로 잘못 걸릴 수 있음) top-30 감사 결과를 사용자가
# 확인한 뒤에만 compute에 반영한다.
ONE_CHAR_REVERSE_PREFIXES = ["구이", "도시락", "돌", "모듬"]


def load_one_char_names(conn):
    """1글자 ingredient_name/synonym_name 목록 (ingredient_id, name) 반환."""
    names = []
    seen = set()
    for row in conn.execute(text("SELECT ingredient_id, ingredient_name FROM ingredient WHERE length(ingredient_name) = 1")):
        key = (row.ingredient_id, row.ingredient_name)
        if key not in seen:
            names.append(key)
            seen.add(key)
    for row in conn.execute(text("SELECT ingredient_id, synonym_name FROM ingredient_synonym WHERE length(synonym_name) = 1")):
        key = (row.ingredient_id, row.synonym_name)
        if key not in seen:
            names.append(key)
            seen.add(key)
    return names


def match_one_char(dish_clean, one_char_names):
    """
    1글자 재료명 인정 조건 (3가지 중 하나):
      1) 단독: 요리명 전체가 정확히 그 1글자 그 자체 (예: '귤', '배')
      2) 정방향: '[1글자][조리법접미사]'로 전체가 정확히 구성 (예: '김구이'=김+구이 인정 /
         '김치찌개'는 '김'+'치찌개'인데 '치찌개'가 접미사 목록에 없어 불인정)
      3) 역방향(조건부): ONE_CHAR_REVERSE_PREFIXES 뒤에 1글자가 바로 붙는 경우
         (예: '구이김', '도시락김', '돌김자반구이') — 문자열 어디에 있든 인정하되
         반드시 지정된 접두어 뒤일 때만 인정한다.
    """
    matched = []
    for ing_id, name in one_char_names:
        if dish_clean.startswith(name):
            remainder = dish_clean[len(name):]
            if remainder == "" or remainder in ONE_CHAR_SUFFIXES:
                matched.append((ing_id, name))
                continue
        for prefix in ONE_CHAR_REVERSE_PREFIXES:
            combo = prefix + name
            idx = dish_clean.find(combo)
            if idx == -1:
                continue
            # combo 뒤에 남는 부분이 없거나(문자열 끝) 알려진 접미사로 "시작"할 때만
            # 인정한다 — 그냥 in 체크만 하면 '구이'+'김'이 '구이김치볶음'(김치!) 안에서도,
            # '구이'+'마'가 '구이마늘바게트'(마늘!) 안에서도 걸려버리는 오탐이 생긴다.
            # (onechar-audit 결과로 실제 확인된 버그 — remainder가 '치...'/'늘...'로
            # 시작해 어떤 접미사와도 안 맞으므로 아래 조건에서 자동으로 걸러진다.)
            remainder = dish_clean[idx + len(combo):]
            if remainder == "" or any(remainder.startswith(suf) for suf in ONE_CHAR_SUFFIXES):
                matched.append((ing_id, name))
                break
    return matched


# ─────────────────────────────────────────────────────────────────
# 1-1. 경로 A ∪ C ∪ 사전 결합 (요리 1개당 재료 id는 set으로 중복 제거)
# ─────────────────────────────────────────────────────────────────
def extract_ingredients_combined(dish_raw, dish_clean, sorted_names, one_char_names, dish_dict):
    result = {}  # ingredient_id -> (name, sources: set)

    for ing_id, name in extract_ingredients_path_a(dish_clean, sorted_names):
        result.setdefault(ing_id, (name, set()))[1].add("A")

    for ing_id, name in match_one_char(dish_clean, one_char_names):
        result.setdefault(ing_id, (name, set()))[1].add("A_1char")

    for code in extract_allergy_codes(dish_raw):
        if code in ALLERGY_INGREDIENT_MAP:
            ing_id, name = ALLERGY_INGREDIENT_MAP[code]
            result.setdefault(ing_id, (name, set()))[1].add("C")

    for pattern, ing_id, name in dish_dict:
        if pattern in dish_raw or pattern in dish_clean:
            result.setdefault(ing_id, (name, set()))[1].add("dict")

    return result  # {ingredient_id: (name, {sources})}


# ─────────────────────────────────────────────────────────────────
# 단계: collect — 대량 수집 (30개교 × 3개년, 이어받기 지원)
# ─────────────────────────────────────────────────────────────────
def is_valid_candidate(row):
    """'(가칭)' 신설예정교, SD_SCHUL_CODE 공백인 학교는 후보에서 제외."""
    name = row.get("SCHUL_NM") or ""
    code = (row.get("SD_SCHUL_CODE") or "").strip()
    if name.startswith("(가칭)"):
        return False
    if not code:
        return False
    return True


def select_schools(session, key, force_refresh=False):
    """
    지역 6곳 × 5개교. schoolInfo로 후보를 뽑되 '(가칭)'/코드공백 학교는 제외하고,
    남은 후보를 가나다순으로 하나씩 실제로 mealServiceDietInfo(2025-03)를 호출해
    데이터가 실제로 나오는 학교만 채택한다 (미달이면 다음 후보로 넘어감).
    """
    if SCHOOLS_CACHE_PATH.exists() and not force_refresh:
        with open(SCHOOLS_CACHE_PATH, encoding="utf-8") as f:
            print(f"  [안내] 학교 목록 캐시 사용: {SCHOOLS_CACHE_PATH}")
            return json.load(f)

    all_schools = []
    for region_code, region_label in REGIONS:
        rows, code, ok = call_neis_api(session, SCHOOL_INFO_SERVICE, key, {
            "ATPT_OFCDC_SC_CODE": region_code,
            "SCHUL_NM": "초등학교",
            "pIndex": 1,
            "pSize": 100,
        })
        if not ok or not rows:
            print(f"  [경고] {region_label}({region_code}) schoolInfo 조회 실패 (code={code}) — 건너뜀")
            continue

        candidates = [r for r in rows if r.get("SCHUL_KND_SC_NM") == "초등학교" and is_valid_candidate(r)]
        skipped_invalid = len([r for r in rows if r.get("SCHUL_KND_SC_NM") == "초등학교"]) - len(candidates)

        picked = []
        skipped_no_data = 0
        for cand in candidates:
            if len(picked) >= SCHOOLS_PER_REGION:
                break
            schul_code = cand.get("SD_SCHUL_CODE")
            test_rows, test_code = fetch_meals(session, key, region_code, schul_code, VALIDATION_FROM_YMD, VALIDATION_TO_YMD)
            time.sleep(REQUEST_DELAY_SEC)
            if test_rows:
                picked.append(cand)
            else:
                skipped_no_data += 1

        print(f"  {region_label}({region_code}): 후보 {len(candidates)}곳"
              f"('(가칭)'/코드공백 {skipped_invalid}곳 사전제외, 검증실패 {skipped_no_data}곳) 중 {len(picked)}곳 채택")
        for r in picked:
            all_schools.append({
                "ATPT_OFCDC_SC_CODE": r.get("ATPT_OFCDC_SC_CODE"),
                "SD_SCHUL_CODE": r.get("SD_SCHUL_CODE"),
                "SCHUL_NM": r.get("SCHUL_NM"),
                "region_label": region_label,
            })

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with open(SCHOOLS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(all_schools, f, ensure_ascii=False, indent=2)
    return all_schools


def cmd_collect(args):
    key = get_neis_key()
    if not key:
        print("  [오류] .env에 NEIS_KEY 값이 비어 있습니다.")
        sys.exit(1)

    import requests
    session = requests.Session()

    print("=" * 50)
    print("  1) 학교 선정 (6개 시도 × 5개교)")
    print("=" * 50)
    schools = select_schools(session, key, force_refresh=getattr(args, "refresh_schools", False))
    print(f"  총 선정 학교 수: {len(schools)}곳")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    fail_log = []
    total_calls = 0
    total_cached = 0

    print("\n" + "=" * 50)
    print("  2) 학교×연도별 급식 수집 (이어받기 지원)")
    print("=" * 50)
    for school in schools:
        atpt = school["ATPT_OFCDC_SC_CODE"]
        schul = school["SD_SCHUL_CODE"]
        for year in YEARS:
            out_path = RAW_DIR / f"{atpt}_{schul}_{year}.json"
            if out_path.exists():
                total_cached += 1
                continue

            from_ymd = f"{year}0101"
            to_ymd = f"{year}1231"
            rows, code = fetch_meals(session, key, atpt, schul, from_ymd, to_ymd)
            total_calls += 1

            if code not in ("INFO-000", "INFO-200"):
                fail_log.append({
                    "ATPT_OFCDC_SC_CODE": atpt, "SD_SCHUL_CODE": schul,
                    "SCHUL_NM": school["SCHUL_NM"], "year": year, "code": code,
                })
                print(f"  [실패] {school['SCHUL_NM']} {year}년 (code={code}) — 건너뜀")
                time.sleep(REQUEST_DELAY_SEC)
                continue

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"school": school, "meals": rows}, f, ensure_ascii=False, indent=2)
            print(f"  {school['SCHUL_NM']}({school['region_label']}) {year}년: {len(rows)}건 저장")
            time.sleep(REQUEST_DELAY_SEC)

    print(f"\n  신규 API 호출: {total_calls}건 / 캐시 사용(건너뜀): {total_cached}건")
    print(f"  실패: {len(fail_log)}건")

    if fail_log:
        with open(COLLECT_FAIL_LOG, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["ATPT_OFCDC_SC_CODE", "SD_SCHUL_CODE", "SCHUL_NM", "year", "code"])
            writer.writeheader()
            writer.writerows(fail_log)
        print(f"  실패 로그: {COLLECT_FAIL_LOG}")


# ─────────────────────────────────────────────────────────────────
# 공통: 저장된 raw JSON 전체를 순회하며 (dish_raw, dish_clean, meal_meta) 생성
# 현재 학교 로스터(schools_selected.json)에 있는 (지역,학교)만 포함한다 —
# 과거에 남은 광주/부산 (가칭)학교 등 로스터 밖 orphan 파일은 자동 제외.
# ─────────────────────────────────────────────────────────────────
# 전체 요리명 특수문자 전수조사 결과(한글/영문/숫자/공백 제외 문자 전부 열거해 확인):
# *, ~, -, $, ., @, +, ,, ♩, ♬, ♥, ^, #, ＃, ㅁ, ㅈ, ㅋ, ㅅ, _, [, ], ), & 전부
# 장식용 마커·구분자였고(예: '*배추김치'=배추김치, 'ㅁ오븐떡갈비'=오븐떡갈비),
# 재료 정보를 담은 문자는 하나도 없었다. 화이트리스트(한글 음절/영문/숫자/공백만
# 허용) 방식으로 나머지를 전부 제거한다. 애초의 "'*'로 시작하면 제외" 접두사
# 규칙은 틀렸음이 확인돼 폐기 — '*배추김치'(632회)처럼 진짜 요리를 지웠었다.
MARKER_CHARS_PATTERN = re.compile(r"[^가-힣A-Za-z0-9 ]")


def strip_markers(name):
    return MARKER_CHARS_PATTERN.sub("", name).strip()


# 실제 요리가 아니라 식단표 섹션 헤더/이벤트 라벨인 항목 — 마커 제거 후 이름이
# 아래와 "정확히 일치"할 때만 제외한다(접두사 일괄 제외 아님). 매칭 시도뿐 아니라
# 분모(총 요리 수)에서도 완전히 제외한다(안 그러면 모든 재료 freq_score가 희석됨).
# 목록은 마커 제거 후 형태 기준: 'V.T.S DAY'→'VTS DAY', '공통양념-1'→'공통양념1',
# '*음료[후식]'→'음료후식'(관측된 실제 패턴, 두 헤더 단어가 이어붙은 형태).
NOISE_EXACT = {
    "후식", "음료", "공통양념", "공통양념1", "공통양념2", "기본양념류",
    "VTS DAY", "음료후식",
}


def is_noise_dish(dish_marker_stripped):
    return dish_marker_stripped in NOISE_EXACT


def load_school_roster():
    if not SCHOOLS_CACHE_PATH.exists():
        return []
    with open(SCHOOLS_CACHE_PATH, encoding="utf-8") as f:
        return json.load(f)


def iter_all_dishes(lunch_only=True):
    roster = load_school_roster()
    valid_pairs = {(s["ATPT_OFCDC_SC_CODE"], s["SD_SCHUL_CODE"]): s["region_label"] for s in roster}

    for path in sorted(RAW_DIR.glob("*.json")):
        parts = path.stem.split("_")
        if len(parts) < 3:
            continue
        atpt, schul, year_str = parts[0], parts[1], parts[2]
        if (atpt, schul) not in valid_pairs:
            continue
        region_label = valid_pairs[(atpt, schul)]

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for meal in data.get("meals", []):
            if lunch_only and meal.get("MMEAL_SC_NM") != "중식":
                continue
            ymd = meal.get("MLSV_YMD")
            month = int(ymd[4:6]) if ymd and len(ymd) >= 6 else None
            year = int(ymd[0:4]) if ymd and len(ymd) >= 4 else None
            for dish_raw in split_dishes(meal.get("DDISH_NM")):
                dish_clean = strip_markers(strip_allergy_and_clean(dish_raw))
                if is_noise_dish(dish_clean):
                    continue
                yield dish_raw, dish_clean, {"month": month, "year": year, "region_label": region_label, "ymd": ymd}


def cmd_noise_report(args):
    """제외된 노이즈 항목(섹션 헤더/이벤트 라벨) 통계 — 규칙별 제거 건수 + 고유 목록."""
    roster = load_school_roster()
    valid_pairs = {(s["ATPT_OFCDC_SC_CODE"], s["SD_SCHUL_CODE"]) for s in roster}

    total_dishes = 0
    removed_counter = Counter()
    for path in sorted(RAW_DIR.glob("*.json")):
        parts = path.stem.split("_")
        if len(parts) < 3 or (parts[0], parts[1]) not in valid_pairs:
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for meal in data.get("meals", []):
            if meal.get("MMEAL_SC_NM") != "중식":
                continue
            for dish_raw in split_dishes(meal.get("DDISH_NM")):
                dish_clean = strip_markers(strip_allergy_and_clean(dish_raw))
                total_dishes += 1
                if is_noise_dish(dish_clean):
                    removed_counter[dish_clean] += 1

    total_removed = sum(removed_counter.values())
    print(f"  전체 요리(필터 전) 수: {total_dishes}")
    print(f"  노이즈로 제거된 행 수: {total_removed} ({round(total_removed/total_dishes*100, 2) if total_dishes else 0}%)")
    print(f"  제거된 고유 항목 수: {len(removed_counter)}")
    print("\n  제거된 고유 항목 전체 목록:")
    for name, cnt in removed_counter.most_common():
        print(f"    - {name}: {cnt}회")


# ─────────────────────────────────────────────────────────────────
# 단계: onechar-audit — 1글자 재료 규칙 수정 후 오탐 감사 (compute 진행 전 필수)
# ─────────────────────────────────────────────────────────────────
def cmd_onechar_audit(args):
    if not any(RAW_DIR.glob("*.json")):
        print(f"  [오류] {RAW_DIR}에 수집된 파일이 없습니다.")
        sys.exit(1)

    engine = get_engine()
    with engine.connect() as conn:
        one_char_names = load_one_char_names(conn)

    # ingredient_id -> Counter(dish_clean -> count)
    per_ingredient = defaultdict(Counter)
    for _dish_raw, dish_clean, _meta in iter_all_dishes():
        for ing_id, name in match_one_char(dish_clean, one_char_names):
            per_ingredient[(ing_id, name)][dish_clean] += 1

    for (ing_id, name), counter in sorted(per_ingredient.items(), key=lambda x: x[0][1]):
        total = sum(counter.values())
        print(f"\n{'=' * 60}")
        print(f"  1글자 재료: '{name}' (id={ing_id}) — 총 매칭 {total}건, 고유 요리명 {len(counter)}개")
        print(f"{'=' * 60}")
        for dish_name, cnt in counter.most_common(30):
            print(f"    - {dish_name}: {cnt}회")


# ─────────────────────────────────────────────────────────────────
# 단계: coverage-matrix — 지역×연도×월 커버리지 매트릭스 (score 계산 전 필수 점검)
# ─────────────────────────────────────────────────────────────────
def cmd_coverage_matrix(args):
    roster = load_school_roster()
    if not roster:
        print("  [오류] 학교 목록 캐시가 없습니다. 먼저 collect를 실행하세요.")
        sys.exit(1)

    cell_counts = Counter()
    for _dish_raw, _dish_clean, meta in iter_all_dishes():
        if meta["year"] is None or meta["month"] is None:
            continue
        cell_counts[(meta["region_label"], meta["year"], meta["month"])] += 1

    regions_present = sorted({s["region_label"] for s in roster})

    print("=" * 90)
    print("  지역 × 연도 × 월 커버리지 매트릭스 (중식 요리 수)")
    print("=" * 90)
    header = "  " + f"{'지역':<6}{'연도':<6}" + "".join(f"{m:>5}" for m in range(1, 13))
    print(header)

    empty_cells = []
    matrix_rows = []
    for region in regions_present:
        for year in YEARS:
            row_counts = [cell_counts.get((region, year, m), 0) for m in range(1, 13)]
            print("  " + f"{region:<6}{year:<6}" + "".join(f"{c:>5}" for c in row_counts))
            matrix_rows.append((region, year, row_counts))
            for m, c in zip(range(1, 13), row_counts):
                if c == 0:
                    empty_cells.append((region, year, m))

    print(f"\n  빈 셀(0건) 총 {len(empty_cells)}개 — compute 단계에서 해당 (지역,연도,월) 조합은 평균에서 제외:")
    for region, year, m in empty_cells:
        print(f"    - {region} {year}-{m:02d}")

    matrix_csv = BASE_DIR / "data" / "external" / "coverage_matrix.csv"
    with open(matrix_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["region", "year"] + [f"m{m}" for m in range(1, 13)])
        for region, year, row_counts in matrix_rows:
            writer.writerow([region, year] + row_counts)
    print(f"\n  저장: {matrix_csv}")


# ─────────────────────────────────────────────────────────────────
# 단계: unmatched-top50 — A∪C(+사전)로도 미매칭인 요리 상위 50개 (DB 미반영)
# ─────────────────────────────────────────────────────────────────
def cmd_unmatched_top50(args):
    if not any(RAW_DIR.glob("*.json")):
        print(f"  [오류] {RAW_DIR}에 수집된 파일이 없습니다. 먼저 'collect'를 실행하세요.")
        sys.exit(1)

    engine = get_engine()
    with engine.connect() as conn:
        sorted_names = load_ingredient_names(conn)
        one_char_names = load_one_char_names(conn)
    dish_dict = load_dish_dict()

    unmatched_counter = Counter()
    total_dishes = 0
    matched_dishes = 0

    for dish_raw, dish_clean, _meta in iter_all_dishes():
        total_dishes += 1
        result = extract_ingredients_combined(dish_raw, dish_clean, sorted_names, one_char_names, dish_dict)
        if result:
            matched_dishes += 1
        else:
            unmatched_counter[dish_clean] += 1

    print(f"  전체 요리(중식) 수: {total_dishes}")
    print(f"  매칭됨: {matched_dishes} ({round(matched_dishes/total_dishes*100, 1) if total_dishes else 0}%)")
    print(f"  미매칭 고유 요리명 수: {len(unmatched_counter)}")

    top50 = unmatched_counter.most_common(50)
    with open(UNMATCHED_TOP50_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["dish_clean", "count"])
        writer.writerows(top50)

    print(f"\n  미매칭 상위 {len(top50)}개 (전체: {UNMATCHED_TOP50_PATH}):")
    for name, cnt in top50:
        print(f"    - {name}: {cnt}회")


# ─────────────────────────────────────────────────────────────────
# 단계: remeasure — 경기초 2026-03 표본(1단계와 동일)으로 전후 비교
# ─────────────────────────────────────────────────────────────────
def cmd_remeasure(args):
    sample_path = BASE_DIR / "data" / "external" / "neis_sample.json"
    if not sample_path.exists():
        print(f"  [오류] {sample_path}가 없습니다 (1단계 산출물). 먼저 explore_neis_meal.py를 실행했어야 합니다.")
        sys.exit(1)

    with open(sample_path, encoding="utf-8") as f:
        data = json.load(f)

    engine = get_engine()
    with engine.connect() as conn:
        sorted_names = load_ingredient_names(conn)
        one_char_names = load_one_char_names(conn)
    dish_dict = load_dish_dict()

    total_dishes = 0
    matched_dishes = 0
    total_extracted = 0

    for meal in data["meals"]:
        for dish_raw in split_dishes(meal.get("DDISH_NM")):
            dish_clean = strip_markers(strip_allergy_and_clean(dish_raw))
            if is_noise_dish(dish_clean):
                continue
            total_dishes += 1
            result = extract_ingredients_combined(dish_raw, dish_clean, sorted_names, one_char_names, dish_dict)
            if result:
                matched_dishes += 1
                total_extracted += len(result)

    coverage = round(matched_dishes / total_dishes * 100, 1) if total_dishes else 0.0
    avg = round(total_extracted / total_dishes, 2) if total_dishes else 0.0

    print("=" * 50)
    print("  경기초 2026-03 표본 재측정 (1단계와 동일 표본)")
    print("=" * 50)
    print(f"               1단계    2단계")
    print(f"  커버리지     84.1%    {coverage}%")
    print(f"  요리당 재료  0.96     {avg}")


# ─────────────────────────────────────────────────────────────────
# 단계: compute — freq_score 층화평균 + 방식1/방식2 비교 + 검증케이스 + 30회 미만 제외
# ─────────────────────────────────────────────────────────────────
MIN_TOTAL_OCCURRENCE = 30

VALIDATION_CASES = [
    (164, "쌀", "제철 아님(연중)"),
    (566, "딸기", "겨울~봄"),
    (961, "오이", "여름"),
    (711, "배추", "가을~겨울"),
    (118, "돼지고기", "제철 아님(연중)"),
]


def build_cell_data():
    """
    (region,year,month) 셀별 총 요리 수 / 재료별 등장 요리 수, 그리고 재료별 3개년
    총 등장 횟수(원시, 셀 가중치 없음)를 한 번의 순회로 계산한다.
    """
    engine = get_engine()
    with engine.connect() as conn:
        sorted_names = load_ingredient_names(conn)
        one_char_names = load_one_char_names(conn)
        id_to_name = {row.ingredient_id: row.ingredient_name for row in conn.execute(text("SELECT ingredient_id, ingredient_name FROM ingredient"))}
    dish_dict = load_dish_dict()

    cell_total = Counter()
    cell_ing_count = defaultdict(Counter)
    total_occurrence = Counter()

    for dish_raw, dish_clean, meta in iter_all_dishes():
        region, year, month = meta["region_label"], meta["year"], meta["month"]
        if year is None or month is None:
            continue
        cell = (region, year, month)
        cell_total[cell] += 1
        result = extract_ingredients_combined(dish_raw, dish_clean, sorted_names, one_char_names, dish_dict)
        for ing_id in result:
            cell_ing_count[cell][ing_id] += 1
            total_occurrence[ing_id] += 1

    return cell_total, cell_ing_count, total_occurrence, id_to_name


def compute_stratified_scores(cell_total, cell_ing_count, ingredient_ids):
    """
    방식1(층화평균): (지역×연도) 셀 단위로 비율을 먼저 내고, 그 비율들을 월별로
    균등가중 평균한다 — 셀 총 요리 수가 0인 셀은 건너뛴다(자동 제외).
    방식2: 방식1 값을 그 재료의 12개월 중 최댓값으로 나눠 정규화.
    반환: base[ing_id][month] -> float, normalized[ing_id][month] -> float, used_cells_by_month[month] -> 셀 수
    """
    cells_by_month = defaultdict(list)
    for (region, year, month), total in cell_total.items():
        if total > 0:
            cells_by_month[month].append((region, year, total))

    base = defaultdict(dict)
    for ing_id in ingredient_ids:
        for month in range(1, 13):
            cells = cells_by_month.get(month, [])
            if not cells:
                base[ing_id][month] = 0.0
                continue
            ratios = []
            for region, year, total in cells:
                cnt = cell_ing_count.get((region, year, month), Counter()).get(ing_id, 0)
                ratios.append(cnt / total)
            base[ing_id][month] = statistics.mean(ratios)

    normalized = defaultdict(dict)
    for ing_id in ingredient_ids:
        max_val = max(base[ing_id].values()) if base[ing_id] else 0.0
        for month in range(1, 13):
            normalized[ing_id][month] = (base[ing_id][month] / max_val) if max_val > 0 else 0.0

    used_cells_by_month = {m: len(cells_by_month.get(m, [])) for m in range(1, 13)}
    return base, normalized, used_cells_by_month


CV_THRESHOLD = 0.15  # 최초 0.25 시도 시 오이(CV=0.180)/배추(CV=0.138) 등 계절 재료가
# 제외 목록에 섞여(사용자가 우려한 정확히 그 상황) 0.15로 낮춤 — 근거는 보고서 참고
MAX_PEAK_MONTHS = 6  # 2중 필터 — CV 단독으론 두부(CV=0.156,peak 11개월)처럼 변동은
# 작지만 거의 항상 켜지는 재료를 못 거른다(CV와 peak개월수는 단조관계가 아님:
# 양파 CV=0.243/peak 3개월 vs 부추 CV=0.204/peak 10개월 실측 확인). "제철은
# 정의상 연중 일부"라는 기준으로 peak>6개월(연중 절반 초과)도 별도로 제외.
SEASONAL_CHECK_IDS = [(566, "딸기"), (961, "오이"), (711, "배추"), (835, "수박"), (488, "냉이"), (448, "귤")]


def compute_final_scores():
    """
    확정된 산출 파이프라인:
      1) 3개년 총 등장 30회 미만 제외
      2) 남은 재료의 월별 비율(방식1, 지역×연도 층화평균) CV 계산
      3) 방식2(재료별 상대빈도, 자기 12개월 최댓값=1.0) 적용 → freq_score, is_peak_season=freq_score>=0.7
      4) 2중 필터: CV<0.15 이거나 peak 개월 수(is_peak_season 켜지는 달)가 6 초과면 제외
         (freq_score 계산식·0.7 문턱 자체는 건드리지 않음 — 테이블에 담을 재료를 거르는 방식)
    반환: final_ids, freq_score(dict[id][month]), cv_map(dict[id]->float), peak_months_map(dict[id]->int),
          total_occurrence, id_to_name, count_excluded_ids, cv_excluded_ids, peak_excluded_ids
    """
    cell_total, cell_ing_count, total_occurrence, id_to_name = build_cell_data()
    all_ing_ids = set(total_occurrence.keys())

    count_excluded_ids = {i for i in all_ing_ids if total_occurrence[i] < MIN_TOTAL_OCCURRENCE}
    after_count_ids = all_ing_ids - count_excluded_ids

    base, normalized, used_cells_by_month = compute_stratified_scores(cell_total, cell_ing_count, after_count_ids)

    cv_map = {}
    for ing_id in after_count_ids:
        values = list(base[ing_id].values())
        mean_v = statistics.mean(values)
        cv_map[ing_id] = (statistics.pstdev(values) / mean_v) if mean_v > 0 else float("inf")

    peak_months_map = {
        ing_id: sum(1 for m in range(1, 13) if normalized[ing_id][m] >= 0.7)
        for ing_id in after_count_ids
    }

    cv_excluded_ids = {i for i in after_count_ids if cv_map[i] < CV_THRESHOLD}
    peak_excluded_ids = {i for i in after_count_ids if peak_months_map[i] > MAX_PEAK_MONTHS}
    final_ids = after_count_ids - cv_excluded_ids - peak_excluded_ids

    freq_score = defaultdict(dict)
    for ing_id in after_count_ids:
        for month in range(1, 13):
            freq_score[ing_id][month] = round(normalized[ing_id][month], 3)

    return {
        "final_ids": final_ids, "freq_score": freq_score, "cv_map": cv_map,
        "peak_months_map": peak_months_map,
        "total_occurrence": total_occurrence, "id_to_name": id_to_name,
        "count_excluded_ids": count_excluded_ids, "cv_excluded_ids": cv_excluded_ids,
        "peak_excluded_ids": peak_excluded_ids,
        "used_cells_by_month": used_cells_by_month,
    }


def cmd_compute(args):
    if not any(RAW_DIR.glob("*.json")):
        print(f"  [오류] {RAW_DIR}에 수집된 파일이 없습니다.")
        sys.exit(1)

    print("  집계 중 (지역×연도×월 셀 순회)...")
    r = compute_final_scores()
    id_to_name = r["id_to_name"]
    total_occurrence = r["total_occurrence"]

    print(f"\n  3개년 총 등장 {MIN_TOTAL_OCCURRENCE}회 미만 제외: {len(r['count_excluded_ids'])}종")
    print(f"  CV<{CV_THRESHOLD} 제외(연중 상시 재료, 제철 개념 없음): {len(r['cv_excluded_ids'])}종")
    print(f"  최종 적재 후보: {len(r['final_ids'])}종")

    # ── 확인 1: CV<0.25 제외 목록 전체 ──
    print("\n" + "=" * 90)
    print(f"  확인 1 — CV<{CV_THRESHOLD} 제외 전체 목록 ({len(r['cv_excluded_ids'])}종, CV 오름차순)")
    print("=" * 90)
    excluded_sorted = sorted(r["cv_excluded_ids"], key=lambda i: r["cv_map"][i])
    for ing_id in excluded_sorted:
        print(f"    - {id_to_name.get(ing_id, ing_id)}: CV={r['cv_map'][ing_id]:.3f} (총 등장 {total_occurrence[ing_id]}회)")

    # ── 확인 2: 잔존 계절 재료 검증 케이스 ──
    print("\n" + "=" * 90)
    print("  확인 2 — 잔존 계절 재료 12개월 freq_score 곡선")
    print("=" * 90)
    for ing_id, name in SEASONAL_CHECK_IDS:
        if ing_id in r["cv_excluded_ids"]:
            print(f"\n  [경고] {name}(id={ing_id})가 CV<{CV_THRESHOLD}로 제외됐습니다 — 계절 재료가 잘못 걸러진 것으로 의심됨.")
            continue
        if ing_id in r["count_excluded_ids"]:
            print(f"\n  [경고] {name}(id={ing_id})가 등장 30회 미만으로 제외됐습니다.")
            continue
        if ing_id not in r["freq_score"]:
            print(f"\n  [경고] {name}(id={ing_id})가 이번 수집분에 존재하지 않습니다.")
            continue
        fs = r["freq_score"][ing_id]
        peak_months = [m for m in range(1, 13) if fs[m] >= 0.7]
        print(f"\n  [{name}] CV={r['cv_map'][ing_id]:.3f}")
        print("  월        : " + " ".join(f"{m:>6}" for m in range(1, 13)))
        print("  freq_score: " + " ".join(f"{fs[m]:>6.3f}" for m in range(1, 13)))
        print(f"  is_peak_season 켜지는 달: {peak_months}")

    # ── 확인 3: 월별 peak 재료 수 분포 ──
    print("\n" + "=" * 90)
    print("  확인 3 — 월별 is_peak_season(freq_score>=0.7) 재료 수 분포")
    print("=" * 90)
    month_peak_counts = {m: 0 for m in range(1, 13)}
    for ing_id in r["final_ids"]:
        for m in range(1, 13):
            if r["freq_score"][ing_id][m] >= 0.7:
                month_peak_counts[m] += 1
    print("  월  : " + " ".join(f"{m:>4}" for m in range(1, 13)))
    print("  개수: " + " ".join(f"{month_peak_counts[m]:>4}" for m in range(1, 13)))

    # ── 참고: CV 임계값 0.15/0.35였다면 ──
    print("\n" + "=" * 90)
    print("  참고 — CV 임계값별 제외 규모 비교 (0.25 채택 근거)")
    print("=" * 90)
    for threshold in (0.15, 0.25, 0.35):
        n = sum(1 for i in r["cv_map"] if r["cv_map"][i] < threshold)
        total = len(r["cv_map"])
        print(f"    CV<{threshold}: {n}/{total}종 제외 ({round(n/total*100,1)}%)")

    # ── 결과 저장 (DB 미반영, load 단계에서 재사용) ──
    out_path = BASE_DIR / "data" / "external" / "seasonal_scores_computed.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ingredient_id", "ingredient_name", "month", "freq_score", "is_peak_season",
                          "total_occurrence_3y", "cv"])
        for ing_id in sorted(r["final_ids"]):
            for month in range(1, 13):
                fs = r["freq_score"][ing_id][month]
                writer.writerow([
                    ing_id, id_to_name.get(ing_id, ""), month, fs, fs >= 0.7,
                    total_occurrence[ing_id], round(r["cv_map"][ing_id], 4),
                ])
    print(f"\n  전체 결과 저장: {out_path}")
    print("\n  [안내] 위 확인 1/2/3을 검토한 뒤 이상 없으면 'load' 단계를 실행하세요.")


UPSERT_SQL = text("""
    INSERT INTO seasonal_ingredient (ingredient_id, month, freq_score, is_peak_season, notes)
    VALUES (:ingredient_id, :month, :freq_score, :is_peak_season, :notes)
    ON CONFLICT (ingredient_id, month) DO UPDATE
        SET freq_score = EXCLUDED.freq_score,
            is_peak_season = EXCLUDED.is_peak_season,
            notes = EXCLUDED.notes
""")


def cmd_load(args):
    if not any(RAW_DIR.glob("*.json")):
        print(f"  [오류] {RAW_DIR}에 수집된 파일이 없습니다.")
        sys.exit(1)

    print("  집계 중 (지역×연도×월 셀 순회)...")
    r = compute_final_scores()
    id_to_name = r["id_to_name"]
    total_occurrence = r["total_occurrence"]

    print(f"  최종 적재 대상: {len(r['final_ids'])}종 × 12개월 = {len(r['final_ids']) * 12}행")

    engine = get_engine()
    rows_written = 0
    rows_deleted = 0
    with engine.begin() as conn:
        # 2중 필터로 제외 대상이 된 재료는 기존에 적재돼 있었더라도 DELETE
        # (peak_months>6 기준 신설로 두부/감자/우유 등 36종이 새로 빠짐 — 3번 참고)
        existing_ids = {
            row.ingredient_id for row in
            conn.execute(text("SELECT DISTINCT ingredient_id FROM seasonal_ingredient"))
        }
        to_delete = existing_ids - r["final_ids"]
        if to_delete:
            result = conn.execute(
                text("DELETE FROM seasonal_ingredient WHERE ingredient_id = ANY(:ids)"),
                {"ids": list(to_delete)},
            )
            rows_deleted = result.rowcount

        for ing_id in sorted(r["final_ids"]):
            cv = r["cv_map"][ing_id]
            peak = r["peak_months_map"][ing_id]
            for month in range(1, 13):
                fs = r["freq_score"][ing_id][month]
                notes = (
                    f"NEIS 30개교(6개시도) 2023-2025 중식 / n={total_occurrence[ing_id]} / "
                    f"방식2 / CV={cv:.3f}(>={CV_THRESHOLD}) / peak {peak}개월(<={MAX_PEAK_MONTHS})"
                )
                conn.execute(UPSERT_SQL, {
                    "ingredient_id": ing_id, "month": month,
                    "freq_score": fs, "is_peak_season": fs >= 0.7,
                    "notes": notes[:200],
                })
                rows_written += 1

    print(f"  기존 행 중 삭제: {rows_deleted}행")
    print(f"  적재 완료: {rows_written}행 upsert")


def parse_args():
    parser = argparse.ArgumentParser(description="seasonal_ingredient 2단계 본적재 스크립트")
    parser.add_argument("phase", choices=[
        "collect", "noise-report", "onechar-audit", "coverage-matrix",
        "unmatched-top50", "remeasure", "compute", "load",
    ])
    parser.add_argument("--refresh-schools", action="store_true",
                         help="collect: 학교 목록 캐시를 무시하고 선정 로직을 재실행")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.phase == "collect":
        cmd_collect(args)
    elif args.phase == "noise-report":
        cmd_noise_report(args)
    elif args.phase == "onechar-audit":
        cmd_onechar_audit(args)
    elif args.phase == "coverage-matrix":
        cmd_coverage_matrix(args)
    elif args.phase == "unmatched-top50":
        cmd_unmatched_top50(args)
    elif args.phase == "remeasure":
        cmd_remeasure(args)
    elif args.phase == "compute":
        cmd_compute(args)
    elif args.phase == "load":
        cmd_load(args)


if __name__ == "__main__":
    main()
