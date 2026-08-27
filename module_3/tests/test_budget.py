# -*- coding: utf-8 -*-
"""module_3 식단가(예산) Hard/Soft 검증 테스트.

DB/네트워크 없이 mock 메뉴(테스트 더블)로 CP-SAT 동작을 확인한다.
배치 위치: module_3/tests/test_budget.py
실행:     pytest module_3/tests/test_budget.py -v

구성:
  · Hard 상한 (현행 코드 대상) ................ 지금 바로 PASS
  · Soft 저가 가산 (현행 코드 대상) ........... 지금 바로 PASS
  · Soft 하한 (PATCH 적용 후 대상) ............ 패치 전에는 자동 skip
"""
import os
import sys

import pytest
from ortools.sat.python import cp_model

# module_3/src 를 import 경로에 추가 (test_soft_constraints.py와 동일 규약)
SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import csp_hard_constraints as hc       # noqa: E402
import soft_constraints as sc           # noqa: E402
from csp_solver import MenuItem         # noqa: E402


def mk(menu_id, name, category, cost=200.0, kcal=150.0):
    return MenuItem(menu_id=menu_id, name=name, category=category,
                    calories=kcal, cost_won=cost)


def _make_x(model, menus, days, n_meals):
    """결정변수 x(i,d,s)만 만든다(구성 제약 없이 순수 배선 테스트용)."""
    M, D, S = range(len(menus)), range(days), range(n_meals)
    return {(m, d, s): model.NewBoolVar(f"x_{m}_{d}_{s}") for m in M for d in D for s in S}


# ===========================================================================
# Hard 상한 — "일정 가격 넘으면 생성 불가" (현행 코드)
# ===========================================================================
def test_hard_budget_day_cap_excludes_over_budget():
    """하루 상한 2000원. 한 끼에 하나만 고르되, 3000원 메뉴만 있으면 INFEASIBLE."""
    menus = [mk(1, "비싼국", "국", cost=3000.0)]
    model = cp_model.CpModel()
    days, n_meals = 1, 1
    x = _make_x(model, menus, days, n_meals)
    # 그 끼니에 반드시 1개 편성(수요 강제) → 상한이 진짜 막는지 본다
    model.Add(sum(x[0, 0, s] for s in range(n_meals)) == 1)

    # enable_energy=False, menu_repeat_window_days=0 로 예산 외 제약을 끈다
    # (기본값: 열량밴드 ON·중복창 3일 → 미설정 시 예산이 아닌 이유로 INFEASIBLE).
    cfg = hc.HardConstraintConfig(budget_limit_per_person=2000.0, budget_period="day",
                                  enable_energy=False, menu_repeat_window_days=0)
    hc.add_hard_constraints(model, x, menus, days=days, n_meals=n_meals, config=cfg)

    status = cp_model.CpSolver().Solve(model)
    assert status == cp_model.INFEASIBLE   # 3000 > 2000 → 생성 불가


def test_hard_budget_day_cap_allows_within_budget():
    """같은 상한에서 1500원 메뉴는 편성 가능."""
    menus = [mk(1, "적정국", "국", cost=1500.0)]
    model = cp_model.CpModel()
    x = _make_x(model, menus, 1, 1)
    model.Add(x[0, 0, 0] == 1)

    cfg = hc.HardConstraintConfig(budget_limit_per_person=2000.0, budget_period="day",
                                  enable_energy=False, menu_repeat_window_days=0)
    hc.add_hard_constraints(model, x, menus, days=1, n_meals=1, config=cfg)

    status = cp_model.CpSolver().Solve(model)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)


def test_hard_budget_total_period():
    """total 주기: 2일 상한 = 1000×2일=2000원. 하루 1500씩 이틀=3000 → 초과로 불가."""
    menus = [mk(1, "국", "국", cost=1500.0)]
    model = cp_model.CpModel()
    days = 2
    x = _make_x(model, menus, days, 1)
    for d in range(days):
        model.Add(x[0, d, 0] == 1)

    cfg = hc.HardConstraintConfig(budget_limit_per_person=1000.0, budget_period="total",
                                  enable_energy=False, menu_repeat_window_days=0)
    hc.add_hard_constraints(model, x, menus, days=days, n_meals=1, config=cfg)

    status = cp_model.CpSolver().Solve(model)
    assert status == cp_model.INFEASIBLE


# ===========================================================================
# Soft 저가 가산 — "낮을수록 가산점" (현행 코드)
# ===========================================================================
def test_soft_cost_prefers_cheaper():
    """싼 메뉴/비싼 메뉴 중 하나만 고를 때, 목적함수 최대화는 싼 쪽을 택한다."""
    menus = [mk(1, "싼반찬", "부찬", cost=100.0),
             mk(2, "비싼반찬", "부찬", cost=900.0)]
    model = cp_model.CpModel()
    x = _make_x(model, menus, 1, 1)
    # 둘 중 정확히 하나 편성
    model.Add(x[0, 0, 0] + x[1, 0, 0] == 1)

    soft = sc.add_soft_objective(model, x, menus, days=1, n_meals=1)
    model.Maximize(soft.score)

    solver = cp_model.CpSolver()
    solver.Solve(model)
    assert solver.Value(x[0, 0, 0]) == 1   # 싼 메뉴 선택
    assert solver.Value(x[1, 0, 0]) == 0


# ===========================================================================
# Soft 하한 — "너무 낮으면 감점" (PATCH 적용 후 대상)
# ===========================================================================
_HAS_FLOOR = "w_floor" in getattr(sc.SoftWeights, "__dataclass_fields__", {})


@pytest.mark.skipif(not _HAS_FLOOR, reason="budget_floor 패치 미적용 — soft_constraints 패치 후 활성화")
def test_soft_floor_penalizes_too_cheap():
    """적정선 500원. 100원(너무 쌈) vs 500원(적정) 중 하나만 고를 때, 적정 쪽을 택한다.

    저가 가산(w_cost)만 있으면 100원이 이기지만, 하한 감점(w_floor)이 이를 뒤집어야 한다.
    """
    menus = [mk(1, "너무싼반찬", "부찬", cost=100.0),
             mk(2, "적정반찬", "부찬", cost=500.0)]
    model = cp_model.CpModel()
    x = _make_x(model, menus, 1, 1)
    model.Add(x[0, 0, 0] + x[1, 0, 0] == 1)

    soft = sc.add_soft_objective(model, x, menus, days=1, n_meals=1,
                                 budget_floor_won=500.0)
    model.Maximize(soft.score)

    solver = cp_model.CpSolver()
    solver.Solve(model)
    assert solver.Value(x[1, 0, 0]) == 1   # 적정선 메뉴 선택(최저가 쏠림 방지)


@pytest.mark.skipif(not _HAS_FLOOR, reason="budget_floor 패치 미적용")
def test_soft_floor_reports_shortfall_zero_when_ok():
    """적정선을 넘기면 부족분(shortfall)은 0이어야 한다."""
    menus = [mk(1, "적정반찬", "부찬", cost=600.0)]
    model = cp_model.CpModel()
    x = _make_x(model, menus, 1, 1)
    model.Add(x[0, 0, 0] == 1)

    soft = sc.add_soft_objective(model, x, menus, days=1, n_meals=1,
                                 budget_floor_won=500.0)
    model.Maximize(soft.score)
    solver = cp_model.CpSolver()
    solver.Solve(model)

    rep = sc.evaluate_breakdown(solver, x, menus, soft, days=1, n_meals=1)
    assert rep["budget_floor"][0]["floor_ok"] is True


@pytest.mark.skipif(not _HAS_FLOOR, reason="budget_floor 패치 미적용")
def test_soft_floor_reports_exact_shortfall_below_rounding():
    """정확값(원)은 하한 미달이나 100원 단위론 충족되는 경계 케이스.

    260+238=498원(하한 500 대비 2원 미달)이지만, 100원 단위 반올림 합은
    3+2=5units(=500)라 솔버 판정(floor_ok)은 충족으로 나온다. 리포트의
    정확값 필드(floor_ok_exact·shortfall_won)가 이 2원 미달을 잡아내야 한다.
    """
    menus = [mk(1, "반찬A", "부찬", cost=260.0),
             mk(2, "반찬B", "부찬", cost=238.0)]
    model = cp_model.CpModel()
    x = _make_x(model, menus, 1, 2)
    model.Add(x[0, 0, 0] == 1)
    model.Add(x[1, 0, 1] == 1)
    model.Add(x[0, 0, 1] == 0)
    model.Add(x[1, 0, 0] == 0)

    soft = sc.add_soft_objective(model, x, menus, days=1, n_meals=2,
                                 budget_floor_won=500.0)
    model.Maximize(soft.score)
    solver = cp_model.CpSolver()
    solver.Solve(model)

    rep = sc.evaluate_breakdown(solver, x, menus, soft, days=1, n_meals=2)
    row = rep["budget_floor"][0]
    assert row["cost"] == 498               # 정확 합계(원)
    assert row["floor_ok"] is True          # 100원 단위 판정: 충족
    assert row["floor_ok_exact"] is False   # 정확값 판정: 미달
    assert row["shortfall_won"] == 2        # 정확값 미달분(원)
