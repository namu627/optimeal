# -*- coding: utf-8 -*-
"""기저질환 대체식(disease_diet) — 질환 기준 교체·알레르기 겹침·값 모름·미확정 질환을 고정한다.

배경(2026-10-06 팀 결정): 고혈압·당뇨·신장질환 입력을 받지만 식단에 반영되지 않았다. A안 — 일반식은 두고
질환 그룹만 기준을 넘는 날의 접시를 낮은 메뉴로 교체, 알레르기도 있으면 질환 먼저 그다음 알레르기.
DB 없이 mock 메뉴로 검증한다.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import disease_diet as dd                  # noqa: E402
from csp_solver import MenuItem            # noqa: E402


def item(mid, name, cat, kcal=100.0, allergens=()):
    return MenuItem(mid, name, cat, kcal, allergens=set(allergens), ingredients={name})


def vals(sodium=None, carbs=None, kcal=100.0, sugar=None, potassium=None):
    return {"sodium": sodium, "carbs": carbs, "kcal": kcal, "sugar": sugar, "potassium": potassium,
            "phosphorus": None}


def group(diseases=(), allergens=()):
    return dd.DietGroup(label="g", allergens=set(allergens), count=3, diseases=set(diseases))


# ---------------------------------------------------------------------------
# 기준·정규화
# ---------------------------------------------------------------------------
def test_limits_and_pending():
    assert [l.key for l in dd.limits_for({"고혈압"})] == ["sodium"]
    assert [l.key for l in dd.limits_for({"당뇨"})] == ["carb_ratio"]
    # 신장질환은 기준 미확정 — 상한 없음, pending 으로 남는다
    assert dd.limits_for({"신장질환"}) == []
    assert dd.pending_diseases(dd.normalize_diseases(["만성콩팥병", "고혈압"])) == ["신장질환"]
    assert dd.normalize_diseases(["당뇨병", " "]) == {"당뇨"}


def test_carb_ratio_is_linear_per_dish():
    lim = dd.CARB_RATIO_DM
    # 밥 300kcal 탄수화물 66g → 4×66 − 0.55×300 = 99 (비율 88% → 초과 기여)
    assert dd.dish_load(lim, vals(carbs=66, kcal=300)) == 4 * 66 - 0.55 * 300
    assert dd.dish_load(lim, vals(carbs=None)) is None


def test_estimate_scales_to_official_kcal_and_needs_coverage():
    row = {"calories": 200, "g": 400, "g_e": 400, "e": 400, "g_k": 400, "k": 1000,
           "g_s": 400, "s": 10, "g_p": 200, "p": 300}
    est = dd.estimate_from_ingredients(row)
    assert est["potassium"] == 500.0          # 재료 합 1000 × (200/400)
    assert est["sugar"] == 5.0
    assert est["phosphorus"] is None          # 재료 g 50%만 값 있음 → 모름
    # 보정비가 범위 밖(0.1)이면 전부 모름
    assert dd.estimate_from_ingredients({**row, "calories": 40})["potassium"] is None


# ---------------------------------------------------------------------------
# 질환 기준 교체
# ---------------------------------------------------------------------------
def test_htn_swaps_highest_contributor_until_under_cap():
    menus = [item(1, "밥", "주식"), item(2, "김치찌개", "국"), item(3, "된장국", "국"),
             item(4, "장조림", "주찬"), item(5, "닭가슴살구이", "주찬")]
    v = {1: vals(sodium=10), 2: vals(sodium=1500), 3: vals(sodium=600),
         4: vals(sodium=900), 5: vals(sodium=300)}
    plan = {1: {"점심": ["밥", "김치찌개", "장조림"]}}
    alt = dd.derive_diet_alternatives(plan, menus, [group({"고혈압"})], v)[0]
    # 2410mg → 김치찌개(가장 큼)를 된장국으로 바꾸면 1510mg 로 충족, 장조림은 그대로
    assert alt["plan"][1]["점심"] == ["밥", "된장국", "장조림"]
    assert [s.original for s in alt["disease_swaps"]] == ["김치찌개"]
    assert alt["day_report"][1]["status"] == "충족"
    assert alt["day_report"][1]["limits"]["sodium"]["total"] == 1510.0
    assert alt["unmet_days"] == [] and alt["kind"] == "기저질환"


def test_unmet_day_keeps_closest_plan_and_flags():
    menus = [item(1, "라면", "주식"), item(2, "짬뽕밥", "주식")]
    v = {1: vals(sodium=2600), 2: vals(sodium=2300)}
    alt = dd.derive_diet_alternatives({1: {"점심": ["라면"]}}, menus, [group({"고혈압"})], v)[0]
    assert alt["plan"][1]["점심"] == ["짬뽕밥"]           # 더 가까운 쪽으로는 바꾼다
    assert alt["day_report"][1]["status"] == "확인 필요"
    assert alt["unmet_days"] == [1]


def test_unknown_value_dish_is_replaced_and_unknown_candidates_unused():
    menus = [item(1, "정체불명", "국"), item(2, "값모름국", "국"), item(3, "미역국", "국")]
    v = {1: vals(sodium=None), 2: vals(sodium=None), 3: vals(sodium=500)}
    alt = dd.derive_diet_alternatives({1: {"점심": ["정체불명"]}}, menus, [group({"고혈압"})], v)[0]
    assert alt["plan"][1]["점심"] == ["미역국"]
    assert alt["disease_swaps"][0].reduced == ["unknown"]
    assert alt["day_report"][1]["status"] == "충족"


def test_kcal_band_respected():
    from csp_hard_constraints import HardConstraintConfig
    menus = [item(1, "짠국", "국", kcal=500), item(2, "싱거운국", "국", kcal=100), item(3, "보통국", "국", kcal=480)]
    v = {1: vals(sodium=2500, kcal=500), 2: vals(sodium=100, kcal=100), 3: vals(sodium=1500, kcal=480)}
    cfg = HardConstraintConfig(target_kcal_per_day=500, kcal_tolerance=0.1, budget_limit_per_person=None)
    alt = dd.derive_diet_alternatives({1: {"점심": ["짠국"]}}, menus, [group({"고혈압"})], v,
                                      hard_config=cfg)[0]
    assert alt["plan"][1]["점심"] == ["보통국"]           # 싱거운국은 열량 밴드(450~550) 밖


def test_diabetes_carb_ratio():
    menus = [item(1, "흰밥", "주식", 300), item(2, "현미밥", "주식", 300), item(3, "제육볶음", "주찬", 300),
             item(4, "떡볶이", "주찬", 300)]
    v = {1: vals(carbs=66, kcal=300), 2: vals(carbs=60, kcal=300),
         3: vals(carbs=10, kcal=300), 4: vals(carbs=60, kcal=300)}
    alt = dd.derive_diet_alternatives({1: {"점심": ["흰밥", "떡볶이"]}}, menus, [group({"당뇨"})], v)[0]
    # 흰밥+떡볶이 = 4×126/600 = 84% → 떡볶이를 제육볶음으로 바꿔 4×76/600 = 50.7%
    assert alt["plan"][1]["점심"] == ["흰밥", "제육볶음"]
    rep = alt["day_report"][1]["limits"]["carb_ratio"]
    assert rep["ok"] and rep["total"] == 50.7


def test_pending_disease_not_applied():
    menus = [item(1, "밥", "주식")]
    alt = dd.derive_diet_alternatives({1: {"점심": ["밥"]}}, menus, [group({"신장질환"})], {1: vals(sodium=10)})[0]
    assert alt["pending_diseases"] == ["신장질환"] and alt["limits"] == [] and alt["day_report"] == {}


# ---------------------------------------------------------------------------
# 알레르기 + 기저질환
# ---------------------------------------------------------------------------
def test_allergy_after_disease_respects_cap():
    menus = [item(1, "밥", "주식"), item(2, "새우볶음", "주찬", allergens={"새우"}),
             item(3, "간장불고기", "주찬"), item(4, "닭구이", "주찬")]
    v = {1: vals(sodium=100), 2: vals(sodium=500), 3: vals(sodium=1950), 4: vals(sodium=400)}
    alt = dd.derive_diet_alternatives({1: {"점심": ["밥", "새우볶음"]}}, menus,
                                      [group({"고혈압"}, {"새우"})], v)[0]
    # 간장불고기는 알레르기는 안전하지만 바꾸면 2000mg 초과 → 닭구이
    assert alt["plan"][1]["점심"] == ["밥", "닭구이"]
    assert alt["kind"] == "알레르기+기저질환"
    assert alt["day_report"][1]["status"] == "충족"


def test_disease_swap_does_not_pick_allergen_menu():
    menus = [item(1, "짠국", "국"), item(2, "새우국", "국", allergens={"새우"}), item(3, "맑은국", "국")]
    v = {1: vals(sodium=2500), 2: vals(sodium=100), 3: vals(sodium=800)}
    alt = dd.derive_diet_alternatives({1: {"점심": ["짠국"]}}, menus, [group({"고혈압"}, {"새우"})], v)[0]
    assert alt["plan"][1]["점심"] == ["맑은국"]


def test_substituted_dish_uses_original_values():
    """재료 치환 접시('메뉴(대체: …)')는 원래 메뉴 값으로 판정한다 — 이름이 달라 '값 모름'이 되면 안 된다."""
    menus = [item(1, "닭꼬치", "주찬")]
    rep = dd.day_report({1: {"점심": ["닭꼬치(대체: 우유→두유)"]}}, menus, [dd.SODIUM_HTN], {1: vals(sodium=500)})
    assert rep[1]["status"] == "충족" and rep[1]["limits"]["sodium"]["total"] == 500.0
