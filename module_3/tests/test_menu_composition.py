# -*- coding: utf-8 -*-
"""반상 끼니 구성(주식1·국1·주찬1·부찬2이상·김치1)과 식단표 표기 순서 검증.

배경(2026-08-12 영양사 지적):
  · 식단표는 **주식 → 국 → 주찬 → 부찬 → 김치** 순으로 작성되어야 한다.
  · '반찬' 한 덩어리가 아니라 주찬/부찬/김치로 나뉘어야 하고, 부찬은 2개 이상이다.

`test_menu_taxonomy.py` 가 주식·국 분류를 고정하듯 여기서는 반찬 계열 분류
(`classify_side_kind`)와 그 분류가 실제 편성·출력에 반영되는지를 본다.
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import menu_affinity as ma                   # noqa: E402
import menu_taxonomy as mt                   # noqa: E402
from csp_solver import (DEFAULT_COMPOSITION, MealPlanRequest, MenuItem,  # noqa: E402
                        build_and_solve)

MEALS = ("점심",)


# ---------------------------------------------------------------------------
# 분류기 — 김치 / 주찬 / 부찬
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["나박김치", "배깍두기", "통배추 겉절이",
                                  "매실동치미", "논우렁순무섞박지", "저염보쌈김치"])
def test_kimchi_is_kimchi(name):
    """김치·겉절이·깍두기·동치미류는 김치 슬롯이다."""
    assert mt.classify_side_kind(name) == mt.KIMCHI


@pytest.mark.parametrize("name", ["김치찌개", "김치전", "돈육김치볶음", "김치볶음밥",
                                  "두부김치찌개", "김치잡채"])
def test_kimchi_cooked_dish_is_not_kimchi(name):
    """'김치를 쓴 요리'는 김치가 아니다 — 김치 뒤에 조리 접미사가 붙는다.

    이 구분이 없으면 김치 슬롯에 김치찌개가 들어가 국·김치가 겹친다.
    """
    assert mt.classify_side_kind(name) != mt.KIMCHI


@pytest.mark.parametrize("name", ["통삼겹맥적구이", "안심스테이크", "조기까스",
                                  "고등어구이", "돼지갈비 볶음", "닭강정",
                                  "생선까스&타르타르소스", "장어찜"])
def test_protein_main_dishes_are_main_side(name):
    """단백질 주요리는 주찬이다(곁들임 소스가 붙어 있어도)."""
    assert mt.classify_side_kind(name) == mt.MAIN_SIDE


@pytest.mark.parametrize("name", ["취나물들깨무침", "연근초무침", "단감피클",
                                  "사과장아찌", "오징어젓무침", "감태샐러드말이와 버섯말이깐풍",
                                  "야채볶음", "삼색나물"])
def test_vegetable_and_muchim_are_sub_side(name):
    """나물·무침·피클류는 단백질 재료어가 있어도 부찬이다(접미사 우선)."""
    assert mt.classify_side_kind(name) == mt.SUB_SIDE


def test_side_kind_is_total():
    """어떤 이름이 와도 세 값 중 하나를 돌려준다(호출부 분기 누락 방지)."""
    for name in ("", "봄날의 연못", "룰룰랄라", "아삭"):
        assert mt.classify_side_kind(name) in mt.SIDE_KINDS


# ---------------------------------------------------------------------------
# 표기 순서
# ---------------------------------------------------------------------------
def test_category_order_is_nutritionist_spec():
    """주식 → 국 → 주찬 → 부찬 → 김치 순위가 유지된다."""
    order = [mt.category_sort_key(c) for c in ("주식", "국", "주찬", "부찬", "김치")]
    assert order == sorted(order) and len(set(order)) == 5


def test_unknown_category_sorts_last():
    """목록에 없는 카테고리는 맨 뒤로 — 순서 계산이 예외로 죽지 않아야 한다."""
    assert mt.category_sort_key("정체불명") > mt.category_sort_key("김치")


# ---------------------------------------------------------------------------
# 편성 — 구성 개수와 출력 순서
# ---------------------------------------------------------------------------
def pool():
    """반상 구성을 채울 수 있는 소형 후보 풀(실제 음식명)."""
    sub = ["시금치나물", "콩나물무침", "도라지생채",
           "연근초무침", "브로콜리무침", "단감피클"]
    spec = (("주식", ["잡곡밥", "흑미밥", "차조밥"], 300.0),
            ("국", ["황태미역국", "배추된장국", "무맑은국"], 90.0),
            ("주찬", ["안심스테이크", "고등어구이", "닭강정"], 150.0),
            ("부찬", sub, 40.0),
            ("김치", ["배추김치", "깍두기", "나박김치"], 12.0))
    out, mid = [], 1
    for cat, names, kcal in spec:
        for i, name in enumerate(names):
            out.append(MenuItem(mid, name, cat, kcal + i))
            mid += 1
    return out


def solve(menus, *, composition=None, days=1):
    """관심사 외 제약(칼로리·예산·중복창·어울림)은 꺼서 구성만 본다.

    어울림의 조리법 상한·다양성(2026-08-26)은 표 없이도 켜지고 **접시 수에
    비례**하는 감점이라, 켜 두면 "상한을 풀면 부찬이 더 담긴다"는 음성 대조
    픽스처가 무력화된다(중복 조리법 접시가 감점이라 2개에서 멈춘다).
    구성 상한을 보는 테스트이므로 여기서는 끈다.
    """
    return build_and_solve(menus, MealPlanRequest(
        days=days, meals=MEALS, hard=None, solver_time_limit=30.0,
        affinity_weights=ma.AffinityWeights(w_method_over_limit=0, w_method_variety=0),
        **({"composition": composition} if composition else {})))


def cats_of(res, menus, day=1, meal="점심"):
    """편성된 접시의 카테고리 목록(편성 순서 그대로)."""
    by_name = {m.name: m.category for m in menus}
    return [by_name[n] for n in res.plan[day][meal]]


def test_default_composition_matches_spec():
    """기본 구성이 영양사 확정값과 같다(문서·코드 표류 방지)."""
    assert DEFAULT_COMPOSITION == {"주식": 1, "국": 1, "주찬": 1,
                                   "부찬": (2, 3), "김치": 1}


def test_meal_has_full_banssang_composition():
    """끼니마다 주식1·국1·주찬1·부찬2~3·김치1 이 편성된다."""
    menus = pool()
    res = solve(menus)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    cats = cats_of(res, menus)
    assert cats.count("주식") == 1
    assert cats.count("국") == 1
    assert cats.count("주찬") == 1
    assert 2 <= cats.count("부찬") <= 3
    assert cats.count("김치") == 1


def test_sub_side_upper_bound_is_enforced():
    """부찬 상한 3이 실제로 걸린다 — 상한을 풀면 4개 이상이 나올 수 있다(음성 대조).

    부찬 후보를 넉넉히 두고 열량 제약을 걸지 않았으므로, 상한이 없으면 목적함수가
    부찬을 더 담는다. 상한을 건 쪽에서만 3개 이하가 보장되어야 한다.
    """
    menus = pool()
    free = solve(menus, composition={"주식": 1, "국": 1, "주찬": 1,
                                     "부찬": (2, None), "김치": 1})
    capped = solve(menus)
    assert cats_of(free, menus).count("부찬") >= 4, "음성 대조 실패 — 픽스처가 상한을 안 건드림"
    assert cats_of(capped, menus).count("부찬") <= 3


def test_dishes_are_ordered_by_category():
    """편성 결과가 주식 → 국 → 주찬 → 부찬 → 김치 순으로 나온다."""
    menus = pool()
    res = solve(menus, days=3)
    for day, meals in res.plan.items():
        for meal, picks in meals.items():
            keys = [mt.category_sort_key(
                next(m.category for m in menus if m.name == n)) for n in picks]
            assert keys == sorted(keys), f"{day}일 {meal} 순서 어긋남: {picks}"


def test_without_kimchi_slot_no_kimchi_is_served():
    """음성 대조 — 구성에서 김치를 빼면 실제로 김치 없는 끼니가 나온다.

    이게 실패하면 위 양성 테스트가 '어차피 김치가 뽑히는 픽스처' 위에서 통과하는 셈이다.
    """
    menus = pool()
    res = solve(menus, composition={"주식": 1, "국": 1, "주찬": 1, "부찬": 2})
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert "김치" not in cats_of(res, menus)


def test_sub_side_minimum_is_a_floor_not_exact():
    """부찬은 '정확히 2개'가 아니라 '2개 이상'이다 — 상한을 걸면 정확히 그 수가 된다."""
    menus = pool()
    res = solve(menus, composition={"주식": 1, "국": 1, "주찬": 1,
                                    "부찬": (2, 2), "김치": 1})
    assert cats_of(res, menus).count("부찬") == 2


def test_infeasible_when_kimchi_pool_empty():
    """김치 후보가 없으면 정직하게 INFEASIBLE — 조용히 5접시로 내지 않는다."""
    menus = [m for m in pool() if m.category != "김치"]
    res = solve(menus)
    assert res.status == "INFEASIBLE"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
