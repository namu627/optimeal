# -*- coding: utf-8 -*-
"""식단가 이월(carryover) 검증 — 총예산 Hard + 끼니 밴드·연속일 균형 Soft + 대체식 total 모드.

DB/네트워크 없이 mock 메뉴로 CP-SAT 동작을 확인한다.
실행: pytest module_3/tests/test_budget_carryover.py -v
"""
import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import alternative_menu as am              # noqa: E402
import budget_carryover as bc              # noqa: E402
import csp_hard_constraints as hc          # noqa: E402
from csp_solver import MealPlanRequest, MenuItem, build_and_solve  # noqa: E402


def mk(menu_id, name, cost, category="국", kcal=150.0, allergens=()):
    return MenuItem(menu_id=menu_id, name=name, category=category, calories=kcal,
                    cost_won=cost, allergens=set(allergens))


def _hard(per_day, period, *, repeat_window=0, include=()):
    # 예산 외 제약(열량 밴드·메뉴 중복 창·식단 구조)을 꺼서 예산 효과만 본다.
    return hc.HardConstraintConfig(budget_limit_per_person=per_day, budget_period=period,
                                   enable_energy=False, menu_repeat_window_days=repeat_window,
                                   include_menu_ids=set(include))


def _req(days, hard, carryover=None, **kw):
    return MealPlanRequest(days=days, meals=("점심",), composition={"국": 1}, hard=hard,
                           carryover=carryover, warm_start=False, solver_time_limit=10.0, **kw)


# ── (a) 총예산 Hard ─────────────────────────────────────────────────────
def test_total_budget_cannot_be_exceeded():
    """B=1,000원 × 1끼 × 2일 = 2,000원인데 메뉴가 3,000원뿐 → 이월로도 불가."""
    menus = [mk(1, "비싼국", 3000.0)]
    res = build_and_solve(menus, _req(2, _hard(1000.0, "total"), bc.CarryoverConfig(1000.0)))
    assert res.status == "INFEASIBLE"


def test_hard_breakdown_reports_total_budget():
    menus = [mk(1, "국", 900.0)]
    res = build_and_solve(menus, _req(2, _hard(1000.0, "total"), bc.CarryoverConfig(1000.0)))
    bt = res.hard_breakdown["budget_total"]
    assert bt == {"total": 1800, "limit": 2000, "headroom": 200, "ok": True}
    assert res.carryover_breakdown["total_headroom"] == 200


# ── 이월: B 를 넘는 끼니 허용 ─────────────────────────────────────────────
def test_carryover_allows_meal_over_budget_within_total():
    """특식(1,500원)을 반드시 한 번 내야 할 때: 하루 상한 1,000원이면 불가, 이월이면 가능."""
    menus = [mk(1, "특식국", 1500.0), mk(2, "평식국", 500.0)]
    day = build_and_solve(menus, _req(2, _hard(1000.0, "day", include={1})))
    assert day.status == "INFEASIBLE"

    carry = build_and_solve(menus, _req(2, _hard(1000.0, "total", include={1}), bc.CarryoverConfig(1000.0)))
    assert carry.status in ("OPTIMAL", "FEASIBLE")
    cb = carry.carryover_breakdown
    assert cb["meals_over_budget"] >= 1            # B 초과 끼니 발생(이월)
    assert cb["total_cost"] <= 2000               # 기간 총액은 지킴
    assert carry.hard_breakdown["budget_total"]["ok"] is True


# ── (b) 끼니 밴드 Soft ───────────────────────────────────────────────────
def test_band_floor_pulls_meal_cost_up():
    """300원(밴드 하한 800원 미만) vs 1,000원(밴드 안). 끄면 싼 쪽, 켜면 밴드 안 쪽을 고른다."""
    menus = [mk(1, "싼국", 300.0), mk(2, "적정국", 1000.0)]
    off = build_and_solve(menus, _req(1, _hard(2000.0, "total")))
    assert off.plan[1]["점심"] == ["싼국"]
    on = build_and_solve(menus, _req(1, _hard(2000.0, "total"), bc.CarryoverConfig(1000.0)))
    assert on.plan[1]["점심"] == ["적정국"]
    assert on.carryover_breakdown["meals_below_band"] == 0


def test_band_violation_is_penalized():
    """밴드 상한(1,200원)을 넘는 끼니만 있으면 초과분만큼 감점 단위가 잡힌다."""
    menus = [mk(1, "비싼국", 2000.0)]
    res = build_and_solve(menus, _req(1, _hard(5000.0, "total"), bc.CarryoverConfig(1000.0)))
    cb = res.carryover_breakdown
    assert cb["meals_above_band"] == 1
    assert cb["penalty_units"]["band"] == 8        # (2000-1200)/100


# ── (c) 연속일 원가 차 Soft ──────────────────────────────────────────────
def test_consecutive_cost_gap_is_penalized():
    """400·1,000·1,600원 세 메뉴를 3일에 한 번씩(중복 창 3일). 균형 항이 1,000원을 가운데에 둔다."""
    menus = [mk(1, "싼국", 400.0), mk(2, "중간국", 1000.0), mk(3, "비싼국", 1600.0)]
    hard = _hard(10000.0, "total", repeat_window=3)
    on = build_and_solve(menus, _req(3, hard, bc.CarryoverConfig(1000.0, w_band=0)))
    cb = on.carryover_breakdown
    assert on.plan[2]["점심"] == ["중간국"]
    assert cb["max_consecutive_cost_diff"] == 600
    assert cb["penalty_units"]["smooth_cost"] == 8   # 두 번 모두 (600-200)/100 — 허용폭 250원→2단위


def test_consecutive_sodium_gap_is_penalized():
    """나트륨만 다른 두 메뉴: 균형 항이 켜지면 연속일 나트륨 차 초과분이 감점 단위로 잡힌다."""
    menus = [mk(1, "짠국", 1000.0), mk(2, "싱건국", 1000.0)]
    hard = _hard(10000.0, "total", repeat_window=2)
    cfg = bc.CarryoverConfig(1000.0, sodium_max_per_day=2000.0)
    res = build_and_solve(menus, _req(2, hard, cfg, hard_nutrient_by_idx={"sodium": {0: 1800.0, 1: 200.0}}))
    cb = res.carryover_breakdown
    assert cb["active_terms"]["smooth_sodium"] is True
    assert cb["max_consecutive_sodium_diff"] == 1600
    assert cb["penalty_units"]["smooth_sodium"] == 11   # (1600-500)/100


# ── 옵션을 끄면 기존과 동일 ──────────────────────────────────────────────
def test_off_by_default_and_unchanged():
    assert MealPlanRequest().carryover is None
    menus = [mk(1, "싼국", 300.0), mk(2, "적정국", 1000.0), mk(3, "비싼국", 1600.0)]
    req = _req(3, _hard(3500.0, "day"), budget_floor_won=900.0)
    a, b = build_and_solve(menus, req), build_and_solve(menus, req)
    assert a.carryover_breakdown is None
    assert a.plan == b.plan and a.objective == b.objective
    # 하루 하한(budget_floor_won)은 carryover 가 없을 때만 쓰인다
    assert a.soft_breakdown["budget_floor"] is not None


def test_carryover_replaces_day_floor():
    menus = [mk(1, "적정국", 1000.0)]
    res = build_and_solve(menus, _req(1, _hard(2000.0, "total"), bc.CarryoverConfig(1000.0),
                                      budget_floor_won=900.0))
    assert res.soft_breakdown["budget_floor"] is None


# ── (e) 대체식: total 모드 기간 총액 ─────────────────────────────────────
def test_alternative_respects_total_budget():
    """교체 후 기간 총액(1,300+200=1,500)이 총예산(700×2=1,400)을 넘으면 교체하지 않는다."""
    # 둘째 날 접시는 다른 카테고리(주식)라 국 자리 대체 후보는 '비싼대체국' 하나뿐이다.
    menus = [mk(1, "우유국", 1000.0, allergens={"우유"}), mk(2, "밥", 200.0, category="주식"),
             mk(3, "비싼대체국", 1300.0)]
    plan = {1: {"점심": ["우유국"]}, 2: {"점심": ["밥"]}}
    grp = am.AllergyGroup("우유", {"우유"})

    total = am.derive_alternative_menus(plan, menus, [grp], ingredient_substitution=False,
                                        hard_config=_hard(700.0, "total"))[0]
    assert total.unresolved and not total.substitutions

    day = am.derive_alternative_menus(plan, menus, [grp], ingredient_substitution=False,
                                      hard_config=_hard(1400.0, "day"))[0]
    assert day.substitutions and day.substitutions[0].alternative == "비싼대체국"


# ── (a') 끼니 원가 울타리 Hard ────────────────────────────────────────────
def test_meal_guard_keeps_every_meal_inside():
    """울타리 0.7~1.3B(700~1,300원). 밴드 Soft 를 꺼도(w_band=0) 싼 300원·비싼 2,000원은 못 고른다."""
    menus = [mk(1, "싼국", 300.0), mk(2, "적정국", 1000.0), mk(3, "비싼국", 2000.0)]
    hard = _hard(10000.0, "total", repeat_window=0)
    free = build_and_solve(menus, _req(3, hard, bc.CarryoverConfig(1000.0, w_band=0)))
    assert free.carryover_breakdown["min_meal_cost"] == 300          # 울타리 없으면 싼 쪽으로 쏠림
    fenced = build_and_solve(menus, _req(3, hard, bc.CarryoverConfig(1000.0, w_band=0, guard_low=0.7, guard_high=1.3)))
    cb = fenced.carryover_breakdown
    assert cb["guard"] == {"min_won": 700, "max_won": 1300, "meals_outside": 0}
    assert all(700 <= m["cost"] <= 1300 for m in cb["meal_costs"])
    assert cb["active_terms"]["meal_guard"] is True


def test_meal_guard_infeasible_when_no_menu_fits():
    menus = [mk(1, "싼국", 300.0), mk(2, "비싼국", 2000.0)]
    res = build_and_solve(menus, _req(1, _hard(10000.0, "total"), bc.CarryoverConfig(1000.0, guard_low=0.7, guard_high=1.3)))
    assert res.status == "INFEASIBLE"


def test_meal_guard_none_is_unchanged_model():
    """울타리 None(기본)이면 제약이 없다 — 결과가 같고, 걸리지 않는 넓은 울타리와도 같은 해·목적값."""
    cfg = bc.CarryoverConfig(1000.0)
    assert cfg.guard_low is None and cfg.guard_high is None and bc.guard_bounds_won(cfg) is None
    menus = [mk(1, "싼국", 300.0), mk(2, "적정국", 1000.0), mk(3, "비싼국", 1600.0)]
    hard = _hard(10000.0, "total", repeat_window=3)
    a = build_and_solve(menus, _req(3, hard, cfg))
    wide = build_and_solve(menus, _req(3, hard, bc.CarryoverConfig(1000.0, guard_low=0.0, guard_high=100.0)))
    assert a.carryover_breakdown["guard"] is None and a.carryover_breakdown["active_terms"]["meal_guard"] is False
    assert a.plan == wide.plan and a.objective == wide.objective
