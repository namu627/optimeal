# -*- coding: utf-8 -*-
"""B6 Phase 0 — 어울림 근거표 구축 로직 검증 (`scripts/build_menu_affinity.py`).

여기서 고정하는 것은 Phase 0 에서 **실제로 틀렸던 두 지점**이다.
  (1) `classify_side_kind` 는 총 함수라 우유·바나나·공통양념까지 부찬으로 돌려준다.
      NEIS 원문에 그대로 쓰면 반찬 축이 오염된다(우유 2,051회·공통양념류 1,075회).
  (2) 끼니당 반찬은 3~4개인데 조리법은 8종이라, 독립 가정 기준선을 쓰면 **모든 조합**이
      lift<1 로 나온다(첫 산출에서 35/35). 이는 기피가 아니라 자리 부족이라는 인공물이다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))

import build_menu_affinity as B  # noqa: E402


# ---------------------------------------------------------------------------
# 요리명 파싱 — NEIS 표기 잡음
# ---------------------------------------------------------------------------
def test_parse_strips_allergen_and_noise():
    """알레르기 번호 괄호와 물결 기호를 떼어낸다."""
    got = B.parse_dishes("유기농쌀밥  <br/>쇠고기무국~  (5.6.16.)<br/>보쌈김치~  (9.13.)")
    assert got == ["유기농쌀밥", "쇠고기무국", "보쌈김치"]


def test_parse_strips_leading_junk():
    """'ㅁ배추김치' 같은 선두 잡문자를 떼어낸다(실데이터 303회 등장)."""
    assert B.parse_dishes("ㅁ배추김치") == ["배추김치"]


def test_parse_handles_empty():
    assert B.parse_dishes("") == []
    assert B.parse_dishes(None) == []


# ---------------------------------------------------------------------------
# (1) 총 함수 함정 — 후식·음료·비요리가 반찬으로 새면 안 된다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["우유", "요구르트", "바나나", "귤", "수박", "파인애플"])
def test_dessert_drink_not_counted_as_side(name):
    """이걸 놓치면 반찬 축이 오염된다 — 우유만 2,051회 등장한다."""
    assert B.dish_category(name) == "후식음료"


@pytest.mark.parametrize("name", ["공통양념", "양념류", "기본양념류", "공통양념-"])
def test_non_dish_excluded(name):
    """요리가 아닌 행(양념 표기)은 집계에서 빠진다."""
    assert B.dish_category(name) == "비요리"


def test_dish_category_keeps_csp_precedence():
    """주식 → 국 → 반찬 순위는 `resolve_menu_category` 와 같아야 한다."""
    assert B.dish_category("혼합잡곡밥") == "주식"
    assert B.dish_category("쇠고기무국") == "국"
    assert B.dish_category("배추김치") == "김치"
    assert B.dish_category("콩나물무침") == "부찬"


def test_porridge_is_staple_not_dessert():
    """단호박죽은 주식이다 — 후식 어휘와 겹쳐 잘못 걸러지면 안 된다."""
    assert B.dish_category("단호박죽") == "주식"


# ---------------------------------------------------------------------------
# (2) 기준선 — 자리 부족 인공물을 걸러내는가
# ---------------------------------------------------------------------------
def _meal(methods, tastes=None):
    return {"staple_kind": "rice", "soup_kind": "soup", "has_kimchi": True,
            "side_methods": methods, "side_tastes": tastes or [None] * len(methods),
            "n_dish": len(methods) + 2}


def test_multinomial_baseline_is_neutral_on_random_meals():
    """조리법이 무작위로 섞인 끼니에서는 lift 가 1 근처여야 한다.

    독립 가정 기준선을 쓰면 여기서도 전부 1 미만이 나온다(인공물). 다항 기준선은
    자리 수를 보존하므로 중립이 나온다 — 이게 두 기준선의 차이다.
    """
    labels = ["볶음", "무침", "구이", "튀김"]
    meals = [_meal([labels[(i + k) % 4] for k in range(3)]) for i in range(400)]
    rows = B.build_method_pairs(meals)
    cross = [r for r in rows if r["value_a"] != r["value_b"] and r["n_both"] > 0]
    assert cross, "교차 조합이 하나도 안 잡혔다"
    assert all(0.5 < r["lift"] < 2.0 for r in cross), \
        f"무작위 끼니인데 치우침: {[(r['value_a'], r['value_b'], r['lift']) for r in cross]}"


def test_duplication_is_detected_when_present():
    """같은 조리법 2개 이상이 실제로 몰려 있으면 lift 가 1보다 크게 나온다."""
    meals = [_meal(["튀김", "튀김", "무침"]) for _ in range(300)]
    rows = B.build_method_pairs(meals)
    dup = next(r for r in rows if r["value_a"] == r["value_b"] == "튀김")
    assert dup["lift"] > 1.0, f"중복이 명백한데 lift {dup['lift']}"


def test_duplication_absent_scores_negative():
    """음성 대조 — 중복이 전혀 없으면 같은 조리법 쌍의 lift 는 1보다 작다."""
    labels = ["볶음", "무침", "구이"]
    meals = [_meal(labels) for _ in range(300)]
    rows = B.build_method_pairs(meals)
    dups = [r for r in rows if r["value_a"] == r["value_b"]]
    assert dups and all(r["n_both"] == 0 for r in dups)
    assert all(r["lift"] < 1.0 for r in dups)


def test_score_is_integer_for_cpsat():
    """CP-SAT 목적함수는 정수-선형이어야 한다."""
    rows = B.build_method_pairs([_meal(["볶음", "무침", "구이"]) for _ in range(100)])
    assert all(isinstance(r["score"], int) for r in rows)


# ---------------------------------------------------------------------------
# 맛 계열 — 조리법 축을 다시 재고 있지 않은가
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("돈육고추장불고기", "매콤"), ("돼지고기데리야끼", "달콤"),
    ("단감피클", "새콤"), ("돈육간장구이", "간장"), ("시금치된장무침", "구수"),
])
def test_taste_keywords(name, expected):
    assert B.taste_of(name) == expected


def test_taste_vocabulary_disjoint_from_cooking_methods():
    """맛 어휘가 조리법 어휘와 겹치면 맛 축이 조리법 축을 다시 재는 셈이 된다."""
    import soft_constraints_diversity as scd
    method_words = {w for _, ws in scd.COOKING_METHOD_KEYWORDS for w in ws}
    taste_words = {w for _, ws in B.TASTE_KEYWORDS for w in ws}
    assert not (method_words & taste_words), f"겹침: {method_words & taste_words}"


def test_taste_returns_none_when_unknown():
    """판정 불가는 None — 집계에서 빠져야 한다(억지 분류 금지)."""
    assert B.taste_of("배추김치") is None
    assert B.taste_of("삼색나물") is None
