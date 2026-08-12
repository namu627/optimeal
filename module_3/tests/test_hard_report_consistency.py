# -*- coding: utf-8 -*-
"""Hard 제약 리포트 ↔ 실제 제약 정합성 회귀 테스트 (2026-08-10 절단 버그).

버그: 제약은 `int(kcal * SCALE)`(소수 2자리)로 걸리는데 리포트는 접시별 `int(kcal)`로
      절단해 하루 접시 수만큼(3끼×4접시 → 최대 ~12kcal) 과소 집계했다.
      그 결과 **제약을 만족한 해가 `kcal_ok=False`("칼로리 위반")로 표시**됐다.
      영양사 검수 자료에 그대로 노출되는 오보라 회귀 테스트로 고정한다.

DB 없이 mock 메뉴로 검증한다(solver 로직 검증용 픽스처, 학습 데이터 아님).
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import csp_hard_constraints as hc          # noqa: E402
from csp_solver import MealPlanRequest, MenuItem, build_and_solve  # noqa: E402


def fractional_menus():
    """소수점 칼로리 후보 — 실 DB `nutrition_recipe.calories`는 DECIMAL 이다.

    모든 후보의 소수부를 .9 로 맞춰 두었다. 접시별 `int()` 절단이 재발하면 하루
    18접시(6접시 × 3끼니)에서 정확히 16.2kcal 이 사라지므로, 아래 밴드 설정과
    맞물려 '정상인데 위반' 오보가 재현된다.
    """
    spec = (("밥", "주식", 320.9, 4, 300.4), ("국", "국", 90.9, 4, 200.4),
            ("주찬", "주찬", 180.9, 6, 250.4), ("부찬", "부찬", 60.9, 12, 150.4),
            ("김치", "김치", 10.9, 6, 50.4))
    out, mid = [], 1
    for label, cat, base, count, cost in spec:
        for i in range(count):
            out.append(MenuItem(mid, f"{label}{i}", cat, base + i, cost_won=cost))
            mid += 1
    return out


def exact_daily_kcal(plan, menus):
    """plan 의 하루 총 kcal 을 float 로 정확히 재계산한다."""
    by = {m.name: m for m in menus}
    return {d: sum(by[n].calories for ms in v.values() for n in ms) for d, v in plan.items()}


@pytest.fixture()
def solved():
    """하한이 '리포트 절단값'과 '실제값' 사이에 오도록 밴드를 좁힌 해.

    수정 전에는 이 설정에서 status=OPTIMAL 인데 kcal_ok=False 가 나왔다.
    밴드 2177.4~2192.6 은 최소 achievable(2179.2) 바로 위에 하한을 두고, 절단값
    (실제−16.2)은 하한 아래로 떨어지도록 잡은 값이다.
    """
    menus = fractional_menus()
    cfg = hc.HardConstraintConfig(target_kcal_per_day=2185.0, kcal_tolerance=0.0035,
                                  budget_limit_per_person=None,
                                  # 이 테스트의 관심사는 리포트 절단뿐 → 이후 추가된
                                  # 끼니배분·중복창 제약은 비활성으로 격리한다.
                                  enable_meal_ratio=False, menu_repeat_window_days=0)
    res = build_and_solve(menus, MealPlanRequest(days=2, hard=cfg, solver_time_limit=30.0))
    assert res.status in ("OPTIMAL", "FEASIBLE"), f"해가 있어야 한다: {res.status}"
    return menus, cfg, res


def test_report_kcal_equals_exact_sum(solved):
    """리포트 kcal 이 float 실측 합과 일치한다(접시별 절단 없음)."""
    menus, _, res = solved
    exact = exact_daily_kcal(res.plan, menus)
    for day, kcal in exact.items():
        assert res.daily_kcal[day] == pytest.approx(kcal, abs=0.05), (
            f"{day}일 리포트 {res.daily_kcal[day]} ≠ 실측 {kcal} — 접시별 절단 재발"
        )


def test_kcal_ok_flag_matches_reality(solved):
    """제약을 만족한 해를 리포트가 '위반'으로 오보하지 않는다."""
    menus, cfg, res = solved
    lo = cfg.target_kcal_per_day * (1 - cfg.kcal_tolerance)
    hi = cfg.target_kcal_per_day * (1 + cfg.kcal_tolerance)
    exact = exact_daily_kcal(res.plan, menus)

    for info in res.hard_breakdown["per_day"]:
        truth = lo <= exact[info["day"]] <= hi
        assert truth, "솔버가 밴드를 지켰어야 한다(제약식 자체 회귀)"
        assert info["kcal_ok"] is True, (
            f"{info['day']}일: 실제 {exact[info['day']]}kcal 은 밴드({lo:.1f}, {hi:.1f}) 안인데 "
            f"리포트가 위반으로 표시 — 절단 버그 재발"
        )


def test_cost_report_not_truncated_per_dish():
    """원가도 접시별 절단하지 않는다(예산 판정 오보 방지)."""
    menus = fractional_menus()
    # 하루 최소 원가는 3,307.2원(끼니당 1,102.4 × 3끼니) — 커트라인은 그 위에 둔다.
    cfg = hc.HardConstraintConfig(enable_energy=False, budget_limit_per_person=3400.0,
                                  budget_period="day",
                                  enable_meal_ratio=False, menu_repeat_window_days=0)
    res = build_and_solve(menus, MealPlanRequest(days=2, hard=cfg, solver_time_limit=30.0))
    assert res.plan, f"해가 있어야 한다: {res.status}"
    by = {m.name: m for m in menus}
    for info in res.hard_breakdown["per_day"]:
        exact = sum(by[n].cost_won for n in
                    [x for ms in res.plan[info["day"]].values() for x in ms])
        assert info["cost"] == pytest.approx(round(exact), abs=1)
        assert info["budget_ok"] is (exact <= cfg.budget_limit_per_person)
