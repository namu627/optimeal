#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B6 Phase 0 — 메뉴 어울림 근거표 구축 (NEIS 실 급식 동시출현 빈도)

목적:
    실제 급식 식단에서 **무엇이 무엇과 함께 나오는가**를 세어, 어울림 점수의 근거표를
    만든다. 프로젝트 핵심 제약상 **LLM 확률 출력 금지**이므로 점수는 관측 빈도에서만
    나올 수 있다(대안은 영양사 검수 수집 표).

입력:
    data/external/neis_meals_raw/*.json — NEIS 급식식단정보 수집분
    (2026-08-10 수집: 15,250끼니 · 36개교 · 3년치. 재수집은 explore_neis_meal.py + NEIS_KEY)

출력:
    data/processed/menu_affinity.csv — 유형쌍별 관측수 · lift · 정수 점수
    코드가 아니라 **데이터**로 두는 이유: 영양사 검수 결과로 덮어쓸 수 있어야 한다.

측정 지표:
    lift = P(B|A) / P(B).  1보다 크면 함께 나오는 경향, 작으면 피하는 경향.
    점수 = round(log(lift) * SCORE_SCALE) — CP-SAT 목적함수가 정수-선형이어야 하므로.
    **관측수(n_both·n_a)를 반드시 함께 저장한다** — 0건(Hard 후보)과 희소(신뢰 낮음)를
    구분해야 하고, 표본이 작은 축(빵 n=59)에 강한 점수를 주면 안 된다.

실행:
    python scripts/build_menu_affinity.py            # 표 생성 + 요약 출력
    python scripts/build_menu_affinity.py --report   # 표를 쓰지 않고 요약만
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "module_3" / "src"))

import menu_taxonomy as mt  # noqa: E402
import soft_constraints_diversity as scd  # noqa: E402

RAW_DIR = REPO_ROOT / "data" / "external" / "neis_meals_raw"
OUT_PATH = REPO_ROOT / "data" / "processed" / "menu_affinity.csv"

# 점수 스케일. log(lift) 를 정수화한다. lift 2배 ≈ +69, 1/2배 ≈ -69.
SCORE_SCALE = 100
# 이 관측수 미만인 조합은 점수를 신뢰하지 않는다(리포트에는 남기되 confidence='low').
MIN_OBS = 30

# ---------------------------------------------------------------------------
# 요리명 파싱 — NEIS DDISH_NM 은 "쇠고기무국~  (5.6.16.)" 형태
# ---------------------------------------------------------------------------
_ALLERGEN = re.compile(r"\(\s*[\d.\s]*\)")     # 알레르기 번호 괄호
_NOISE = re.compile(r"[~*@#()\[\]]|\d")         # 표기 기호·숫자
_LEAD = re.compile(r"^[ㄱ-ㅎㅏ-ㅣ\s·.-]+")      # 'ㅁ배추김치' 같은 선두 잡문자


def parse_dishes(ddish: str) -> list[str]:
    """DDISH_NM 한 칸을 요리명 목록으로 자른다."""
    out = []
    for raw in re.split(r"<br\s*/?>", ddish or ""):
        name = _LEAD.sub("", _NOISE.sub("", _ALLERGEN.sub("", raw))).strip()
        if name:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# 요리 분류 — CSP 와 같은 축으로. 단 NEIS 에는 급식 부산물이 섞여 있다.
# ---------------------------------------------------------------------------
# ⚠ `classify_side_kind` 는 **총 함수**라 어떤 이름이든 주찬/부찬/김치를 돌려준다.
#   CSP 에서는 DB `menu_category` 가 이미 반찬 계열일 때만 부르므로 문제없지만,
#   NEIS 원문에 그대로 적용하면 우유·바나나·공통양념이 전부 '부찬'이 된다
#   (실측: 우유 2,051회·바나나 293회·공통양념류 1,075회). 그래서 먼저 걸러낸다.
DESSERT_DRINK = (
    "우유", "요구르트", "요플레", "야쿠르트", "두유", "주스", "쥬스", "식혜", "수정과",
    "미숫가루", "스무디", "에이드", "차)", "보리차", "옥수수차", "매실차",
    "바나나", "귤", "한라봉", "천혜향", "사과", "배즙", "수박", "참외", "오렌지",
    "파인애플", "딸기", "포도", "자두", "복숭아", "키위", "멜론", "망고", "블루베리",
    "방울토마토", "토마토)", "아이스크림", "젤리", "푸딩", "케이크", "쿠키", "카스테라",
    "약과", "떡)", "찐빵", "호두과자", "머핀", "마카롱", "초코", "생과일",
)
NON_DISH = ("양념", "소스)", "공통", "기본", "선택", "추가", "없음", "자율", "행사")


def dish_category(name: str) -> str:
    """요리명 하나를 CSP 카테고리로 분류한다.

    `menu_taxonomy.resolve_menu_category` 와 **같은 우선순위**를 쓰되, NEIS 에만 있는
    후식·음료·비요리를 먼저 걸러낸다.

    Args:
        name: 정제된 요리명.

    Returns:
        주식 / 국 / 주찬 / 부찬 / 김치 / 후식음료 / 비요리 중 하나.
    """
    if any(k in name for k in NON_DISH):
        return "비요리"
    if mt.is_staple(name):
        return "주식"
    if mt.detect_soup_kind(name) is not None:
        return "국"
    if any(k in name for k in DESSERT_DRINK):
        return "후식음료"
    return mt.classify_side_kind(name)


def meal_axes(dishes: list[str]) -> dict:
    """끼니 하나에서 어울림 축 값을 뽑는다."""
    cats = [(d, dish_category(d)) for d in dishes]
    staples = [d for d, c in cats if c == "주식"]
    soups = [d for d, c in cats if c == "국"]
    sides = [(d, c) for d, c in cats if c in ("주찬", "부찬")]
    return {
        "staple_kind": mt.classify_staple_kind(staples[0]) if staples else None,
        "soup_kind": (mt.detect_soup_kind(soups[0]) or "soup") if soups else "없음",
        "has_kimchi": any(c == "김치" for _, c in cats),
        "side_methods": [scd.classify_cooking_methods([_Fake(d)]).get(0) for d, _ in sides],
        "side_tastes": [taste_of(d) for d, _ in sides],
        "n_dish": len(cats),
    }


class _Fake:
    """`classify_cooking_methods` 가 기대하는 최소 인터페이스(name 만 필요)."""

    def __init__(self, name):
        self.name = name


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def load_meals() -> list[dict]:
    """수집된 NEIS 끼니를 축 값으로 변환해 돌려준다."""
    meals = []
    for path in sorted(glob.glob(str(RAW_DIR / "*.json"))):
        for m in json.load(open(path, encoding="utf-8"))["meals"]:
            dishes = parse_dishes(m.get("DDISH_NM", ""))
            if len(dishes) >= 3:                     # 결식·행사일 제외
                meals.append(meal_axes(dishes))
    return meals


def lift_rows(axis: str, pairs: Counter, count_a: Counter, count_b: Counter,
              n_meals: int) -> list[dict]:
    """(A, B) 동시출현에서 lift 와 정수 점수를 만든다."""
    rows = []
    for (a, b), n_both in sorted(pairs.items(), key=lambda kv: -kv[1]):
        n_a, n_b = count_a[a], count_b[b]
        if not n_a or not n_b:
            continue
        p_b_given_a = n_both / n_a
        p_b = n_b / n_meals
        lift = (p_b_given_a / p_b) if p_b else 0.0
        # lift=0(관측 0건)은 log 불가 → 점수 하한으로 표시하고 Hard 후보로 남긴다.
        score = (round(math.log(lift) * SCORE_SCALE) if lift > 0 else -3 * SCORE_SCALE)
        rows.append({
            "axis": axis, "value_a": a, "value_b": b,
            "n_a": n_a, "n_b": n_b, "n_both": n_both, "n_meals": n_meals,
            "p_b_given_a": round(p_b_given_a, 4), "p_b": round(p_b, 4),
            "lift": round(lift, 3), "score": score,
            "confidence": "low" if n_a < MIN_OBS or n_both < MIN_OBS else "ok",
        })
    return rows


def build_staple_soup(meals) -> list[dict]:
    """⑤ 주식유형 × 국유형."""
    pairs, ca, cb = Counter(), Counter(), Counter()
    n = 0
    for m in meals:
        if not m["staple_kind"] or m["staple_kind"] not in mt.STAPLE_KINDS:
            continue
        n += 1
        ca[m["staple_kind"]] += 1
        cb[m["soup_kind"]] += 1
        pairs[(m["staple_kind"], m["soup_kind"])] += 1
    return lift_rows("주식유형×국유형", pairs, ca, cb, n)


def build_staple_kimchi(meals) -> list[dict]:
    """① 주식유형 × 김치 편성 — H-4d(김치 조건화)의 근거."""
    pairs, ca, cb = Counter(), Counter(), Counter()
    n = 0
    for m in meals:
        if not m["staple_kind"] or m["staple_kind"] not in mt.STAPLE_KINDS:
            continue
        n += 1
        key = "김치있음" if m["has_kimchi"] else "김치없음"
        ca[m["staple_kind"]] += 1
        cb[key] += 1
        pairs[(m["staple_kind"], key)] += 1
    return lift_rows("주식유형×김치", pairs, ca, cb, n)


def build_method_pairs(meals) -> list[dict]:
    """②③ 한 끼니 안 반찬들의 조리법 조합(같은 조리법 중복 포함).

    ⚠ 여기서는 **독립 가정 기준선(lift)을 쓰면 안 된다.** 한 끼니의 반찬은 3~4개뿐인데
    조리법은 8종이라 슬롯을 두고 경쟁한다. 그래서 어떤 조합이든 동시출현이 P(a)·P(b)
    보다 낮게 나오고(초기 산출에서 35개 조합 전부 lift ≤ 0.97), 이는 '서로 피한다'가
    아니라 자리 부족이라는 **구조적 인공물**이다.

    대신 **끼니당 반찬 수를 보존하는 다항 추출 기준선**과 비교한다. 반찬 n개를 전역
    조리법 분포에서 독립 추출했을 때의 기대 동시출현 확률을 끼니마다 계산해 합한다
    (해석적 계산이라 난수 불필요 → 재현성 확보).

        a≠b : P(둘 다 등장) = 1 - (1-p_a)^n - (1-p_b)^n + (1-p_a-p_b)^n
        a=b : P(2개 이상)   = 1 - (1-p)^n - n·p·(1-p)^(n-1)
    """
    method_freq: Counter = Counter()
    sized = []
    for m in meals:
        methods = [x for x in m["side_methods"] if x]
        if len(methods) >= 2:
            sized.append(methods)
            method_freq.update(methods)
    total = sum(method_freq.values())
    p = {k: c / total for k, c in method_freq.items()}
    labels = sorted(p)

    observed: Counter = Counter()
    expected: dict = defaultdict(float)
    for methods in sized:
        n = len(methods)
        seen = Counter(methods)
        for i, a in enumerate(labels):
            for b in labels[i:]:
                if a == b:
                    if seen[a] >= 2:
                        observed[(a, b)] += 1
                    pa = p[a]
                    expected[(a, b)] += 1 - (1 - pa) ** n - n * pa * (1 - pa) ** (n - 1)
                else:
                    if seen[a] and seen[b]:
                        observed[(a, b)] += 1
                    expected[(a, b)] += (1 - (1 - p[a]) ** n - (1 - p[b]) ** n
                                         + max(0.0, 1 - p[a] - p[b]) ** n)
    return _null_rows("조리법×조리법", observed, expected, len(sized))


# ---------------------------------------------------------------------------
# ④ 맛 계열 — DB 스키마가 없어 메뉴명 키워드로만 근사한다(근거 최약)
# ---------------------------------------------------------------------------
# ⚠ 조리법 어휘(조림·찜·무침)와 겹치지 않도록 **양념·재료 어휘만** 쓴다. 겹치면 맛 축이
#   조리법 축을 다시 재는 셈이 되어 신호가 있어도 해석할 수 없다.
#   ⚠ 실제로 겹쳐 있었다 — 강정·탕수(튀김) / 초무침·냉채·생채(무침) / 장조림(조림).
#   테스트 `test_taste_vocabulary_disjoint_from_cooking_methods` 가 잡아냈고 제거했다.
#   그 결과 커버리지가 더 떨어진다는 사실 자체가 ④의 판정 근거다.
TASTE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("매콤", (
        "고추장", "고춧가루", "매운", "매콤", "얼큰", "짬뽕", "떡볶",
        "제육", "불닭", "청양", "칠리",
    )),
    ("달콤", (
        "맛탕", "데리야", "꿀", "시럽", "카라멜", "허니", "메이플",
        "설탕", "마요", "크림",
    )),
    ("새콤", ("피클", "겨자", "유자", "레몬", "새콤", "발사믹", "식초", "오리엔탈")),
    ("간장", ("간장", "불고기", "조선간장", "진간장")),
    ("구수", ("된장", "청국장", "들깨", "미소", "강된장")),
)


def taste_of(name: str) -> str | None:
    """메뉴명에서 맛 계열을 근사한다. 판정 불가면 None(집계 제외)."""
    for taste, keys in TASTE_KEYWORDS:
        if any(k in name for k in keys):
            return taste
    return None


def build_taste_pairs(meals) -> list[dict]:
    """④ 한 끼니 안 반찬들의 맛 계열 조합. 조리법 축과 같은 다항 기준선을 쓴다."""
    freq: Counter = Counter()
    sized = []
    for m in meals:
        tastes = [t for t in m["side_tastes"] if t]
        if len(tastes) >= 2:
            sized.append(tastes)
            freq.update(tastes)
    if not sized:
        return []
    total = sum(freq.values())
    p = {k: c / total for k, c in freq.items()}
    labels = sorted(p)
    observed: Counter = Counter()
    expected: dict = defaultdict(float)
    for tastes in sized:
        n = len(tastes)
        seen = Counter(tastes)
        for i, a in enumerate(labels):
            for b in labels[i:]:
                if a == b:
                    if seen[a] >= 2:
                        observed[(a, b)] += 1
                    expected[(a, b)] += (1 - (1 - p[a]) ** n
                                         - n * p[a] * (1 - p[a]) ** (n - 1))
                else:
                    if seen[a] and seen[b]:
                        observed[(a, b)] += 1
                    expected[(a, b)] += (1 - (1 - p[a]) ** n - (1 - p[b]) ** n
                                         + max(0.0, 1 - p[a] - p[b]) ** n)
    return _null_rows("맛계열×맛계열", observed, expected, len(sized))


def _null_rows(axis: str, observed: Counter, expected: dict, n_meals: int) -> list[dict]:
    """다항 기준선 대비 관측비(=lift)와 정수 점수를 만든다."""
    rows = []
    for key, exp in sorted(expected.items(), key=lambda kv: -kv[1]):
        obs = observed.get(key, 0)
        lift = (obs / exp) if exp > 0 else 0.0
        score = (round(math.log(lift) * SCORE_SCALE) if lift > 0 else -3 * SCORE_SCALE)
        rows.append({
            "axis": axis, "value_a": key[0], "value_b": key[1],
            "n_a": round(exp), "n_b": n_meals, "n_both": obs, "n_meals": n_meals,
            "p_b_given_a": round(obs / n_meals, 4) if n_meals else 0.0,
            "p_b": round(exp / n_meals, 4) if n_meals else 0.0,
            "lift": round(lift, 3), "score": score,
            "confidence": "low" if exp < MIN_OBS or obs < MIN_OBS else "ok",
        })
    return rows


def summarize(rows: list[dict]) -> None:
    """축별 요약을 사람이 읽을 형태로 출력한다."""
    by_axis = defaultdict(list)
    for r in rows:
        by_axis[r["axis"]].append(r)
    for axis, rs in by_axis.items():
        lifts = [r["lift"] for r in rs if r["confidence"] == "ok"]
        span = f"{min(lifts):.2f}~{max(lifts):.2f}" if lifts else "—"
        print(f"\n[{axis}] 조합 {len(rs)}개 (신뢰 ok {len(lifts)}개) · lift 범위 {span}")
        for r in sorted(rs, key=lambda r: r["lift"])[:4]:
            print(f"   기피 {r["value_a"]:8s}+{r['value_b']:8s} "
                  f"lift {r['lift']:5.2f} 점수 {r['score']:+5d} "
                  f"(n={r['n_both']:,}/{r['n_a']:,}, {r['confidence']})")
        for r in sorted(rs, key=lambda r: -r["lift"])[:3]:
            print(f"   선호 {r['value_a']:8s}+{r['value_b']:8s} "
                  f"lift {r['lift']:5.2f} 점수 {r['score']:+5d} "
                  f"(n={r['n_both']:,}/{r['n_a']:,}, {r['confidence']})")


def main() -> int:
    ap = argparse.ArgumentParser(description="B6 어울림 근거표 구축 (NEIS 동시출현)")
    ap.add_argument("--report", action="store_true", help="CSV 를 쓰지 않고 요약만 출력")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    meals = load_meals()
    print(f"[입력] 끼니 {len(meals):,}건 · 원본 {len(glob.glob(str(RAW_DIR / '*.json')))}파일")
    covered = sum(1 for m in meals if m["staple_kind"] in mt.STAPLE_KINDS)
    print(f"[커버리지] 주식 식별 {covered:,}건 ({covered / len(meals):.1%}) — "
          f"나머지는 주식 없는 끼니(간식·특식)로 축 집계에서 제외")

    rows = (build_staple_soup(meals) + build_staple_kimchi(meals)
            + build_method_pairs(meals) + build_taste_pairs(meals))
    summarize(rows)

    if not args.report:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\n[완료] {out.relative_to(REPO_ROOT)} — {len(rows)}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
