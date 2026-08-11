# -*- coding: utf-8 -*-
"""H-4b 주식 자격 · H-4c 메뉴 궁합 제약의 편성 동작 검증.

`test_menu_taxonomy.py` 가 분류기를 고정한다면, 여기서는 그 분류가 실제 CSP 편성에
반영되는지를 본다. DB 없이 mock 메뉴로 검증한다(solver 로직 검증용 픽스처).

주의: 이 제약들은 **실제 음식명**을 전제로 하므로 픽스처도 실제 음식명을 쓴다.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import csp_hard_constraints as hc          # noqa: E402
from csp_solver import MealPlanRequest, MenuItem, build_and_solve  # noqa: E402

MEALS = ("점심",)


def pool(with_rice=True):
    """실제 음식명을 쓴 소형 후보 풀.

    주식은 밥류·면류·스프(비주식)를 섞고, 국은 stew(부대찌개)와 soup(미역국)을 둔다.
    반찬은 끼니당 2개가 필요하므로 넉넉히 준다.
    """
    menus, mid = [], 1
    staples = ([("잡곡밥", 300.0), ("파인애플볶음밥", 320.0)] if with_rice else []) + [
        ("함초 냉이 국수", 310.0), ("블랙빈 곤약국수", 305.0),
        ("포니언 스프", 300.0), ("치킨완자스프", 315.0),   # 비주식(스프)
    ]
    for name, kcal in staples:
        menus.append(MenuItem(mid, name, "주식", kcal)); mid += 1
    for name, kcal in [("삼계부대찌개", 100.0), ("맑은부대찌개", 105.0),
                       ("황태미역국", 95.0), ("배추된장국", 98.0)]:
        menus.append(MenuItem(mid, name, "국", kcal)); mid += 1
    for i in range(8):
        menus.append(MenuItem(mid, f"나물무침{i}", "반찬", 50.0 + i * 10)); mid += 1
    return menus


def solve(menus, *, staple_main=False, pairing=False, days=1, kcal=500.0, tol=0.5):
    """관심사 외 제약(예산·나트륨)은 끄고 격리해서 푼다."""
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=kcal, kcal_tolerance=tol,
        budget_limit_per_person=None, enable_meal_ratio=False,
        menu_repeat_window_days=0,
        enable_staple_main=staple_main, enable_menu_pairing=pairing)
    return build_and_solve(menus, MealPlanRequest(
        days=days, meals=MEALS, hard=cfg, solver_time_limit=30.0))


def picked(res, day=1):
    """해당 일자 점심에 편성된 메뉴명 목록."""
    return res.plan[day]["점심"]


# ---------------------------------------------------------------------------
# H-4b 주식 자격
# ---------------------------------------------------------------------------
def test_soup_can_fill_main_slot_without_constraint():
    """음성 대조 — 제약이 없으면 스프가 주식 슬롯에 들어갈 수 있다.

    이게 실패하면 아래 양성 테스트가 '원래 안 뽑히는 픽스처' 위에서 통과하는 셈이다.
    """
    menus = [m for m in pool() if m.category != "주식"]
    menus += [MenuItem(99, "포니언 스프", "주식", 300.0)]   # 주식 후보가 스프뿐
    res = solve(menus, staple_main=False)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert "포니언 스프" in picked(res)


def test_soup_cannot_fill_main_slot():
    """H-4b — 스프는 주식 슬롯에 들어가지 못한다."""
    res = solve(pool(), staple_main=True)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert not ({"포니언 스프", "치킨완자스프"} & set(picked(res)))


def test_infeasible_when_only_non_staple_mains():
    """주식 후보가 전부 비주식이면 조용히 넘어가지 않고 해 없음으로 끝난다."""
    menus = [m for m in pool() if m.category != "주식"]
    menus += [MenuItem(99, "포니언 스프", "주식", 300.0)]
    res = solve(menus, staple_main=True)
    assert res.status == "INFEASIBLE"


# ---------------------------------------------------------------------------
# H-4c 메뉴 궁합
# ---------------------------------------------------------------------------
def test_noodle_with_stew_possible_without_constraint():
    """음성 대조 — 제약이 없으면 국수 + 부대찌개 조합이 나올 수 있다."""
    menus = [m for m in pool(with_rice=False) if m.name not in ("포니언 스프", "치킨완자스프")]
    menus = [m for m in menus if m.category != "국"] + [
        MenuItem(50, "삼계부대찌개", "국", 100.0)]          # 국 후보가 찌개뿐
    res = solve(menus, pairing=False)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert "삼계부대찌개" in picked(res)


def test_stew_not_paired_with_noodle():
    """H-4c — 면류 주식과 찌개가 같은 끼니에 오지 않는다."""
    res = solve(pool(), staple_main=True, pairing=True)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    names = set(picked(res))
    noodles = {"함초 냉이 국수", "블랙빈 곤약국수"}
    stews = {"삼계부대찌개", "맑은부대찌개"}
    assert not (names & noodles and names & stews)


def test_stew_forces_rice_when_only_stew_soup():
    """국 후보가 찌개뿐이면 주식은 반드시 밥류가 된다."""
    menus = [m for m in pool() if m.category != "국"]
    menus += [MenuItem(50, "삼계부대찌개", "국", 100.0)]
    res = solve(menus, staple_main=True, pairing=True)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    names = set(picked(res))
    assert "삼계부대찌개" in names
    assert names & {"잡곡밥", "파인애플볶음밥"}


def test_infeasible_when_stew_only_and_no_rice():
    """찌개만 있고 밥이 없으면 해가 없다 — 규칙을 우회하지 않는다."""
    menus = [m for m in pool(with_rice=False) if m.category != "국"]
    menus += [MenuItem(50, "삼계부대찌개", "국", 100.0)]
    res = solve(menus, staple_main=True, pairing=True)
    assert res.status == "INFEASIBLE"


# ---------------------------------------------------------------------------
# 리포트 · 기본값
# ---------------------------------------------------------------------------
def test_pairing_report_is_clean():
    """리포트가 위반 0건을 보고한다."""
    res = solve(pool(), staple_main=True, pairing=True)
    pr = res.hard_breakdown["pairing"]
    assert pr["meals_without_staple"] == []
    assert pr["incompatible_pairs"] == []
    assert pr["all_ok"] is True


def test_default_config_does_not_activate():
    """기본 설정에서는 두 제약이 꺼져 있어 기존 호출부 동작이 그대로다."""
    res = solve(pool())
    assert res.hard_breakdown["active_terms"]["staple_main"] is False
    assert res.hard_breakdown["active_terms"]["menu_pairing"] is False
    assert res.hard_breakdown["pairing"] == {}
