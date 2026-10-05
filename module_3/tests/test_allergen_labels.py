# -*- coding: utf-8 -*-
"""알레르기 판정이 '19종 표시 이름' 기준으로 동작하는지 고정한다.

배경(2026-10-05): constraints 알레르기 행이 0개라 메뉴 allergens 가 늘 비었고, 화면 선택값('난류'·'밀'·
'갑각류')과 DB 재료명('달걀'·'밀가루')이 달라 데이터를 넣어도 절반은 걸리지 않았다.
이제 constraints.description 에 표시 이름을 적재하고(scripts/load_allergen_constraints.py), 메뉴의
allergens 는 표시 이름 집합, ingredient_allergens 는 재료명 → 표시 이름이다.

DB 없이 mock 메뉴로 검증한다.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import alternative_menu as am              # noqa: E402
from csp_solver import MenuItem            # noqa: E402


def item(mid, name, cat, ing_allergens=None, ingredients=()):
    ia = {k: set(v) for k, v in (ing_allergens or {}).items()}
    return MenuItem(mid, name, cat, 100.0,
                    allergens=set().union(*ia.values()) if ia else set(),
                    ingredients=set(ingredients) | set(ia), ingredient_allergens=ia)


def derive(plan, menus, allergens, **kw):
    return am.derive_alternative_menus(
        plan, menus, [am.AllergyGroup(label="g", allergens=set(allergens))], **kw)[0]


# ---------------------------------------------------------------------------
# 표준 이름 정규화
# ---------------------------------------------------------------------------
def test_normalize_aliases_and_codes():
    assert am.normalize_allergens(["갑각류"]) == {"게", "새우"}
    assert am.normalize_allergens(["계란", "소고기", "01", "6"]) == {"난류", "쇠고기", "밀"}
    assert am.normalize_allergens([" 우유 ", ""]) == {"우유"}
    # 모르는 값은 버리지 않는다(조용히 무시되면 그 알레르겐이 빠진다)
    assert am.normalize_allergens(["키위"]) == {"키위"}


def test_labels_cover_19():
    assert len(am.ALLERGEN_LABELS) == 19 == len(set(am.ALLERGEN_LABELS))


# ---------------------------------------------------------------------------
# 교체 대상 판정 — 표시 이름으로, 선언한 것만
# ---------------------------------------------------------------------------
def test_hit_by_label_not_ingredient_name():
    """'달걀' 재료가 든 메뉴가 '난류' 그룹에서 교체 대상이 된다(이전엔 이름이 달라 놓쳤다)."""
    menus = [item(1, "달걀찜", "부찬", {"달걀": {"난류"}}),
             item(2, "시금치나물", "부찬", ingredients={"시금치"})]
    alt = derive({1: {"점심": ["달걀찜"]}}, menus, {"난류"}, ingredient_substitution=False)
    assert [s.alternative for s in alt.substitutions] == ["시금치나물"]
    assert alt.substitutions[0].hit_allergens == {"난류"}


def test_crustacean_alias_hits_crab_and_shrimp():
    menus = [item(1, "새우볶음", "주찬", {"새우": {"새우"}}),
             item(2, "꽃게탕", "국", {"꽃게": {"게"}}),
             item(3, "제육볶음", "주찬", {"돼지고기": {"돼지고기"}}),
             item(4, "무국", "국", ingredients={"무"})]
    alt = derive({1: {"점심": ["새우볶음", "꽃게탕"]}}, menus, {"갑각류"}, ingredient_substitution=False)
    assert {s.original for s in alt.substitutions} == {"새우볶음", "꽃게탕"}


def test_cross_reactive_not_used_for_hit():
    """난류 그룹이라고 닭고기 메뉴를 교체하지는 않는다(교차반응은 후보 필터에만)."""
    menus = [item(1, "닭볶음", "주찬", {"닭고기": {"닭고기"}}),
             item(2, "두부조림", "주찬", {"두부": {"대두"}})]
    alt = derive({1: {"점심": ["닭볶음"]}}, menus, {"난류"})
    assert alt.substitutions == [] and alt.unresolved == []


def test_cross_reactive_filters_candidates():
    """대체 후보에서는 교차반응 묶음까지 뺀다 — 새우 그룹에 게 메뉴는 대체로 오지 않는다."""
    menus = [item(1, "새우볶음", "주찬", {"새우": {"새우"}}),
             item(2, "게살볶음", "주찬", {"게살": {"게"}}),
             item(3, "버섯볶음", "주찬", ingredients={"버섯"})]
    alt = derive({1: {"점심": ["새우볶음"]}}, menus, {"새우"}, ingredient_substitution=False)
    assert [s.alternative for s in alt.substitutions] == ["버섯볶음"]


# ---------------------------------------------------------------------------
# 재료 치환 — 알레르겐 재료 전부를 봐야 한다
# ---------------------------------------------------------------------------
def test_substitution_refuses_when_other_dairy_remains():
    """우유→두유만 바꾸고 버터를 남기면 안 된다. 버터는 치환표에 없으니 메뉴 교체로 간다."""
    menus = [item(1, "크림스프", "국", {"우유": {"우유"}, "버터": {"우유"}}),
             item(2, "미역국", "국", ingredients={"미역"}),
             item(9, "두유라떼", "후식", {"두유": {"대두"}})]
    alt = derive({1: {"점심": ["크림스프"]}}, menus, {"우유"})
    s = alt.substitutions[0]
    assert (s.method, s.alternative) == ("메뉴대체", "미역국")


def test_substitution_skips_substitute_with_group_allergen():
    """우유+대두 그룹이면 우유→두유 치환은 금지(두유=대두) → 다음 후보 멸치."""
    menus = [item(1, "우유죽", "주식", {"우유": {"우유"}}, ingredients={"쌀"}),
             item(9, "두유", "음료", {"두유": {"대두"}})]
    alt = derive({1: {"점심": ["우유죽"]}}, menus, {"우유", "대두"})
    s = alt.substitutions[0]
    assert s.method == "치환" and "우유→멸치" in s.alternative


def test_substituted_dish_allergens_recomputed():
    """치환 접시의 allergens 는 바뀐 재료 기준으로 다시 계산된다(우유 빠지고 두유의 대두가 들어감)."""
    menus = [item(1, "우유죽", "주식", {"우유": {"우유"}}, ingredients={"쌀"}),
             item(9, "두유", "음료", {"두유": {"대두"}})]
    virt = am._try_ingredient_substitution(
        menus[0], am._expand_cross_reactive({"우유"}), allergen_index=am._allergen_index(menus))
    assert virt.ingredients == {"쌀", "두유"}
    assert virt.allergens == {"대두"}


def test_menu_has_unsafe_checks_ingredient_names_too():
    """교차반응 묶음의 19종 밖 재료(사과 — 복숭아 묶음)도 후보 필터에 걸린다."""
    m = item(1, "사과샐러드", "부찬", ingredients={"사과"})
    assert am.menu_has_unsafe(m, am._expand_cross_reactive({"복숭아"}))
    assert not am.menu_has_unsafe(m, am._expand_cross_reactive({"우유"}))


# ---------------------------------------------------------------------------
# 재료 정보가 없는 메뉴 — allergens=∅ 이 '알레르겐 없음'이 아니다
# ---------------------------------------------------------------------------
def unknown(mid, name, cat):
    return MenuItem(mid, name, cat, 100.0, allergen_unknown=True)


def test_unknown_menu_in_common_plan_is_unresolved():
    """공통식의 재료 정보 없는 메뉴는 바꾸지 않고 '재료 정보 없음'으로 영양사 확인에 올린다."""
    menus = [unknown(1, "곤약 콩조림", "부찬"), item(2, "시금치나물", "부찬", ingredients={"시금치"})]
    alt = derive({1: {"점심": ["곤약 콩조림"]}}, menus, {"대두"})
    assert alt.substitutions == []
    [u] = alt.unresolved
    assert (u.original, u.reason) == ("곤약 콩조림", am.UNRESOLVED_UNKNOWN)


def test_unknown_menu_never_picked_as_alternative():
    menus = [item(1, "달걀찜", "부찬", {"달걀": {"난류"}}), unknown(2, "곤약 콩조림", "부찬")]
    alt = derive({1: {"점심": ["달걀찜"]}}, menus, {"난류"}, ingredient_substitution=False)
    assert alt.substitutions == []
    assert alt.unresolved[0].reason == am.UNRESOLVED_NO_SAFE


def test_unknown_menu_untouched_without_groups_allergens():
    """알레르겐이 없는 그룹(빈 선택)이면 표시하지 않는다."""
    alt = derive({1: {"점심": ["곤약 콩조림"]}}, [unknown(1, "곤약 콩조림", "부찬")], set())
    assert alt.unresolved == [] and alt.substitutions == []


def test_hard_h3_bans_unknown_menu():
    """H-3(excluded_allergens)가 켜지면 재료 정보 없는 메뉴도 배제한다 — 알레르겐 메뉴와 함께."""
    import csp_hard_constraints as hc
    from ortools.sat.python import cp_model
    menus = [MenuItem(1, "무국", "국", 200.0, ingredients={"무"}),
             MenuItem(2, "곤약 콩조림", "국", 200.0, allergen_unknown=True),
             item(3, "우유죽", "주식", {"우유": {"우유"}})]
    model = cp_model.CpModel()
    x = {(m, 0, 0): model.NewBoolVar(f"x{m}") for m in range(len(menus))}
    cfg = hc.HardConstraintConfig(enable_energy=False, budget_limit_per_person=None,
                                  excluded_allergens={"우유"})
    hard = hc.add_hard_constraints(model, x, menus, days=1, n_meals=1, config=cfg)
    assert hard.excluded_idx == {1, 2}
